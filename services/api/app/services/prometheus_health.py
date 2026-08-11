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

3. Each reconciled `:Service.state` also gets turned into a real
   `AnomalyFlag`/`AnomalyEvent` row in Postgres via
   _sync_service_state_anomalies(), the same upsert + open-episode
   pattern anomaly_detector.detect_anomalies() uses for cpu_usage/
   ram_usage. Before this, step 2 above updated the graph and stopped --
   nothing ever created a row for GET /api/v1/anomalies or the Alerts
   page to show, so a service actually going down produced no alert at
   all. Only "down" and "unreachable" are treated as anomalous; see
   SERVICE_STATE_SEVERITY below for why "disabled"/unknown are not.
"""
import logging
from datetime import datetime

from .. import graph_db, models
from .prometheus_client import query

logger = logging.getLogger(__name__)

UP_QUERY = 'up{job="node_exporter"}'

# Severity assigned to each reconciled Service.state that represents a
# real problem. Deliberately NOT reusing anomaly_detector.THRESHOLDS/
# severity_from_zscore -- those convert a continuous z-score into a
# severity band, but a service's state is categorical (there's no
# "sigma" for "down"), so this is just a direct lookup instead.
#
# "disabled" is left out on purpose: that's an admin action (e.g. `nova
# service-disable`), not a failure, so it shouldn't raise an alert.
# A state of None/"unknown" (reconcile_service_state() falls through to
# None when openstack_state itself is None) means neither side has an
# opinion yet -- also not alerted, same reasoning as "unknown" node
# health elsewhere in this module.
SERVICE_STATE_SEVERITY = {
    "down": "critical",         # OpenStack itself reports the service stopped
    "unreachable": "critical",  # OpenStack says up, but its host is confirmed down
}

# Mirrors anomaly_detector._SEVERITY_RANK (severity only ratchets up while
# an AnomalyEvent stays open). Kept as its own copy rather than importing
# it -- this module intentionally has no dependency on anomaly_detector.py,
# see the module docstring: the two run on independent schedules against
# independent data sources.
_SEVERITY_RANK = {"normal": 0, "medium": 1, "high": 2, "critical": 3}

# metric_name / method values written onto the AnomalyFlag/AnomalyEvent
# rows this module creates. "service_state" is what alert_correlation.py
# and the web UI (lib/anomalies.ts::isServiceStateMetric) already expect
# for a state-check-derived alert, as opposed to a scored metric.
SERVICE_STATE_METRIC_NAME = "service_state"
SERVICE_STATE_METHOD = "service_state_check"


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


def _sync_service_state_to_graph(session) -> list[dict]:
    """Recompute every :Service's reconciled `state` from its own
    `openstack_state` and the `health` of the :Node it RUNS_ON (just set
    by _sync_node_health_to_graph earlier in the same pass, so this
    always reconciles against the freshest health). Mirrors
    reconcile_service_state() above -- keep the two in sync. A Service
    with no RUNS_ON edge (shouldn't happen; topology_sync always creates
    one) reconciles against 'unknown' via the OPTIONAL MATCH + coalesce,
    rather than this silently doing nothing for it.

    Returns the resulting {"service_id": ..., "state": ...} pairs from
    this exact pass so the caller can turn them into Postgres alerts
    (_sync_service_state_anomalies) without a second graph round-trip.
    """
    result = session.run(
        """
        MATCH (s:Service)
        OPTIONAL MATCH (s)-[:RUNS_ON]->(n:Node)
        WITH s, coalesce(n.health, 'unknown') AS node_health
        SET s.state = CASE
            WHEN s.openstack_state <> 'up' THEN s.openstack_state
            WHEN node_health = 'down' THEN 'unreachable'
            ELSE 'up'
        END
        RETURN s.id AS service_id, s.state AS state
        """
    )
    return [{"service_id": row["service_id"], "state": row["state"]} for row in result]


def _sync_service_state_anomalies(db, service_states: list[dict]) -> None:
    """Turn this pass's reconciled Service.state values into
    AnomalyFlag/AnomalyEvent rows in Postgres -- same upsert-the-current-
    flag + open/ratchet/resolve-the-episode pattern
    anomaly_detector.detect_anomalies() uses for cpu_usage/ram_usage, so
    a service actually going down shows up as a real alert instead of
    only ever updating the graph.

    current_value/z_score don't mean anything for a categorical state --
    there's no percentage, no sigma -- so they're set to the same
    1.0/0.0 placeholders the web UI already treats as "not a real
    statistical read" for metric_name == "service_state" (see
    lib/anomalies.ts::isServiceStateMetric).

    `db` is a Postgres (SQLAlchemy) session, unlike `session` elsewhere
    in this module which is a Neo4j session -- kept as a separate
    function/parameter rather than threading Postgres through the graph
    helpers above so those stay focused on the graph alone.
    """
    now = datetime.utcnow()

    for row in service_states:
        service_id = row.get("service_id")
        if not service_id:
            # A Service vertex with no `id` shouldn't happen (topology_sync
            # always sets one) -- skip rather than write a Postgres row
            # keyed on nothing.
            continue

        severity = SERVICE_STATE_SEVERITY.get(row.get("state"), "normal")

        existing = (
            db.query(models.AnomalyFlag)
            .filter_by(hostname=service_id, metric_name=SERVICE_STATE_METRIC_NAME)
            .first()
        )
        if existing:
            existing.current_value = 1.0
            existing.z_score = 0.0
            existing.severity = severity
            existing.method = SERVICE_STATE_METHOD
            existing.baseline_n = None
            existing.detected_at = now
        else:
            db.add(models.AnomalyFlag(
                hostname=service_id, metric_name=SERVICE_STATE_METRIC_NAME,
                current_value=1.0, z_score=0.0,
                severity=severity, method=SERVICE_STATE_METHOD,
                baseline_n=None, detected_at=now,
            ))

        # History: same one-open-episode-per-(hostname, metric_name)
        # bookkeeping as anomaly_detector.py, so a past service-down
        # episode remains visible on Alerts > History after it resolves.
        open_event = (
            db.query(models.AnomalyEvent)
            .filter_by(hostname=service_id, metric_name=SERVICE_STATE_METRIC_NAME, resolved_at=None)
            .first()
        )
        if severity != "normal":
            if open_event is None:
                db.add(models.AnomalyEvent(
                    hostname=service_id, metric_name=SERVICE_STATE_METRIC_NAME,
                    current_value=1.0, z_score=0.0,
                    severity=severity, method=SERVICE_STATE_METHOD,
                    baseline_n=None, started_at=now,
                ))
            elif _SEVERITY_RANK[severity] >= _SEVERITY_RANK[open_event.severity]:
                open_event.current_value = 1.0
                open_event.z_score = 0.0
                open_event.severity = severity
                open_event.method = SERVICE_STATE_METHOD
        elif open_event is not None:
            open_event.resolved_at = now

    db.commit()


def sync_prometheus_health(db=None) -> dict:
    """One full Phase-4 pass: pull `up{job="node_exporter"}`, overlay it
    onto every :Node as `health`, recompute every :Service's reconciled
    `state` from that, then (when a `db` session is given) turn those
    reconciled states into AnomalyFlag/AnomalyEvent rows in Postgres via
    _sync_service_state_anomalies() -- so a service actually going down
    produces a real alert, not just an updated graph property. `db` is
    optional (default None) so callers that only care about the graph
    side (e.g. existing tests) can still call this without a Postgres
    session; the alerting step is simply skipped, with a warning logged,
    when it's omitted. main.py's periodic loop always passes a real one.

    If the Prometheus query itself fails (Prometheus unreachable), this
    pass is skipped entirely -- existing `health`/`state` values (and any
    open AnomalyFlag/AnomalyEvent rows) are left exactly as the last
    successful pass wrote them rather than being overwritten with
    guesses, and the next scheduled pass will try again.
    """
    try:
        health = fetch_node_health()
    except Exception:
        logger.exception("prometheus health sync: failed to query Prometheus, skipping this pass")
        return {"queried": False, "nodes_up": 0, "nodes_down": 0}

    with graph_db.driver.session() as session:
        _sync_node_health_to_graph(session, health)
        service_states = _sync_service_state_to_graph(session)

    if db is not None:
        try:
            _sync_service_state_anomalies(db, service_states)
        except Exception:
            # Best-effort, same reasoning as crud.record_topology_sync_run's
            # own try/except in main.py: a Postgres hiccup here shouldn't
            # undo the graph writes this pass already made, and the next
            # tick will just try the Postgres side again from fresh state.
            logger.exception("prometheus health sync: failed to sync service-state alerts to Postgres")
    else:
        logger.warning("prometheus health sync: no db session provided, skipping service-state alerting")

    nodes_up = sum(1 for state in health.values() if state == "up")
    nodes_down = sum(1 for state in health.values() if state == "down")
    logger.info(
        "prometheus health sync: %d node(s) up, %d node(s) down (per Prometheus)",
        nodes_up, nodes_down,
    )
    return {"queried": True, "nodes_up": nodes_up, "nodes_down": nodes_down}
