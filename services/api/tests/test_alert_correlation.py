"""Tests for services/alert_correlation.py (Phase 6 -- graph-correlated
alerts).

Neo4j is faked the same way test_topology_sync.py/test_prometheus_health.py/
test_topology_router.py fake it, but here the fake also has to answer
"what's within 2 hops of this vertex" and "what's the shortest path
between these two vertices" instead of just returning a fixed row set --
so _FakeGraphStore does a small real BFS over a hand-built (source, type,
target) edge list rather than pattern-matching on Cypher substrings alone.
Postgres (AnomalyFlag rows) is a real in-memory SQLite DB, same fixture
pattern as test_anomaly_detector.py.
"""
from collections import defaultdict, deque
from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import models
from app.services import alert_correlation
from app.services.alert_correlation import build_incidents


# ------------------------------------------------------------- fake graph --

class _FakeGraphStore:
    """Vertices keyed by id (mirrors the graph's own `id` property being
    unique across every label), edges kept as the same (source, type,
    target) triples topology_sync.py's Cypher would have written --
    direction preserved in storage, but every lookup below treats them as
    undirected, same as alert_correlation.py's own Cypher does.
    """

    def __init__(self):
        self.vertices: dict[str, dict] = {}
        self.edges: list[tuple[str, str, str]] = []

    def add_vertex(self, vid: str, label: str, **props):
        self.vertices[vid] = {"label": label, "properties": {"id": vid, **props}}

    def add_edge(self, source: str, rel_type: str, target: str):
        self.edges.append((source, rel_type, target))

    def _undirected_adjacency(self):
        adj = defaultdict(list)
        for s, t, tgt in self.edges:
            adj[s].append((tgt, t))
            adj[tgt].append((s, t))
        return adj

    def reachable_within(self, start: str, max_hops: int) -> set[str]:
        adj = self._undirected_adjacency()
        visited = {start}
        frontier = [start]
        for _ in range(max_hops):
            nxt = []
            for u in frontier:
                for v, _t in adj[u]:
                    if v not in visited:
                        visited.add(v)
                        nxt.append(v)
            frontier = nxt
        visited.discard(start)
        return visited

    def shortest_path(self, a: str, b: str, max_hops: int):
        adj = self._undirected_adjacency()
        parent: dict[str, str | None] = {a: None}
        parent_edge: dict[str, str] = {}
        depth = {a: 0}
        queue = deque([a])
        while queue:
            u = queue.popleft()
            if u == b:
                break
            if depth[u] >= max_hops:
                continue
            for v, etype in adj[u]:
                if v not in parent:
                    parent[v] = u
                    parent_edge[v] = etype
                    depth[v] = depth[u] + 1
                    queue.append(v)
        if b not in parent:
            return [], []
        path_ids = []
        cur: str | None = b
        while cur is not None:
            path_ids.append(cur)
            cur = parent[cur]
        path_ids.reverse()
        nodes = [{"id": pid, "label": self.vertices[pid]["label"]} for pid in path_ids]
        edges = []
        cur = b
        while parent[cur] is not None:
            edges.append({"type": parent_edge[cur], "source": parent[cur], "target": cur})
            cur = parent[cur]
        edges.reverse()
        return nodes, edges


class _FakeResult:
    def __init__(self, records: list[dict]):
        self._records = records

    def __iter__(self):
        return iter(self._records)

    def single(self):
        return self._records[0] if self._records else None


class _FakeSession:
    def __init__(self, store: _FakeGraphStore):
        self.store = store

    def run(self, query, **kwargs):
        if "RETURN v.id AS id, labels(v)[0] AS label" in query:
            ids = kwargs["ids"]
            return _FakeResult([
                {"id": vid, "label": self.store.vertices[vid]["label"], "properties": self.store.vertices[vid]["properties"]}
                for vid in ids
                if vid in self.store.vertices
            ])
        if "MATCH (start)-[:RUNS_ON|SERVES|CONNECTS*1..2]-(other)" in query:
            anchor_id = kwargs["anchor_id"]
            candidates = set(kwargs["candidate_ids"])
            reachable = self.store.reachable_within(anchor_id, alert_correlation.MAX_HOPS)
            return _FakeResult([{"id": rid} for rid in reachable if rid in candidates])
        if "shortestPath(" in query:
            nodes, edges = self.store.shortest_path(kwargs["id1"], kwargs["id2"], alert_correlation.MAX_HOPS)
            if not nodes:
                return _FakeResult([])
            return _FakeResult([{"path_nodes": nodes, "path_edges": edges}])
        raise AssertionError(f"unexpected query in fake session: {query}")

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _FakeDriver:
    def __init__(self, store: _FakeGraphStore):
        self.store = store

    def session(self):
        return _FakeSession(self.store)


class _UnreachableDriver:
    """Stands in for Neo4j being down: opening a session raises, exactly
    like the neo4j driver does when it can't reach the server."""

    def session(self):
        from neo4j.exceptions import ServiceUnavailable
        raise ServiceUnavailable("no connection to Neo4j")


# ---------------------------------------------------------------------- db --

@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:")
    models.Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


def _flag(db, hostname, metric_name, severity="high", current_value=90.0, z_score=3.5,
          method="robust_zscore", baseline_n=50, minutes_ago=0):
    row = models.AnomalyFlag(
        hostname=hostname,
        metric_name=metric_name,
        current_value=current_value,
        z_score=z_score,
        severity=severity,
        method=method,
        baseline_n=baseline_n,
        detected_at=datetime.utcnow() - timedelta(minutes=minutes_ago),
    )
    db.add(row)
    db.commit()
    return row


# ------------------------------------------------------- same-host grouping --

def test_two_metrics_on_same_host_become_one_incident(db, monkeypatch):
    store = _FakeGraphStore()
    store.add_vertex("compute-02", "Node", role="compute")
    monkeypatch.setattr(alert_correlation.graph_db, "driver", _FakeDriver(store))

    _flag(db, "compute-02", "cpu_usage", severity="high", minutes_ago=5)
    _flag(db, "compute-02", "ram_usage", severity="medium", minutes_ago=1)

    incidents = build_incidents(db)

    assert len(incidents) == 1
    assert incidents[0]["member_count"] == 2
    assert incidents[0]["severity"] == "high"
    assert incidents[0]["root_cause_guess"] == {"vertex_id": "compute-02", "label": "Node"}
    assert incidents[0]["graph_path"] == {"vertex_ids": ["compute-02"], "edges": []}


# ------------------------------------------------------ RUNS_ON correlation --

def test_node_and_its_service_correlate_via_runs_on(db, monkeypatch):
    """The demo script's exact scenario (action plan doc, section 4):
    a compute node's CPU anomaly + its Nova service going unreachable ->
    one incident, not two."""
    store = _FakeGraphStore()
    store.add_vertex("compute-02", "Node", role="compute")
    store.add_vertex("nova-compute@compute-02", "Service", binary="nova-compute",
                      state="unreachable", openstack_state="up")
    store.add_edge("nova-compute@compute-02", "RUNS_ON", "compute-02")
    monkeypatch.setattr(alert_correlation.graph_db, "driver", _FakeDriver(store))

    _flag(db, "compute-02", "cpu_usage", severity="high", minutes_ago=5)
    _flag(db, "nova-compute@compute-02", "service_state", severity="critical",
          current_value=1.0, z_score=0.0, minutes_ago=2)

    incidents = build_incidents(db)

    assert len(incidents) == 1
    incident = incidents[0]
    assert incident["member_count"] == 2
    assert incident["severity"] == "critical"
    assert incident["root_cause_guess"] == {"vertex_id": "nova-compute@compute-02", "label": "Service"}
    assert incident["narrative"] == (
        "compute-02 is under CPU pressure and its Nova compute service has gone unreachable."
    )
    assert set(incident["graph_path"]["vertex_ids"]) == {"compute-02", "nova-compute@compute-02"}
    assert incident["graph_path"]["edges"] == [
        {"type": "RUNS_ON", "source": "nova-compute@compute-02", "target": "compute-02"}
    ]


def test_unrelated_alert_stays_its_own_incident(db, monkeypatch):
    """Negative check from the demo script: a third, unrelated anomaly
    (different host, no graph path within 2 hops) must NOT get folded
    into the compute-02/nova-compute incident."""
    store = _FakeGraphStore()
    store.add_vertex("compute-02", "Node", role="compute")
    store.add_vertex("nova-compute@compute-02", "Service", binary="nova-compute", state="unreachable")
    store.add_edge("nova-compute@compute-02", "RUNS_ON", "compute-02")
    store.add_vertex("storage-09", "Node", role="storage")
    monkeypatch.setattr(alert_correlation.graph_db, "driver", _FakeDriver(store))

    _flag(db, "compute-02", "cpu_usage", severity="high")
    _flag(db, "nova-compute@compute-02", "service_state", severity="critical", current_value=1.0, z_score=0.0)
    _flag(db, "storage-09", "cpu_usage", severity="medium")

    incidents = build_incidents(db)

    by_member_count = sorted(i["member_count"] for i in incidents)
    assert by_member_count == [1, 2]
    standalone = next(i for i in incidents if i["member_count"] == 1)
    assert standalone["members"][0]["hostname"] == "storage-09"
    assert standalone["root_cause_guess"] == {"vertex_id": "storage-09", "label": "Node"}


# -------------------------------------------------- 2-hop SERVES correlation --

def test_two_hosts_correlate_through_shared_network_two_hops(db, monkeypatch):
    """Tier 4 from the action plan doc: two different nodes' agent
    services both SERVES the same Network -- correlated at exactly the
    MAX_HOPS boundary, through a vertex (the Network) that never itself
    has an open alert."""
    store = _FakeGraphStore()
    store.add_vertex("compute-05", "Node", role="compute")
    store.add_vertex("compute-07", "Node", role="compute")
    store.add_vertex("net-1", "Network", name="sandbox-net", status="ACTIVE")
    store.add_vertex("neutron-dhcp-agent@compute-05", "Service", binary="neutron-dhcp-agent", state="unreachable")
    store.add_vertex("neutron-l3-agent@compute-07", "Service", binary="neutron-l3-agent", state="up")
    store.add_edge("neutron-dhcp-agent@compute-05", "RUNS_ON", "compute-05")
    store.add_edge("neutron-l3-agent@compute-07", "RUNS_ON", "compute-07")
    store.add_edge("neutron-dhcp-agent@compute-05", "SERVES", "net-1")
    store.add_edge("neutron-l3-agent@compute-07", "SERVES", "net-1")
    monkeypatch.setattr(alert_correlation.graph_db, "driver", _FakeDriver(store))

    _flag(db, "neutron-dhcp-agent@compute-05", "service_state", severity="high", current_value=1.0, z_score=0.0, minutes_ago=4)
    _flag(db, "neutron-l3-agent@compute-07", "service_state", severity="high", current_value=1.0, z_score=0.0, minutes_ago=1)

    incidents = build_incidents(db)

    assert len(incidents) == 1
    incident = incidents[0]
    assert incident["member_count"] == 2
    # Path goes through net-1 (2 hops) even though net-1 has no open
    # alert of its own -- it just has to be on the graph, not on the
    # open-alert list.
    assert "net-1" in incident["graph_path"]["vertex_ids"]


def test_three_hop_path_does_not_correlate(db, monkeypatch):
    """Past MAX_HOPS, two alerts stay separate -- "the point is to merge
    things the graph says are structurally related, not to invent
    correlations it doesn't support" (action plan doc, section 2)."""
    store = _FakeGraphStore()
    store.add_vertex("a", "Node")
    store.add_vertex("b", "Service", binary="svc-b")
    store.add_vertex("c", "Network", name="net-c")
    store.add_vertex("d", "Service", binary="svc-d")
    store.add_vertex("e", "Node")
    store.add_edge("b", "RUNS_ON", "a")
    store.add_edge("b", "SERVES", "c")
    store.add_edge("d", "SERVES", "c")
    store.add_edge("d", "RUNS_ON", "e")
    monkeypatch.setattr(alert_correlation.graph_db, "driver", _FakeDriver(store))

    _flag(db, "a", "cpu_usage", severity="high")
    _flag(db, "e", "cpu_usage", severity="high")

    incidents = build_incidents(db)

    # a -> b -> c -> d -> e is 4 hops; a and e themselves are 4 hops apart
    # (MAX_HOPS=2), so they must NOT merge.
    assert len(incidents) == 2
    assert sorted(i["member_count"] for i in incidents) == [1, 1]


# ---------------------------------------------------------- degraded fallback --

def test_falls_back_to_ungrouped_when_graph_unreachable(db, monkeypatch):
    monkeypatch.setattr(alert_correlation.graph_db, "driver", _UnreachableDriver())

    _flag(db, "compute-02", "cpu_usage", severity="high")
    _flag(db, "nova-compute@compute-02", "service_state", severity="critical", current_value=1.0, z_score=0.0)

    incidents = build_incidents(db)

    assert len(incidents) == 2
    assert all(i["member_count"] == 1 for i in incidents)
    assert all(i["graph_path"] is None for i in incidents)
    for incident in incidents:
        hostname = incident["members"][0]["hostname"]
        assert incident["root_cause_guess"] == {"vertex_id": hostname, "label": None}


def test_no_open_alerts_returns_empty_list(db, monkeypatch):
    store = _FakeGraphStore()
    monkeypatch.setattr(alert_correlation.graph_db, "driver", _FakeDriver(store))

    assert build_incidents(db) == []
