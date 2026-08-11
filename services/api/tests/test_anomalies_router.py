"""Tests for routers/anomalies.py's Phase 6 addition, GET /incidents.

Postgres is real (via SessionLocal), same pattern test_topology_router.py
uses for its /health tests -- AnomalyFlag rows are seeded directly and
the table is cleared up front so leftover rows from a previous run
against the same (non-ephemeral, locally-reused) Postgres can't change
the grouping. Neo4j is faked the same way test_topology_router.py fakes
it for its graph-backed endpoints: a small fixed sample graph, matched on
a distinctive substring of the Cypher text.

GET /api/v1/anomalies and /history are untouched by this phase (see the
action plan doc, section 3.2) and already have their own tests elsewhere
in this file's sibling modules -- these tests only cover the new
/incidents endpoint.
"""
from datetime import datetime, timedelta

from fastapi.testclient import TestClient

from app import graph_db, models
from app.db import SessionLocal
from app.main import app

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
    """One fixed sample graph: a compute Node ("compute-02") and the Nova
    Service that RUNS_ON it -- exactly the demo script's scenario (action
    plan doc, section 4)."""

    _VERTICES = {
        "compute-02": {"label": "Node", "properties": {"id": "compute-02", "role": "compute"}},
        "nova-compute@compute-02": {
            "label": "Service",
            "properties": {"id": "nova-compute@compute-02", "binary": "nova-compute", "state": "unreachable"},
        },
    }
    _REACHABLE = {
        "compute-02": {"nova-compute@compute-02"},
        "nova-compute@compute-02": {"compute-02"},
    }

    def run(self, query, **kwargs):
        if "RETURN v.id AS id, labels(v)[0] AS label" in query:
            return _FakeResult([
                {"id": vid, **self._VERTICES[vid]} for vid in kwargs["ids"] if vid in self._VERTICES
            ])
        if "MATCH (start)-[:RUNS_ON|SERVES|CONNECTS*1..2]-(other)" in query:
            anchor_id = kwargs["anchor_id"]
            candidates = set(kwargs["candidate_ids"])
            reachable = self._REACHABLE.get(anchor_id, set())
            return _FakeResult([{"id": rid} for rid in reachable if rid in candidates])
        if "shortestPath(" in query:
            return _FakeResult([{
                "path_nodes": [
                    {"id": "nova-compute@compute-02", "label": "Service"},
                    {"id": "compute-02", "label": "Node"},
                ],
                "path_edges": [{"type": "RUNS_ON", "source": "nova-compute@compute-02", "target": "compute-02"}],
            }])
        raise AssertionError(f"unexpected query in fake session: {query}")

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _FakeDriver:
    def session(self):
        return _FakeSession()


class _UnreachableDriver:
    def session(self):
        from neo4j.exceptions import ServiceUnavailable
        raise ServiceUnavailable("no connection to Neo4j")


# ---------------------------------------------------------------------- db --

def _clear_open_alerts():
    # Same rationale as test_topology_router.py's _clear_sync_runs(): this
    # endpoint reads every open alert with no hostname filter, so leftover
    # rows from a previous run against the same Postgres would otherwise
    # bleed into the grouping.
    db = SessionLocal()
    try:
        db.query(models.AnomalyFlag).delete()
        db.commit()
    finally:
        db.close()


def _seed_alert(hostname, metric_name, severity, minutes_ago=0, current_value=90.0, z_score=3.5, baseline_n=50):
    db = SessionLocal()
    try:
        db.add(models.AnomalyFlag(
            hostname=hostname,
            metric_name=metric_name,
            current_value=current_value,
            z_score=z_score,
            severity=severity,
            method="robust_zscore",
            baseline_n=baseline_n,
            detected_at=datetime.utcnow() - timedelta(minutes=minutes_ago),
        ))
        db.commit()
    finally:
        db.close()


# -------------------------------------------------------------------- tests --

def test_incidents_groups_correlated_alerts_via_runs_on(monkeypatch):
    monkeypatch.setattr(graph_db, "driver", _FakeDriver())
    _clear_open_alerts()
    _seed_alert("compute-02", "cpu_usage", "high", minutes_ago=5)
    _seed_alert("nova-compute@compute-02", "service_state", "critical", minutes_ago=2, current_value=1.0, z_score=0.0, baseline_n=1)

    body = client.get("/api/v1/anomalies/incidents").json()

    assert len(body) == 1
    incident = body[0]
    assert incident["member_count"] == 2
    assert incident["severity"] == "critical"
    assert incident["root_cause_guess"] == {"vertex_id": "nova-compute@compute-02", "label": "Service"}
    assert incident["narrative"] == (
        "compute-02 is under CPU pressure and its Nova compute service has gone unreachable."
    )
    assert set(incident["graph_path"]["vertex_ids"]) == {"compute-02", "nova-compute@compute-02"}


def test_incidents_leaves_unrelated_alert_standalone(monkeypatch):
    monkeypatch.setattr(graph_db, "driver", _FakeDriver())
    _clear_open_alerts()
    _seed_alert("compute-02", "cpu_usage", "high")
    _seed_alert("nova-compute@compute-02", "service_state", "critical", current_value=1.0, z_score=0.0, baseline_n=1)
    _seed_alert("storage-09", "ram_usage", "medium")

    body = client.get("/api/v1/anomalies/incidents").json()

    member_counts = sorted(i["member_count"] for i in body)
    assert member_counts == [1, 2]
    standalone = next(i for i in body if i["member_count"] == 1)
    assert standalone["members"][0]["hostname"] == "storage-09"


def test_incidents_falls_back_to_ungrouped_when_graph_unreachable(monkeypatch):
    monkeypatch.setattr(graph_db, "driver", _UnreachableDriver())
    _clear_open_alerts()
    _seed_alert("compute-02", "cpu_usage", "high")
    _seed_alert("nova-compute@compute-02", "service_state", "critical", current_value=1.0, z_score=0.0, baseline_n=1)

    response = client.get("/api/v1/anomalies/incidents")

    # Postgres alerting keeps working even though the graph is down --
    # a 200 with every alert as its own incident, not a 503.
    assert response.status_code == 200
    body = response.json()
    assert len(body) == 2
    assert all(i["member_count"] == 1 for i in body)
    assert all(i["graph_path"] is None for i in body)


def test_incidents_empty_when_no_open_alerts(monkeypatch):
    monkeypatch.setattr(graph_db, "driver", _FakeDriver())
    _clear_open_alerts()

    response = client.get("/api/v1/anomalies/incidents")

    assert response.status_code == 200
    assert response.json() == []
