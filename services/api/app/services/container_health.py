"""Phase 4b of the topology-graph feature: container-level health in the
Living Model (see docs/architecture/adr-0012-container-health.md).

**The gap this closes.** The twin knew a service was down only through
OpenStack's own API report (topology_sync.py, `Service.openstack_state`) and
a host-level `up{job="node_exporter"}` (prometheus_health.py). On a Kolla
deployment every service is a Docker container, and that left two blind spots:

1. Containers that are not an OpenStack API service at all -- `rabbitmq`,
   `mariadb`, `keystone`, `haproxy`, `memcached`, `nova_libvirt` -- had no
   representation in the graph, so one of them going down raised nothing, even
   though losing the message bus or the database takes the whole control plane
   with it.
2. Containers that *are* an OpenStack service were only seen once Nova/
   Neutron/Cinder's own heartbeat timed out (about a minute later), and never
   at all for a container that is up-but-unhealthy or crash-looping.

**Telemetry.** Each node writes `cortex_container_*` metrics through
node_exporter's textfile collector (ansible role `container_metrics`, one
`docker ps -a` per interval -- no new exporter, no new port, no new Prometheus
job). They arrive in the same `node_exporter` scrape, already labelled with
the `node` hostname by prometheus_sd, which is what joins them to a `:Node`:

    cortex_container_up{container="rabbitmq",state="exited",health="none"} 0
    cortex_container_restart_count{container="rabbitmq"} 3
    cortex_containers_scrape_timestamp_seconds 1.7e9

**Graph.** `(:Container {id: "name@host"})-[:RUNS_ON]->(:Node)` and, when the
container is the runtime of an OpenStack service on that node,
`(:Service)-[:RUNS_IN]->(:Container)` (Kolla names the container after the
binary: `nova_compute` <-> `nova-compute`). prometheus_health.py then folds
the container's state into `Service.state` (see reconcile_service_state).

**Honesty rules**, because a twin that guesses is worse than one that says
"unknown":

- Stale data is `unknown`, never carried forward as `running`: if the node's
  `cortex_containers_scrape_timestamp_seconds` is older than
  STALE_AFTER_SECONDS (the collector timer died, but node_exporter is still
  answering) every container on it goes `unknown` and raises no alert.
- A container Cortex has seen before that is absent from a *fresh* scrape is
  `missing` (it was removed, e.g. `docker rm`) -- a critical state, kept for
  MISSING_RETENTION_DAYS so an intentional removal ages out instead of
  alerting forever.
- A node with no series at all is the host-level `Node.health` problem, not
  forty container alerts: its containers are `unknown`.
"""
import logging
import os
import time
from typing import Optional

from .. import graph_db
from .prometheus_client import query

logger = logging.getLogger(__name__)

CONTAINER_UP_QUERY = "cortex_container_up"
CONTAINER_RESTARTS_QUERY = "cortex_container_restart_count"
SCRAPE_TIMESTAMP_QUERY = "cortex_containers_scrape_timestamp_seconds"

# ~9 collector intervals at the default 20s timer. Env-overridable: how stale
# is "stale" depends on the timer the ansible role was deployed with.
STALE_AFTER_SECONDS = float(os.environ.get("CONTAINER_STALE_AFTER_SECONDS", "180"))
MISSING_RETENTION_DAYS = int(os.environ.get("CONTAINER_MISSING_RETENTION_DAYS", "7"))

# Normalized container states written to :Container.state.
RUNNING = "running"
UNHEALTHY = "unhealthy"  # running, but its own Docker healthcheck fails
RESTARTING = "restarting"  # crash-looping
DOWN = "down"  # exited / dead / created / paused -- not serving
MISSING = "missing"  # previously seen, now absent from a fresh scrape
UNKNOWN = "unknown"  # no fresh telemetry -- no opinion, no alert

# States that mean "this container is not doing its job".
PROBLEM_STATES = {UNHEALTHY, RESTARTING, DOWN, MISSING}
# Of those, the ones that take the owning OpenStack service down with them
# (`unhealthy` still serves requests, just badly -- it is alerted on its own).
SERVICE_DOWN_STATES = {RESTARTING, DOWN, MISSING}

_DOCKER_DOWN = {"exited", "dead", "created", "paused", "removing"}


def normalize_state(docker_state: Optional[str], health: Optional[str] = None) -> str:
    """Docker's `State.Status` (+ `Health.Status` when the image defines a
    healthcheck, as Kolla's do) -> one of this module's states."""
    state = (docker_state or "").lower()
    if state == "running":
        return UNHEALTHY if (health or "").lower() == "unhealthy" else RUNNING
    if state == "restarting":
        return RESTARTING
    if state in _DOCKER_DOWN:
        return DOWN
    return UNKNOWN


def container_binary(container: str) -> str:
    """Kolla container name -> OpenStack service binary:
    `nova_compute` -> `nova-compute`, `neutron_l3_agent` -> `neutron-l3-agent`."""
    return container.replace("_", "-")


def container_id(container: str, host: str) -> str:
    return f"{container}@{host}"


# --------------------------------------------------------------------
# Reading Prometheus
# --------------------------------------------------------------------

def fetch_observations() -> dict[str, dict]:
    """{hostname: {"scraped_at": float | None, "containers": {name: {...}}}}
    for every node that has any `cortex_container_*` series. Raises whatever
    prometheus_client.query() raises -- the caller decides what an
    unreachable Prometheus means (skip the pass, leave the graph as it was)."""
    observations: dict[str, dict] = {}

    def host_entry(sample: dict) -> Optional[dict]:
        host = sample.get("metric", {}).get("node")
        if not host:
            return None  # series that didn't come through file_sd: nothing to join it to a :Node on
        return observations.setdefault(host, {"scraped_at": None, "containers": {}})

    for sample in query(CONTAINER_UP_QUERY):
        entry = host_entry(sample)
        name = sample.get("metric", {}).get("container")
        if entry is None or not name:
            continue
        metric = sample["metric"]
        entry["containers"][name] = {
            "state": normalize_state(metric.get("state"), metric.get("health")),
            "docker_state": metric.get("state"),
            "restart_count": 0,
        }

    for sample in query(CONTAINER_RESTARTS_QUERY):
        entry = host_entry(sample)
        name = sample.get("metric", {}).get("container")
        if entry is not None and name in entry["containers"]:
            try:
                entry["containers"][name]["restart_count"] = int(float(sample["value"][1]))
            except (KeyError, IndexError, TypeError, ValueError):
                pass

    for sample in query(SCRAPE_TIMESTAMP_QUERY):
        entry = host_entry(sample)
        if entry is not None:
            try:
                entry["scraped_at"] = float(sample["value"][1])
            except (KeyError, IndexError, TypeError, ValueError):
                pass

    return observations


# --------------------------------------------------------------------
# Pure decision logic (unit-tested without Prometheus or Neo4j)
# --------------------------------------------------------------------

def build_container_rows(
    observations: dict[str, dict],
    known: dict[str, set[str]],
    now: Optional[float] = None,
    stale_after: float = STALE_AFTER_SECONDS,
) -> list[dict]:
    """One row per (host, container) the graph should hold this pass.

    `known` is {hostname: {container names}} the graph already has, which is
    what lets a container that vanished be reported as `missing` rather than
    silently forgotten. See the module docstring for the staleness rules."""
    now = time.time() if now is None else now
    rows: list[dict] = []
    seen: set[tuple[str, str]] = set()

    for host, obs in observations.items():
        scraped_at = obs.get("scraped_at")
        fresh = scraped_at is not None and (now - scraped_at) <= stale_after
        for name, c in obs["containers"].items():
            seen.add((host, name))
            rows.append({
                "id": container_id(name, host), "name": name, "host": host,
                "binary": container_binary(name),
                "state": c["state"] if fresh else UNKNOWN,
                "docker_state": c.get("docker_state"),
                "restart_count": c.get("restart_count", 0),
            })
        for name in known.get(host, set()):
            if (host, name) not in seen:
                rows.append({
                    "id": container_id(name, host), "name": name, "host": host,
                    "binary": container_binary(name),
                    "state": MISSING if fresh else UNKNOWN,
                    "docker_state": None, "restart_count": 0,
                })

    # Hosts with known containers but no series at all: the node-level
    # problem is Node.health's to report, so they go unknown, not missing.
    for host, names in known.items():
        if host in observations:
            continue
        for name in names:
            rows.append({
                "id": container_id(name, host), "name": name, "host": host,
                "binary": container_binary(name), "state": UNKNOWN,
                "docker_state": None, "restart_count": 0,
            })
    return rows


def alert_rows(rows: list[dict], service_ids_by_container: dict[str, str]) -> list[dict]:
    """The `{"service_id", "state"}` pairs prometheus_health's
    `_sync_service_state_anomalies` turns into AnomalyFlag/AnomalyEvent rows.

    A container that implements an OpenStack service is already covered by
    that service's reconciled `state` (a down container makes it "down"), so
    it only gets an alert of its own for the one thing the service state
    can't express -- `unhealthy`. Every other container (rabbitmq, mariadb,
    ...) is alerted on directly. Rows are emitted for healthy containers too
    ("up") so an open alert resolves when the container recovers; `unknown`
    emits nothing, leaving any open alert exactly as it was."""
    out = []
    for row in rows:
        state = row["state"]
        if state == UNKNOWN:
            continue
        mapped = row["id"] in service_ids_by_container
        if mapped:
            alert_state = UNHEALTHY if state == UNHEALTHY else "up"
        elif state in PROBLEM_STATES:
            alert_state = state if state == UNHEALTHY else "down"
        else:
            alert_state = "up"
        out.append({"service_id": row["id"], "state": alert_state})
    return out


# --------------------------------------------------------------------
# Graph writes
# --------------------------------------------------------------------

def _known_containers(session) -> dict[str, set[str]]:
    known: dict[str, set[str]] = {}
    for record in session.run("MATCH (k:Container) RETURN k.host AS host, k.name AS name"):
        if record["host"] and record["name"]:
            known.setdefault(record["host"], set()).add(record["name"])
    return known


def _sync_containers_to_graph(session, rows: list[dict]) -> None:
    session.run(
        """
        UNWIND $rows AS c
        MERGE (k:Container {id: c.id})
        SET k.name = c.name,
            k.host = c.host,
            k.binary = c.binary,
            k.state = c.state,
            k.docker_state = c.docker_state,
            k.restart_count = c.restart_count,
            k.missing_since = CASE
                WHEN c.state = 'missing' THEN coalesce(k.missing_since, datetime())
                ELSE null END,
            k.observed_at = datetime()
        WITH k, c
        MATCH (n:Node {id: c.host})
        MERGE (k)-[:RUNS_ON]->(n)
        """,
        rows=rows,
    )
    # A Service is "implemented by" the container of the same binary on the
    # same node. Idempotent MERGE; a Service that disappears takes its edge
    # with it when topology_sync's sweep removes the vertex.
    session.run(
        """
        MATCH (k:Container)-[:RUNS_ON]->(n:Node)
        MATCH (s:Service)-[:RUNS_ON]->(n)
        WHERE s.binary = k.binary
        MERGE (s)-[:RUNS_IN]->(k)
        """
    )
    # Containers whose node left the graph, and `missing` ones past retention.
    session.run("MATCH (k:Container) WHERE NOT (k)-[:RUNS_ON]->(:Node) DETACH DELETE k")
    session.run(
        "MATCH (k:Container {state: 'missing'}) "
        "WHERE k.missing_since < datetime() - duration({days: $days}) DETACH DELETE k",
        days=MISSING_RETENTION_DAYS,
    )


def _service_ids_by_container(session) -> dict[str, str]:
    return {
        record["container_id"]: record["service_id"]
        for record in session.run(
            "MATCH (s:Service)-[:RUNS_IN]->(k:Container) RETURN k.id AS container_id, s.id AS service_id"
        )
    }


def sync_container_health() -> dict:
    """One pass: Prometheus -> :Container vertices -> (returned) alert rows.

    Never raises: an unreachable Prometheus or graph skips the pass and leaves
    the graph exactly as the last good pass wrote it (same contract as
    prometheus_health.sync_prometheus_health). Returns
    `{"queried": bool, "containers": int, "problems": int, "alert_rows": [...]}`;
    the caller folds `alert_rows` into the service-state alert pass."""
    try:
        observations = fetch_observations()
    except Exception:
        logger.exception("container health sync: failed to query Prometheus, skipping this pass")
        return {"queried": False, "containers": 0, "problems": 0, "alert_rows": []}

    try:
        with graph_db.driver.session() as session:
            rows = build_container_rows(observations, _known_containers(session))
            if rows:
                _sync_containers_to_graph(session, rows)
            alerts = alert_rows(rows, _service_ids_by_container(session))
    except Exception:
        logger.exception("container health sync: graph write failed, skipping this pass")
        return {"queried": False, "containers": 0, "problems": 0, "alert_rows": []}

    problems = sum(1 for r in rows if r["state"] in PROBLEM_STATES)
    logger.info("container health sync: %d container(s) tracked, %d in a problem state", len(rows), problems)
    return {"queried": True, "containers": len(rows), "problems": problems, "alert_rows": alerts}
