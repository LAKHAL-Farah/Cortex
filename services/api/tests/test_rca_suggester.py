"""Tests for services/rca_suggester.py.

Neo4j is faked the same fetch_vertex_detail shape test_topology_router.py
uses (a small fixed vertex -> {properties, label, outgoing, incoming}
map), since rca_suggester.py calls graph_db.fetch_vertex_detail directly
rather than issuing its own Cypher. Postgres (AnomalyFlag rows) is a real
in-memory SQLite DB, same fixture pattern as test_alert_correlation.py.
"""
from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import graph_db, models
from app.services.rca_suggester import find_causal_suggestions


# --------------------------------------------------------------- fake graph --

class _FakeResult:
    def __init__(self, records: list[dict]):
        self._records = records

    def single(self):
        return self._records[0] if self._records else None


class _FakeSession:
    """vertex_id -> {label, outgoing: [...], incoming: [...]} answering
    exactly the one Cypher shape fetch_vertex_detail issues
    (`WHERE n.id = $vertex_id`)."""

    def __init__(self, vertices: dict[str, dict]):
        self.vertices = vertices

    def run(self, query, **kwargs):
        assert "WHERE n.id = $vertex_id" in query, f"unexpected query: {query}"
        vertex_id = kwargs["vertex_id"]
        vertex = self.vertices.get(vertex_id)
        if vertex is None:
            return _FakeResult([])
        return _FakeResult([{
            "properties": {"id": vertex_id},
            "label": vertex["label"],
            "outgoing": vertex.get("outgoing", []),
            "incoming": vertex.get("incoming", []),
        }])

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _FakeDriver:
    def __init__(self, vertices: dict[str, dict]):
        self.vertices = vertices

    def session(self):
        return _FakeSession(self.vertices)


class _UnreachableDriver:
    def session(self):
        from neo4j.exceptions import ServiceUnavailable
        raise ServiceUnavailable("no connection to Neo4j")


def _node_service_graph():
    """compute-02 :Node <-[RUNS_ON]- nova-compute@compute-02 :Service --
    the same demo scenario test_alert_correlation.py uses."""
    return {
        "compute-02": {
            "label": "Node",
            "outgoing": [],
            "incoming": [
                {"id": "nova-compute@compute-02", "label": "Service", "relationship": "RUNS_ON", "direction": "incoming"},
            ],
        },
        "nova-compute@compute-02": {
            "label": "Service",
            "outgoing": [
                {"id": "compute-02", "label": "Node", "relationship": "RUNS_ON", "direction": "outgoing"},
            ],
            "incoming": [],
        },
    }


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


# ------------------------------------------------------------------- tests --

def test_node_and_service_pair_produces_directed_suggestion(db, monkeypatch):
    monkeypatch.setattr(graph_db, "driver", _FakeDriver(_node_service_graph()))
    _flag(db, "compute-02", "cpu_usage", severity="critical")
    _flag(db, "nova-compute@compute-02", "cpu_usage", severity="high")

    suggestions = find_causal_suggestions(db)

    assert len(suggestions) == 1
    s = suggestions[0]
    assert s["cause"]["id"] == "compute-02"
    assert s["cause"]["label"] == "Node"
    assert s["effect"]["id"] == "nova-compute@compute-02"
    assert s["effect"]["label"] == "Service"
    assert s["relationship"] == "RUNS_ON"
    # Acceptance criterion: the relationship name must literally appear in
    # the sentence, not just metric names -- fails the test if someone
    # "simplifies" the template down to a generic correlation line.
    assert "RUNS_ON" in s["text"]
    assert s["text"] == (
        "compute-02's cpu_usage is critical, which likely caused "
        "nova-compute@compute-02's cpu_usage anomaly, since "
        "nova-compute@compute-02 RUNS_ON compute-02."
    )


def test_no_suggestion_when_anomalous_pair_has_no_edge(db, monkeypatch):
    vertices = {
        "compute-02": {"label": "Node", "outgoing": [], "incoming": []},
        "storage-09": {"label": "Node", "outgoing": [], "incoming": []},
    }
    monkeypatch.setattr(graph_db, "driver", _FakeDriver(vertices))
    _flag(db, "compute-02", "cpu_usage", severity="high")
    _flag(db, "storage-09", "ram_usage", severity="high")

    assert find_causal_suggestions(db) == []


def test_no_suggestion_when_neighbor_is_not_anomalous(db, monkeypatch):
    monkeypatch.setattr(graph_db, "driver", _FakeDriver(_node_service_graph()))
    _flag(db, "compute-02", "cpu_usage", severity="high")
    # nova-compute@compute-02 has no open AnomalyFlag row -- normal.

    assert find_causal_suggestions(db) == []


def test_connects_adjacency_is_skipped_not_defaulted(db, monkeypatch):
    """CONNECTS isn't in the directionality table -- must be skipped
    outright, never guessed at a direction."""
    vertices = {
        "sub-1": {
            "label": "Subnet",
            "outgoing": [
                {"id": "net-1", "label": "Network", "relationship": "CONNECTS", "direction": "outgoing"},
            ],
            "incoming": [],
        },
        "net-1": {
            "label": "Network",
            "outgoing": [],
            "incoming": [
                {"id": "sub-1", "label": "Subnet", "relationship": "CONNECTS", "direction": "incoming"},
            ],
        },
    }
    monkeypatch.setattr(graph_db, "driver", _FakeDriver(vertices))
    _flag(db, "sub-1", "service_state", severity="high", current_value=1.0, z_score=0.0, baseline_n=1)
    _flag(db, "net-1", "service_state", severity="high", current_value=1.0, z_score=0.0, baseline_n=1)

    assert find_causal_suggestions(db) == []


def test_no_open_alerts_returns_empty_without_touching_graph(db, monkeypatch):
    class _ExplodingDriver:
        def session(self):
            raise AssertionError("should not query the graph with no open alerts")

    monkeypatch.setattr(graph_db, "driver", _ExplodingDriver())

    assert find_causal_suggestions(db) == []


def test_graph_unavailable_propagates(db, monkeypatch):
    monkeypatch.setattr(graph_db, "driver", _UnreachableDriver())
    _flag(db, "compute-02", "cpu_usage", severity="high")

    from neo4j.exceptions import ServiceUnavailable
    with pytest.raises(ServiceUnavailable):
        find_causal_suggestions(db)


def test_serves_relationship_uses_service_as_cause(db, monkeypatch):
    vertices = {
        "neutron-dhcp-agent@ctrl-01": {
            "label": "Service",
            "outgoing": [
                {"id": "net-1", "label": "Network", "relationship": "SERVES", "direction": "outgoing"},
            ],
            "incoming": [],
        },
        "net-1": {
            "label": "Network",
            "outgoing": [],
            "incoming": [
                {"id": "neutron-dhcp-agent@ctrl-01", "label": "Service", "relationship": "SERVES", "direction": "incoming"},
            ],
        },
    }
    monkeypatch.setattr(graph_db, "driver", _FakeDriver(vertices))
    _flag(db, "neutron-dhcp-agent@ctrl-01", "service_state", severity="critical", current_value=1.0, z_score=0.0, baseline_n=1)
    _flag(db, "net-1", "service_state", severity="high", current_value=1.0, z_score=0.0, baseline_n=1)

    suggestions = find_causal_suggestions(db)

    assert len(suggestions) == 1
    s = suggestions[0]
    assert s["cause"]["id"] == "neutron-dhcp-agent@ctrl-01"
    assert s["effect"]["id"] == "net-1"
    assert s["relationship"] == "SERVES"
    assert "SERVES" in s["text"]


def test_worst_metric_represents_a_multi_metric_vertex(db, monkeypatch):
    monkeypatch.setattr(graph_db, "driver", _FakeDriver(_node_service_graph()))
    _flag(db, "compute-02", "cpu_usage", severity="medium", z_score=2.1)
    _flag(db, "compute-02", "ram_usage", severity="critical", z_score=5.0)
    _flag(db, "nova-compute@compute-02", "cpu_usage", severity="high")

    suggestions = find_causal_suggestions(db)

    assert len(suggestions) == 1
    assert suggestions[0]["cause"]["metric_name"] == "ram_usage"
    assert suggestions[0]["cause"]["severity"] == "critical"
