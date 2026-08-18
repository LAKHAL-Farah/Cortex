"""Tests for the condensed network-health API response."""

from neo4j.exceptions import ServiceUnavailable

from app import graph_db
from app.routers import network


def test_network_health_is_ok_when_graph_and_nodes_are_healthy(monkeypatch):
    monkeypatch.setattr(
        graph_db,
        "fetch_network_anomalies",
        lambda: {"routers_down": [], "floating_ips_orphaned": [], "ports_down": []},
    )
    monkeypatch.setattr(
        network,
        "measure_node_latencies",
        lambda db: [{"hostname": "controller", "ip_address": "10.0.1.10", "port": 9100, "latency_ms": 1.2, "reachable": True, "error": None}],
    )

    result = network.get_network_health(db=object())

    assert result.status == "ok"
    assert result.graph_available is True


def test_network_health_is_degraded_when_graph_is_unavailable(monkeypatch):
    def graph_unavailable():
        raise ServiceUnavailable("Neo4j unavailable")

    monkeypatch.setattr(graph_db, "fetch_network_anomalies", graph_unavailable)
    monkeypatch.setattr(network, "measure_node_latencies", lambda db: [])

    result = network.get_network_health(db=object())

    assert result.status == "degraded"
    assert result.graph_available is False
    assert result.routers_down == []
