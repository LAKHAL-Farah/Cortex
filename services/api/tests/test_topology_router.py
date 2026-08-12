"""Tests for routers/topology.py (Phase 5 -- API).

The graph endpoints (/graph, /nodes/{id}, /services, /networks) are read
paths over graph_db.py's Neo4j queries; Neo4j is faked the same way
test_topology_sync.py/test_prometheus_health.py fake it -- a small
in-memory result set per query shape, matched on a distinctive substring
of the Cypher text, rather than a real Neo4j instance (there isn't one in
CI -- see .github/workflows/ci.yml).

/health is backed by Postgres only (the topology_sync_runs table), so
those tests seed it directly via SessionLocal, same pattern as
test_baselines_router.py.
"""
import uuid
from datetime import datetime, timedelta

from fastapi.testclient import TestClient

from app import graph_db, models
from app.db import SessionLocal
from app.main import app
from app.routers import topology as topology_router

client = TestClient(app)


# --------------------------------------------------------------- fake graph --

class _FakeResult:
    def __init__(self, records: list[dict]):
        self._records = records

    def __iter__(self):
        return iter(self._records)

    def single(self):
        return self._records[0] if self._records else None


class _FakeSession:
    """Answers the handful of read-only Cypher shapes graph_db.py's Phase 5
    functions issue, matched on a distinctive substring of the query text.
    Backed by one small, fixed sample graph: a hypervisor Node
    ("compute1-sim") a Service ("nova-compute@compute1-sim") RUNS_ON, and a
    Network ("net-1") with one Subnet ("sub-1") CONNECTS-ing to it.
    """

    def run(self, query, **kwargs):
        if "RETURN properties(n).id AS id" in query:
            return _FakeResult([
                {"id": "compute1-sim", "label": "Node", "properties": {"id": "compute1-sim", "role": "compute"}},
                {"id": "nova-compute@compute1-sim", "label": "Service", "properties": {"id": "nova-compute@compute1-sim", "binary": "nova-compute"}},
            ])
        if "MATCH (a)-[r]->(b)" in query:
            return _FakeResult([
                {"source": "nova-compute@compute1-sim", "target": "compute1-sim", "type": "RUNS_ON"},
            ])
        if "WHERE n.id = $vertex_id" in query:
            vertex_id = kwargs["vertex_id"]
            if vertex_id != "compute1-sim":
                return _FakeResult([])
            return _FakeResult([{
                "properties": {"id": "compute1-sim", "role": "compute"},
                "label": "Node",
                "outgoing": [],
                "incoming": [
                    {"id": "nova-compute@compute1-sim", "label": "Service", "relationship": "RUNS_ON", "direction": "incoming"},
                ],
            }])
        if "MATCH (s:Service)" in query:
            return _FakeResult([
                {"service": {"id": "nova-compute@compute1-sim", "binary": "nova-compute", "openstack_state": "up"}, "node_id": "compute1-sim"},
            ])
        if "MATCH (net:Network)" in query:
            return _FakeResult([
                {
                    "network": {"id": "net-1", "name": "sandbox-net", "status": "ACTIVE"},
                    "subnets": [{"id": "sub-1", "cidr": "10.0.1.0/24"}],
                    "gateway_routers": [],
                    "floating_ips": [],
                    "serving_agents": [],
                },
            ])
        raise AssertionError(f"unexpected query in fake session: {query}")

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _FakeDriver:
    def session(self):
        return _FakeSession()


def _use_fake_graph(monkeypatch):
    monkeypatch.setattr(graph_db, "driver", _FakeDriver())


# --------------------------------------------------------------------- /graph --

def test_get_graph_returns_flattened_nodes_and_edges(monkeypatch):
    _use_fake_graph(monkeypatch)

    body = client.get("/api/v1/topology/graph").json()

    assert {n["id"] for n in body["nodes"]} == {"compute1-sim", "nova-compute@compute1-sim"}
    assert body["edges"] == [{"source": "nova-compute@compute1-sim", "target": "compute1-sim", "type": "RUNS_ON"}]


# ------------------------------------------------------------------- /nodes --

def test_get_vertex_detail_returns_properties_and_neighbors(monkeypatch):
    _use_fake_graph(monkeypatch)

    res = client.get("/api/v1/topology/nodes/compute1-sim")
    assert res.status_code == 200
    body = res.json()
    assert body["label"] == "Node"
    assert body["properties"]["role"] == "compute"
    assert body["neighbors"] == [
        {"id": "nova-compute@compute1-sim", "label": "Service", "relationship": "RUNS_ON", "direction": "incoming"},
    ]


def test_get_vertex_detail_404s_for_unknown_id(monkeypatch):
    _use_fake_graph(monkeypatch)

    res = client.get("/api/v1/topology/nodes/no-such-vertex")
    assert res.status_code == 404


# ---------------------------------------------------------------- /services --

def test_list_services_includes_node_id_and_openstack_state(monkeypatch):
    _use_fake_graph(monkeypatch)

    body = client.get("/api/v1/topology/services").json()
    assert len(body) == 1
    assert body[0]["id"] == "nova-compute@compute1-sim"
    assert body[0]["node_id"] == "compute1-sim"
    assert body[0]["openstack_state"] == "up"


# ---------------------------------------------------------------- /networks --

def test_list_networks_nests_subnets(monkeypatch):
    _use_fake_graph(monkeypatch)

    body = client.get("/api/v1/topology/networks").json()
    assert len(body) == 1
    assert body[0]["id"] == "net-1"
    assert body[0]["subnets"] == [{"id": "sub-1", "cidr": "10.0.1.0/24"}]
    assert body[0]["gateway_routers"] == []


# ------------------------------------------------------------------- /health --

def _seed_run(sync_type, status, minutes_ago=0, summary=None, error=None):
    db = SessionLocal()
    try:
        finished = datetime.utcnow() - timedelta(minutes=minutes_ago)
        row = models.TopologySyncRun(
            id=uuid.uuid4(),
            sync_type=sync_type,
            status=status,
            summary=summary,
            error=error,
            started_at=finished - timedelta(seconds=5),
            finished_at=finished,
        )
        db.add(row)
        db.commit()
    finally:
        db.close()


def _clear_sync_runs():
    # Unlike hostname/metric_name-keyed tables elsewhere (see test_nodes.py,
    # test_baselines_router.py), sync_type only ever takes two fixed values
    # ("openstack", "prometheus_health") -- there's no per-test-unique key
    # to sidestep collisions with rows left behind by a previous run against
    # the same (non-ephemeral, locally-reused) Postgres. Clearing the table
    # up front makes these tests deterministic regardless of what ran
    # before, rather than relying on the DB being empty.
    db = SessionLocal()
    try:
        db.query(models.TopologySyncRun).delete()
        db.commit()
    finally:
        db.close()


def test_health_reports_unknown_when_no_runs_recorded_for_a_sync_type():
    _clear_sync_runs()

    # A sync_type that has never run (e.g. right after a fresh deploy)
    # comes back as an explicit `null` entry + "unknown" contributing to
    # the overall status, not a 404 or a silently-omitted key.
    body = client.get("/api/v1/topology/health").json()
    assert body["syncs"]["openstack"] is None
    assert body["syncs"]["prometheus_health"] is None
    assert body["status"] == "unknown"


def test_health_status_is_ok_when_latest_runs_are_ok():
    _clear_sync_runs()
    _seed_run("openstack", "ok", minutes_ago=1)
    _seed_run("prometheus_health", "ok", minutes_ago=1)

    body = client.get("/api/v1/topology/health").json()
    assert body["status"] == "ok"
    assert body["syncs"]["openstack"]["status"] == "ok"
    assert body["syncs"]["prometheus_health"]["status"] == "ok"


def test_health_uses_most_recent_run_not_an_earlier_one():
    _clear_sync_runs()
    _seed_run("openstack", "failed", minutes_ago=10)
    _seed_run("openstack", "ok", minutes_ago=1)
    _seed_run("prometheus_health", "ok", minutes_ago=1)

    body = client.get("/api/v1/topology/health").json()
    assert body["syncs"]["openstack"]["status"] == "ok"


def test_health_status_reflects_worst_of_the_two_sync_types():
    _clear_sync_runs()
    _seed_run("openstack", "ok", minutes_ago=1)
    _seed_run("prometheus_health", "failed", minutes_ago=1, error="prometheus unreachable")

    body = client.get("/api/v1/topology/health").json()
    assert body["status"] == "failed"
    assert body["syncs"]["prometheus_health"]["error"] == "prometheus unreachable"


# ------------------------------------------------------------------- /resync --
# Backs the "Reconverge" control in the topology top bar (TopologyView.tsx).
# sync_topology() itself talks to OpenStack, so it's monkeypatched here the
# same way test_topology_sync.py fakes it at the boundary rather than
# standing up a real cloud.

def test_resync_records_an_ok_run_and_returns_it(monkeypatch):
    _clear_sync_runs()
    monkeypatch.setattr(
        topology_router,
        "sync_topology",
        lambda db: {"complete_picture": True, "network_topology_ok": True, "hypervisors": 2},
    )

    res = client.post("/api/v1/topology/resync")
    assert res.status_code == 200
    body = res.json()
    assert body["sync_type"] == "openstack"
    assert body["status"] == "ok"
    assert body["summary"]["hypervisors"] == 2

    # Recorded exactly like a scheduled pass -- /health picks it straight up.
    health = client.get("/api/v1/topology/health").json()
    assert health["syncs"]["openstack"]["status"] == "ok"


def test_resync_records_degraded_when_summary_reports_a_partial_picture(monkeypatch):
    _clear_sync_runs()
    monkeypatch.setattr(
        topology_router,
        "sync_topology",
        lambda db: {"complete_picture": False, "network_topology_ok": True},
    )

    body = client.post("/api/v1/topology/resync").json()
    assert body["status"] == "degraded"


def test_resync_records_failed_and_still_returns_200_when_the_pass_raises(monkeypatch):
    _clear_sync_runs()

    def _boom(db):
        raise RuntimeError("openstack unreachable")

    monkeypatch.setattr(topology_router, "sync_topology", _boom)

    res = client.post("/api/v1/topology/resync")
    # The API request to *trigger* a resync succeeded -- it's the sync pass
    # itself that failed, which is surfaced in the body, not as an HTTP
    # error (mirrors main.py's periodic loop: a failed pass is recorded,
    # not raised).
    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "failed"
    assert "openstack unreachable" in body["error"]
