"""Phase 4 of the topology-graph feature: cross-check the graph against
Prometheus-observed liveness (see docs/architecture/adr-0003-prometheus-
cross-check.md for the actual decisions -- this module is just the
implementation of it).

Two things happen here, on their own schedule (see main.py), independently
of topology_sync's OpenStack poll:

1. Every `:Node` vertex gets an explicit `health` property straight from
   `up{job="node_exporter"}` -- "up"/"down" when Prometheus has an opinion,
   "unknown" when it doesn't. Matched on the `node` label
   prometheus_sd.py already writes into every file_sd target -- the same
   hostname topology_sync.py uses as a Node vertex's `id` -- not
   `instance` (ip:port), which has nothing to join against on the Node
   side.

2. Every `:Service` vertex gets a reconciled `state`, computed from its
   own OpenStack-reported `openstack_state` (topology_sync.py writes this;
   Phase 2/3 used to write it as `state` directly, see adr-0003 decision 2
   for why that's now split in two) and the `health` of the `:Node` it
   `RUNS_ON`. See reconcile_service_state() for the actual precedence --
   it's expressed once there in Python and mirrored in the Cypher CASE in
   _sync_service_state_to_graph so the two can't drift apart silently; if
   you change one, change the other (test_prometheus_health.py checks
   both against the same table).
"""
import logging

from .. import graph_db
from .prometheus_client import query

logger = logging.getLogger(__name__)

UP_QUERY = 'up{job="node_exporter"}'


def fetch_node_health() -> dict[str, str]:
    """{hostname: "up"|"down"} for every node_exporter target Prometheus
    currently has an opinion on this tick. Hostnames Prometheus has no
    series for at all are simply absent from the returned dict -- the
    caller (_sync_node_health_to_graph) is what turns "absent" into an
    explicit "unknown" for every Node in the graph, not this function.

    Raises whatever prometheus_client.query() raises (e.g. Prometheus
    unreachable); sync_prometheus_health() decides what that means for
    the graph rather than this function swallowing it.
    """
    health: dict[str, str] = {}
    for item in query(UP_QUERY):
        hostname = item.get("metric", {}).get("node")
        if not hostname:
            # up{} without a `node` label didn't come through file_sd (or
            # file_sd hasn't picked up a labels change yet) -- nothing to
            # join it to a Node vertex with, so skip rather than guess.
            continue
        value = item.get("value", [None, None])[1]
        health[hostname] = "up" if value == "1" else "down"
    return health


def reconcile_service_state(openstack_state: str | None, node_health: str | None) -> str | None:
    """The cross-check decision itself -- see adr-0003 decision 2 for the
    full table and reasoning. In short: OpenStack's own "down" always
    wins (it's a more specific signal than host-level `up`); OpenStack
    "up" against a confirmed-"down" host is flagged as "unreachable"
    rather than trusting either side outright; anything else (including
    "up" against an "unknown" host) passes `openstack_state` through
    unchanged.
    """
    if openstack_state != "up":
        return openstack_state
    if node_health == "down":
        return "unreachable"
    return "up"


def _sync_node_health_to_graph(session, health: dict[str, str]) -> None:
    """Every :Node currently in the graph gets an explicit health value
    this pass -- "up"/"down" from `health`, "unknown" for any Node id
    `health` has no opinion on -- rather than leaving whatever value a
    previous pass wrote in place, which would look like a live reading
    long after Prometheus stopped reporting on that host (see adr-0003
    decision 1: "unknown" has to be reachable, not just "down" by another
    name).
    """
    session.run(
        """
        MATCH (n:Node)
        WITH n, CASE
            WHEN n.id IN $up_ids THEN 'up'
            WHEN n.id IN $down_ids THEN 'down'
            ELSE 'unknown'
        END AS h
        SET n.health = h, n.health_checked_at = datetime()
        """,
        up_ids=[hostname for hostname, state in health.items() if state == "up"],
        down_ids=[hostname for hostname, state in health.items() if state == "down"],
    )


def _sync_service_state_to_graph(session) -> None:
    """Recompute every :Service's reconciled `state` from its own
    `openstack_state` and the `health` of the :Node it RUNS_ON (just set
    by _sync_node_health_to_graph earlier in the same pass, so this
    always reconciles against the freshest health). Mirrors
    reconcile_service_state() above -- keep the two in sync. A Service
    with no RUNS_ON edge (shouldn't happen; topology_sync always creates
    one) reconciles against 'unknown' via the OPTIONAL MATCH + coalesce,
    rather than this silently doing nothing for it.
    """
    session.run(
        """
        MATCH (s:Service)
        OPTIONAL MATCH (s)-[:RUNS_ON]->(n:Node)
        WITH s, coalesce(n.health, 'unknown') AS node_health
        SET s.state = CASE
            WHEN s.openstack_state <> 'up' THEN s.openstack_state
            WHEN node_health = 'down' THEN 'unreachable'
            ELSE 'up'
        END
        """
    )


def sync_prometheus_health(db=None) -> dict:
    """One full Phase-4 pass: pull `up{job="node_exporter"}`, overlay it
    onto every :Node as `health`, then recompute every :Service's
    reconciled `state` from that. `db` is accepted (and ignored) only so
    this fits main.py's _run_periodic(fn, interval, name) signature the
    same way every other periodic job does -- unlike topology_sync, this
    never touches Postgres or OpenStack, only Prometheus and Neo4j.

    If the Prometheus query itself fails (Prometheus unreachable), this
    pass is skipped entirely -- existing `health`/`state` values are left
    exactly as the last successful pass wrote them rather than being
    overwritten with guesses, and the next scheduled pass will try again.
    """
    try:
        health = fetch_node_health()
    except Exception:
        logger.exception("prometheus health sync: failed to query Prometheus, skipping this pass")
        return {"queried": False, "nodes_up": 0, "nodes_down": 0}

    with graph_db.driver.session() as session:
        _sync_node_health_to_graph(session, health)
        _sync_service_state_to_graph(session)

    nodes_up = sum(1 for state in health.values() if state == "up")
    nodes_down = sum(1 for state in health.values() if state == "down")
    logger.info(
        "prometheus health sync: %d node(s) up, %d node(s) down (per Prometheus)",
        nodes_up, nodes_down,
    )
    return {"queried": True, "nodes_up": nodes_up, "nodes_down": nodes_down}
