"""Tests for app/agents/nodes/network.py -- the Network Agent (v0.9) and
its Phase C (v0.10) entity-scoped extension.

Same style as test_anomaly_agent.py: every external call the node makes
(collect_network_metrics, network_health.get_node_network_health and the
Phase C entity-scoped reads/candidate lists) is monkeypatched at its call
site rather than exercised against a real Prometheus/OpenStack, and no
NVIDIA_API_KEY is set so every narration function always takes the
deterministic LLMConfigError fallback path.
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


def _health(agents=None, routers=None, networks=None, floating_ips=None, instances=None):
    return {
        "hostname": NODE["hostname"],
        "agents": agents or [],
        "routers": routers or [],
        "networks": networks or [],
        "floating_ips": floating_ips or [],
        "instances": instances or [],
    }


def _agent(binary="neutron-openvswitch-agent", alive=True, admin_state_up=True):
    return {"id": "a1", "binary": binary, "agent_type": "Open vSwitch agent",
            "host": NODE["hostname"], "alive": alive, "admin_state_up": admin_state_up}


def _instance(id="i1", name="vm-1", status="ACTIVE", has_down_port=False, ports=None):
    return {"id": id, "name": name, "status": status, "hypervisor_hostname": NODE["hostname"],
            "ports": ports or [], "has_down_port": has_down_port}


def _port(id="p1", name="port1", status="ACTIVE", admin_state_up=True, network_id="net-1", fixed_ips=None):
    return {"id": id, "name": name, "status": status, "admin_state_up": admin_state_up,
            "device_id": None, "device_owner": "compute:nova", "network_id": network_id,
            "mac_address": "fa:16:3e:00:00:01", "fixed_ips": fixed_ips or []}


def _no_known_entities(monkeypatch):
    """Most tests don't exercise the Phase C entity-scoped fallback at
    all (a node always resolves first) -- but network_agent still calls
    resolve_node before anything else, and a couple of tests deliberately
    give it a query/known_nodes combo where that fails. Monkeypatching
    the three list_known_* functions to empty keeps those tests fast and
    deterministic instead of depending on this environment's (lack of)
    OpenStack config for the fallback path's own failure mode."""
    monkeypatch.setattr(network_health, "list_known_networks", lambda conn=None: [])
    monkeypatch.setattr(network_health, "list_known_subnets", lambda conn=None: [])
    monkeypatch.setattr(network_health, "list_known_instances", lambda conn=None: [])


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
    assert agent_result["raw_data"]["scope"] == "node"
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
# Phase C (v0.10) -- instance/port data folded into the node-scoped check
# --------------------------------------------------------------------

def test_check_neutron_flags_instance_with_down_port(monkeypatch):
    bad_instance = _instance(id="i3", name="sandbox-vm-3-broken", status="ERROR", has_down_port=True)
    monkeypatch.setattr(
        network_health, "get_node_network_health",
        lambda hostname, conn=None: _health(agents=[_agent()], instances=[bad_instance]),
    )

    signal = network._check_neutron(NODE)

    assert signal["has_signal"] is True
    assert signal["bad_instances"] == [bad_instance]
    assert "sandbox-vm-3-broken" in signal["detail"]


def test_check_neutron_clean_when_no_instances_have_down_ports(monkeypatch):
    healthy_instance = _instance(id="i1", name="vm-1", status="ACTIVE", has_down_port=False)
    monkeypatch.setattr(
        network_health, "get_node_network_health",
        lambda hostname, conn=None: _health(agents=[_agent()], instances=[healthy_instance]),
    )

    signal = network._check_neutron(NODE)

    assert signal["has_signal"] is False
    assert signal["bad_instances"] == []


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
# Node resolution failure -> falls through to the Phase C entity path,
# which also finds nothing -> error
# --------------------------------------------------------------------

def test_network_agent_sets_error_when_nothing_resolves(monkeypatch):
    _no_known_entities(monkeypatch)

    state = {"user_query": "is the network okay?", "known_nodes": [
        {"hostname": "compute-02", "role": "compute", "instance": "10.0.1.12:9100"},
        {"hostname": "storage-09", "role": "storage", "instance": "10.0.2.9:9100"},
    ]}
    result = network.network_agent(state)

    assert result["agent_result"] is None
    assert "couldn't tell which node, network, subnet, or instance" in result["error"].lower()


def test_network_agent_degrades_when_entity_lookup_itself_fails(monkeypatch):
    def boom(conn=None):
        raise Exception("timeout")

    monkeypatch.setattr(network_health, "list_known_networks", boom)
    monkeypatch.setattr(network_health, "list_known_subnets", lambda conn=None: [])
    monkeypatch.setattr(network_health, "list_known_instances", lambda conn=None: [])

    state = {"user_query": "is the network okay?", "known_nodes": [
        {"hostname": "compute-02", "role": "compute", "instance": "10.0.1.12:9100"},
        {"hostname": "storage-09", "role": "storage", "instance": "10.0.2.9:9100"},
    ]}
    result = network.network_agent(state)

    assert result["agent_result"] is None
    assert "couldn't tell which node" in result["error"].lower()
    assert "couldn't look up networks" in result["error"].lower()
    assert result["failures"][0]["source"] == "network.entity_lookup"


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


# ======================================================================
# Phase C (v0.10) -- entity-scoped path (network / subnet / instance)
# ======================================================================

_NETWORK_ENTITY = {"kind": "network", "id": "net-1", "name": "sandbox-net", "cidr": None}
_SUBNET_ENTITY = {"kind": "subnet", "id": "sub-1", "name": "sandbox-subnet", "cidr": "10.0.0.0/24"}
_INSTANCE_ENTITY = {"kind": "instance", "id": "i1", "name": "sandbox-vm-1", "cidr": None}


def _stub_entity_lookup(monkeypatch, networks=None, subnets=None, instances=None):
    monkeypatch.setattr(network_health, "list_known_networks", lambda conn=None: networks or [])
    monkeypatch.setattr(network_health, "list_known_subnets", lambda conn=None: subnets or [])
    monkeypatch.setattr(network_health, "list_known_instances", lambda conn=None: instances or [])


def test_network_agent_which_vms_on_network(monkeypatch):
    _stub_entity_lookup(monkeypatch, networks=[{"id": "net-1", "name": "sandbox-net"}])
    healthy_row = {"instance": {"id": "i1", "name": "vm-1", "status": "ACTIVE"}, "port": _port(), "port_down": False}
    monkeypatch.setattr(
        network_health, "get_network_instance_health",
        lambda network_id, conn=None: {"network_id": network_id, "instances": [healthy_row]},
    )

    state = {"user_query": "which VMs are on sandbox-net", "known_nodes": []}
    result = network.network_agent(state)

    agent_result = result["agent_result"]
    assert result["error"] is None
    assert agent_result["raw_data"]["scope"] == "network"
    assert agent_result["raw_data"]["entity"]["id"] == "net-1"
    assert agent_result["raw_data"]["entity_signal"]["has_signal"] is False
    assert "vm-1" in agent_result["summary"]
    assert result["resolved_entities"]["last_network_entity"]["id"] == "net-1"
    assert result["resolved_entities"]["last_agent"] == "network"


def test_network_agent_network_scope_flags_down_port(monkeypatch):
    _stub_entity_lookup(monkeypatch, networks=[{"id": "net-1", "name": "sandbox-net"}])
    bad_row = {
        "instance": {"id": "i3", "name": "sandbox-vm-3-broken", "status": "ERROR"},
        "port": _port(id="p3", status="DOWN"), "port_down": True,
    }
    monkeypatch.setattr(
        network_health, "get_network_instance_health",
        lambda network_id, conn=None: {"network_id": network_id, "instances": [bad_row]},
    )

    state = {"user_query": "is anything down on sandbox-net", "known_nodes": []}
    result = network.network_agent(state)

    entity_signal = result["agent_result"]["raw_data"]["entity_signal"]
    assert entity_signal["has_signal"] is True
    assert "sandbox-vm-3-broken" in entity_signal["detail"]


def test_network_agent_subnet_scope_reports_down_ports(monkeypatch):
    _stub_entity_lookup(monkeypatch, subnets=[{"id": "sub-1", "name": "sandbox-subnet", "cidr": "10.0.0.0/24"}])
    monkeypatch.setattr(
        network_health, "get_subnet_port_health",
        lambda subnet_id, conn=None: {
            "subnet_id": subnet_id, "network_id": "net-1",
            "ports": [_port(id="p1", status="DOWN")],
            "down_ports": [_port(id="p1", status="DOWN")],
            "dhcp_agents": [],
        },
    )

    state = {"user_query": "is anything down on sandbox-subnet", "known_nodes": []}
    result = network.network_agent(state)

    assert result["agent_result"]["raw_data"]["scope"] == "subnet"
    assert result["agent_result"]["raw_data"]["entity_signal"]["has_signal"] is True


def test_network_agent_instance_scope_explains_no_internet(monkeypatch):
    _stub_entity_lookup(monkeypatch, instances=[{"id": "i1", "name": "sandbox-vm-1"}])
    monkeypatch.setattr(
        network_health, "get_instance_connectivity",
        lambda instance_id, conn=None: {
            "instance_id": instance_id,
            "instance": {"id": instance_id, "name": "sandbox-vm-1", "status": "ACTIVE"},
            "ports": [
                {
                    "port": _port(),
                    "port_down": False,
                    "network": {"id": "net-1", "name": "sandbox-net", "router_external": False},
                    "network_is_external": False,
                    "gateway_routers": [],
                    "floating_ips": [],
                }
            ],
        },
    )

    state = {"user_query": "why can't sandbox-vm-1 reach the internet", "known_nodes": []}
    result = network.network_agent(state)

    entity_signal = result["agent_result"]["raw_data"]["entity_signal"]
    assert entity_signal["has_signal"] is True
    assert "no router with an external gateway" in entity_signal["detail"]


def test_network_agent_instance_scope_down_port_takes_priority_reason(monkeypatch):
    _stub_entity_lookup(monkeypatch, instances=[{"id": "i1", "name": "sandbox-vm-1"}])
    monkeypatch.setattr(
        network_health, "get_instance_connectivity",
        lambda instance_id, conn=None: {
            "instance_id": instance_id,
            "instance": {"id": instance_id, "name": "sandbox-vm-1", "status": "ERROR"},
            "ports": [
                {
                    "port": _port(status="DOWN"),
                    "port_down": True,
                    "network": {"id": "net-1", "name": "sandbox-net", "router_external": False},
                    "network_is_external": False,
                    "gateway_routers": [],
                    "floating_ips": [],
                }
            ],
        },
    )

    state = {"user_query": "why can't sandbox-vm-1 reach the internet", "known_nodes": []}
    result = network.network_agent(state)

    entity_signal = result["agent_result"]["raw_data"]["entity_signal"]
    assert "port on sandbox-net is down" in entity_signal["detail"]
    assert entity_signal["down_instances"] == [{"id": "i1", "name": "sandbox-vm-1", "status": "ERROR"}]


def test_network_agent_entity_scope_degrades_gracefully_when_neutron_unreachable(monkeypatch):
    _stub_entity_lookup(monkeypatch, networks=[{"id": "net-1", "name": "sandbox-net"}])

    def boom(network_id, conn=None):
        raise Exception("connection refused")

    monkeypatch.setattr(network_health, "get_network_instance_health", boom)

    state = {"user_query": "which VMs are on sandbox-net", "known_nodes": []}
    result = network.network_agent(state)

    entity_signal = result["agent_result"]["raw_data"]["entity_signal"]
    assert entity_signal["degraded"] is True
    assert result["agent_result"]["confidence"] <= network._DEGRADED_NEUTRON_CONFIDENCE_CAP
    assert result["failures"][0]["source"] == "network.neutron"


def test_network_agent_ambiguous_entity_query_sets_error(monkeypatch):
    # Two same-kind candidates, query names neither -- exact tier finds
    # nothing unique, LLM tier is skipped (no NVIDIA_API_KEY in test env),
    # session-memory tier has nothing to fall back to either.
    _stub_entity_lookup(
        monkeypatch,
        networks=[{"id": "net-1", "name": "sandbox-net"}, {"id": "net-2", "name": "sandbox-storage-net"}],
    )

    state = {"user_query": "is anything down on the network", "known_nodes": []}
    result = network.network_agent(state)

    assert result["agent_result"] is None
    assert "couldn't tell which node, network, subnet, or instance" in result["error"].lower()
