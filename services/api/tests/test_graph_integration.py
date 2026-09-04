"""End-to-end tests through the actual compiled LangGraph (app.agents.
graph.app_graph), not just the individual node functions -- these are the
tests that directly demonstrate v0.6's definition of done: a live
incident produces the full 3-layer OpenStack Expert answer, and killing
Loki mid-incident still returns a sensible, honestly-degraded answer
instead of hanging.

Every external dependency is monkeypatched at its own call site (same
convention as test_anomaly_agent.py) -- no real Postgres/Prometheus/Loki/
NVIDIA API involved. No NVIDIA_API_KEY is set in this environment, so
intent_router.route() always falls back to DEFAULT_AGENT ("monitoring")
unless a fake LLM is installed -- which is used deliberately below to
reach the anomaly/openstack_expert branches without needing a real model.
"""
import time
from types import SimpleNamespace

import app.agents.intent_router as intent_router
import app.agents.nodes.anomaly as anomaly
import app.agents.nodes.monitoring as monitoring
import app.agents.nodes.network as network
from app.agents.graph import app_graph
from app.services import loki_client, network_health

NODE = {"hostname": "compute-02", "role": "compute", "instance": "10.0.1.12:9100"}
KNOWN_NODES = [NODE]


def _flag(metric_name="cpu_usage", severity="critical", z_score=4.2, current_value=97.3):
    from datetime import datetime
    return SimpleNamespace(
        hostname=NODE["hostname"],
        metric_name=metric_name,
        severity=severity,
        z_score=z_score,
        current_value=current_value,
        method="robust_zscore",
        detected_at=datetime(2026, 8, 25, 12, 0, 0),
    )


def _route_to(monkeypatch, agent: str, confidence: float = 0.9):
    """Installs a fake classifier so route() picks `agent` deterministically,
    for exercising branches DEFAULT_AGENT's no-API-key fallback can't reach."""
    classification = SimpleNamespace(agent=agent, confidence=confidence)

    class _FakeStructured:
        def invoke(self, messages):
            return classification

    class _FakeLLM:
        def with_structured_output(self, schema):
            return _FakeStructured()

    monkeypatch.setattr(intent_router, "get_chat_model", lambda **kwargs: _FakeLLM())


def _invoke(query: str):
    return app_graph.invoke({"user_query": query, "known_nodes": KNOWN_NODES, "failures": []})


# --------------------------------------------------------------------
# DoD scenario 1: a live incident produces the full 3-layer answer
# --------------------------------------------------------------------

def test_live_incident_produces_full_three_layer_expert_answer(monkeypatch):
    _route_to(monkeypatch, "anomaly")
    monkeypatch.setattr(anomaly.crud, "list_open_anomaly_flags", lambda db, hostname: [_flag()])
    monkeypatch.setattr(loki_client, "query_range", lambda *a, **k: [])  # clean, no correlated logs

    result = _invoke("something's wrong with compute-02")

    assert result["target_agent"] == "openstack_expert"
    answer = result["final_answer"]
    assert "What's happening" in answer
    assert "How to confirm it yourself" in answer
    assert "What's usually done about it" in answer
    assert "(read-only)" in answer
    assert "(state-changing)" in answer
    assert result["failures"] == []  # clean run, nothing degraded


# --------------------------------------------------------------------
# DoD scenario 2: kill Loki mid-incident -> degraded, not hung/crashed
# --------------------------------------------------------------------

def test_killing_loki_mid_incident_returns_promptly_and_honestly_degraded(monkeypatch):
    _route_to(monkeypatch, "anomaly")
    monkeypatch.setattr(anomaly.crud, "list_open_anomaly_flags", lambda db, hostname: [_flag()])

    def _dead_connection(*a, **k):
        raise ConnectionError("connection refused")

    monkeypatch.setattr(loki_client, "query_range", _dead_connection)

    started = time.monotonic()
    result = _invoke("something's wrong with compute-02")
    elapsed = time.monotonic() - started

    # Must not hang: the breaker's timeout+one-retry budget (8s, see
    # anomaly.py's get_breaker("anomaly.loki", ...)) bounds this well
    # under the test-runner-friendly ceiling below, even though the
    # underlying exception is actually instant here.
    assert elapsed < 5.0

    assert result["failures"], "a failed Loki call must surface as a FailureRecord"
    assert result["failures"][0]["source"] == "anomaly.loki"

    answer = result["final_answer"]
    # Honestly labeled: the resilience-layer degraded note is present...
    assert "log-check" in answer.lower()
    assert "reduced confidence" in answer.lower() or "failed while gathering evidence" in answer.lower()
    # ...and the chat still returns a full, useful answer on top of it --
    # the metric-only diagnosis still chained into the expert agent.
    assert "What's happening" in answer
    assert "How to confirm it yourself" in answer
    assert "What's usually done about it" in answer
    assert result["target_agent"] == "openstack_expert"


# --------------------------------------------------------------------
# Chaining after monitoring (not just anomaly)
# --------------------------------------------------------------------

def test_concerning_monitoring_reading_chains_into_expert_agent(monkeypatch):
    # No _route_to needed: with no NVIDIA_API_KEY set, route() already
    # defaults to "monitoring" -- this exercises that real fallback path,
    # not a forced one.
    monkeypatch.setattr(
        monitoring,
        "collect_metrics",
        lambda: [{
            "instance": NODE["instance"], "node": NODE["hostname"], "role": NODE["role"],
            "cpu_percent": 96, "memory_percent": 40, "disk_percent": 30,
            "status": "up", "health": "critical",
        }],
    )

    result = _invoke("how is compute-02 doing")

    assert result["target_agent"] == "openstack_expert"
    assert "host-cpu-pressure" == result["agent_result"]["raw_data"]["matched_symptom_id"]
    assert "diagnosed_by" in result["agent_result"]["raw_data"]
    assert result["agent_result"]["raw_data"]["diagnosed_by"] == "monitoring"


def test_healthy_monitoring_reading_does_not_chain_into_expert_agent(monkeypatch):
    monkeypatch.setattr(
        monitoring,
        "collect_metrics",
        lambda: [{
            "instance": NODE["instance"], "node": NODE["hostname"], "role": NODE["role"],
            "cpu_percent": 12, "memory_percent": 30, "disk_percent": 40,
            "status": "up", "health": "healthy",
        }],
    )

    result = _invoke("how is compute-02 doing")

    assert result["target_agent"] == "monitoring"  # never chained -- nothing to teach
    assert "What's happening" not in result["final_answer"]


# --------------------------------------------------------------------
# Chaining after network (v0.9)
# --------------------------------------------------------------------

def _network_health(agents=None, routers=None, networks=None, floating_ips=None):
    return {
        "hostname": NODE["hostname"], "agents": agents or [], "routers": routers or [],
        "networks": networks or [], "floating_ips": floating_ips or [],
    }


def test_down_neutron_agent_chains_into_expert_agent(monkeypatch):
    _route_to(monkeypatch, "network")
    monkeypatch.setattr(network, "collect_network_metrics", lambda: [{
        "instance": NODE["instance"], "node": NODE["hostname"], "role": NODE["role"],
        "network_rx_bytes": 1000.0, "network_tx_bytes": 500.0,
        "network_errors_per_sec": 0.0, "network_drops_per_sec": 0.0, "status": "up",
    }])
    down_agent = {
        "id": "a1", "binary": "neutron-openvswitch-agent", "agent_type": "Open vSwitch agent",
        "host": NODE["hostname"], "alive": False, "admin_state_up": True,
    }
    monkeypatch.setattr(
        network_health, "get_node_network_health",
        lambda hostname, conn=None: _network_health(agents=[down_agent]),
    )

    result = _invoke("is the network okay on compute-02")

    assert result["target_agent"] == "openstack_expert"
    assert result["agent_result"]["raw_data"]["matched_symptom_id"] == "neutron-ovs-agent-down"
    assert result["agent_result"]["raw_data"]["diagnosed_by"] == "network"
    answer = result["final_answer"]
    assert "What's happening" in answer
    assert "How to confirm it yourself" in answer
    assert "What's usually done about it" in answer


def test_healthy_network_reading_does_not_chain_into_expert_agent(monkeypatch):
    _route_to(monkeypatch, "network")
    monkeypatch.setattr(network, "collect_network_metrics", lambda: [{
        "instance": NODE["instance"], "node": NODE["hostname"], "role": NODE["role"],
        "network_rx_bytes": 1000.0, "network_tx_bytes": 500.0,
        "network_errors_per_sec": 0.0, "network_drops_per_sec": 0.0, "status": "up",
    }])
    healthy_agent = {
        "id": "a1", "binary": "neutron-openvswitch-agent", "agent_type": "Open vSwitch agent",
        "host": NODE["hostname"], "alive": True, "admin_state_up": True,
    }
    monkeypatch.setattr(
        network_health, "get_node_network_health",
        lambda hostname, conn=None: _network_health(agents=[healthy_agent]),
    )

    result = _invoke("is the network okay on compute-02")

    assert result["target_agent"] == "network"  # never chained -- nothing to teach
    assert "What's happening" not in result["final_answer"]


def test_neutron_outage_during_network_check_degrades_but_does_not_hang(monkeypatch):
    _route_to(monkeypatch, "network")
    monkeypatch.setattr(network, "collect_network_metrics", lambda: [{
        "instance": NODE["instance"], "node": NODE["hostname"], "role": NODE["role"],
        "network_rx_bytes": 1000.0, "network_tx_bytes": 500.0,
        "network_errors_per_sec": 0.0, "network_drops_per_sec": 0.0, "status": "up",
    }])

    def _dead_connection(hostname, conn=None):
        raise ConnectionError("connection refused")

    monkeypatch.setattr(network_health, "get_node_network_health", _dead_connection)

    started = time.monotonic()
    result = _invoke("is the network okay on compute-02")
    elapsed = time.monotonic() - started

    assert elapsed < 5.0
    assert result["failures"], "a failed Neutron call must surface as a FailureRecord"
    assert result["failures"][0]["source"] == "network.neutron"
    assert result["target_agent"] == "network"  # chain attempted (degraded still triggers,
    # same idiom as anomaly.py's log_signal.get("degraded")) but with no down_agents to
    # match against, openstack_expert finds nothing and leaves this diagnosis as final.
    assert "couldn't complete" in result["final_answer"].lower()


# --------------------------------------------------------------------
# Standalone routing straight to the expert agent
# --------------------------------------------------------------------

def test_standalone_how_do_i_check_question_routes_directly(monkeypatch):
    _route_to(monkeypatch, "openstack_expert")

    result = _invoke("how do I check if nova-compute is running")

    assert result["target_agent"] == "openstack_expert"
    assert result["agent_result"]["raw_data"]["matched_symptom_id"] == "nova-compute-down"
    assert result["agent_result"]["raw_data"]["diagnosed_by"] is None  # not chained
