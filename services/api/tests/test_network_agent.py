"""Tests for app/agents/nodes/network.py -- the v0.9 Network Agent.

Same style as test_anomaly_agent.py: every external call the node makes
(collect_network_metrics, network_health.get_node_network_health) is
monkeypatched at its call site rather than exercised against a real
Prometheus/OpenStack, and no NVIDIA_API_KEY is set so _narrate always
takes the deterministic LLMConfigError fallback path.
"""
from app.agents.nodes import network
from app.services import network_health

NODE = {"hostname": "compute-02", "role": "compute", "instance": "10.0.1.12:9100"}
KNOWN_NODES = [NODE]


def _metrics(instance=NODE["instance"], rx=1000.0, tx=500.0, errors=0.0, drops=0.0, status="up"):
    return {
        "node": NODE["hostname"],
        "role": NODE["role"],
        "instance": instance,
        "network_rx_bytes": rx,
        "network_tx_bytes": tx,
        "network_errors_per_sec": errors,
        "network_drops_per_sec": drops,
        "status": status,
    }


def _health(agents=None, routers=None, networks=None, floating_ips=None):
    return {
        "hostname": NODE["hostname"],
        "agents": agents or [],
        "routers": routers or [],
        "networks": networks or [],
        "floating_ips": floating_ips or [],
    }


def _agent(binary="neutron-openvswitch-agent", alive=True, admin_state_up=True):
    return {"id": "a1", "binary": binary, "agent_type": "Open vSwitch agent",
            "host": NODE["hostname"], "alive": alive, "admin_state_up": admin_state_up}


# --------------------------------------------------------------------
# Clean bill of health on both sources
# --------------------------------------------------------------------

def test_network_agent_reports_clean_reading_with_full_confidence(monkeypatch):
    monkeypatch.setattr(network, "collect_network_metrics", lambda: [_metrics()])
    monkeypatch.setattr(
        network_health, "get_node_network_health",
        lambda hostname, conn=None: _health(agents=[_agent()]),
    )

    state = {"user_query": "how's the network on compute-02", "known_nodes": KNOWN_NODES}
    result = network.network_agent(state)

    agent_result = result["agent_result"]
    assert agent_result["confidence"] == 1.0
    assert result["error"] is None
    assert "compute-02" in agent_result["summary"]
    assert agent_result["raw_data"]["metric_signal"]["has_signal"] is False
    assert agent_result["raw_data"]["neutron_signal"]["has_signal"] is False


# --------------------------------------------------------------------
# Node-level errors/drops surface as a signal
# --------------------------------------------------------------------

def test_network_agent_flags_nonzero_error_and_drop_rates(monkeypatch):
    monkeypatch.setattr(network, "collect_network_metrics", lambda: [_metrics(errors=3.5, drops=1.2)])
    monkeypatch.setattr(
        network_health, "get_node_network_health",
        lambda hostname, conn=None: _health(agents=[_agent()]),
    )

    state = {"user_query": "any packet loss on compute-02?", "known_nodes": KNOWN_NODES}
    result = network.network_agent(state)

    metric_signal = result["agent_result"]["raw_data"]["metric_signal"]
    assert metric_signal["has_signal"] is True
    assert "3.50 errors/sec" in metric_signal["detail"]
    assert "1.20 dropped packets/sec" in metric_signal["detail"]
    assert result["agent_result"]["confidence"] == 1.0  # Neutron side still healthy, not degraded


def test_network_agent_no_error_or_drops_is_not_a_signal(monkeypatch):
    monkeypatch.setattr(network, "collect_network_metrics", lambda: [_metrics(errors=0.0, drops=0.0)])
    monkeypatch.setattr(
        network_health, "get_node_network_health",
        lambda hostname, conn=None: _health(agents=[_agent()]),
    )

    state = {"user_query": "how's compute-02's network", "known_nodes": KNOWN_NODES}
    result = network.network_agent(state)

    assert result["agent_result"]["raw_data"]["metric_signal"]["has_signal"] is False


# --------------------------------------------------------------------
# Neutron control-plane problems surface as a signal
# --------------------------------------------------------------------

def test_network_agent_flags_down_neutron_agent(monkeypatch):
    monkeypatch.setattr(network, "collect_network_metrics", lambda: [_metrics()])
    down = _agent(alive=False)
    monkeypatch.setattr(
        network_health, "get_node_network_health",
        lambda hostname, conn=None: _health(agents=[down]),
    )

    state = {"user_query": "is the network okay on compute-02", "known_nodes": KNOWN_NODES}
    result = network.network_agent(state)

    neutron_signal = result["agent_result"]["raw_data"]["neutron_signal"]
    assert neutron_signal["has_signal"] is True
    assert neutron_signal["down_agents"] == [down]
    assert "neutron-openvswitch-agent" in neutron_signal["detail"]


def test_check_neutron_flags_bad_router_directly(monkeypatch):
    bad_router = {"id": "r1", "name": "router1", "status": "DOWN", "admin_state_up": True}
    monkeypatch.setattr(
        network_health, "get_node_network_health",
        lambda hostname, conn=None: _health(agents=[_agent(binary="neutron-l3-agent")], routers=[bad_router]),
    )

    signal = network._check_neutron(NODE)

    assert signal["has_signal"] is True
    assert signal["bad_routers"] == [bad_router]
    assert "router(s) hosted here not fully up" in signal["detail"]


# --------------------------------------------------------------------
# Neutron unreachable -> degrade, don't hard-fail
# --------------------------------------------------------------------

def test_network_agent_degrades_gracefully_when_neutron_unreachable(monkeypatch):
    monkeypatch.setattr(network, "collect_network_metrics", lambda: [_metrics()])

    def boom(hostname, conn=None):
        raise Exception("connection refused")

    monkeypatch.setattr(network_health, "get_node_network_health", boom)

    state = {"user_query": "check the network on compute-02", "known_nodes": KNOWN_NODES}
    result = network.network_agent(state)

    agent_result = result["agent_result"]
    assert agent_result["raw_data"]["neutron_signal"]["degraded"] is True
    assert agent_result["confidence"] <= network._DEGRADED_NEUTRON_CONFIDENCE_CAP
    assert "couldn't complete" in agent_result["summary"].lower()

    assert len(result["failures"]) == 1
    assert result["failures"][0]["source"] == "network.neutron"


# --------------------------------------------------------------------
# Node resolution failure
# --------------------------------------------------------------------

def test_network_agent_sets_error_when_node_unresolvable():
    state = {"user_query": "is the network okay?", "known_nodes": [
        {"hostname": "compute-02", "role": "compute", "instance": "10.0.1.12:9100"},
        {"hostname": "storage-09", "role": "storage", "instance": "10.0.2.9:9100"},
    ]}
    result = network.network_agent(state)

    assert result["agent_result"] is None
    assert "couldn't tell which node" in result["error"].lower()


# --------------------------------------------------------------------
# resolved_entities bookkeeping (session-memory follow-ups)
# --------------------------------------------------------------------

def test_network_agent_records_last_node_and_agent(monkeypatch):
    monkeypatch.setattr(network, "collect_network_metrics", lambda: [_metrics()])
    monkeypatch.setattr(
        network_health, "get_node_network_health",
        lambda hostname, conn=None: _health(agents=[_agent()]),
    )

    state = {"user_query": "how's the network on compute-02", "known_nodes": KNOWN_NODES}
    result = network.network_agent(state)

    assert result["resolved_entities"]["last_node"]["hostname"] == "compute-02"
    assert result["resolved_entities"]["last_agent"] == "network"
