"""Tests for services/network_health.py -- Neutron control-plane health
scoped to one host's agents, plus (v0.10, Phase C) instance/port-scoped
reads. Same faking style as test_topology_sync.py: a tiny SimpleNamespace
stand-in for openstacksdk resources, and a fake `conn.network`/`conn.compute`
namespace passed straight into the functions under test (no need to
monkeypatch _connect() at all, since every function accepts an optional
`conn`)."""
import types

from app.services import network_health


def _agent(id, binary, host, agent_type, is_alive=True, is_admin_state_up=True):
    return types.SimpleNamespace(
        id=id, binary=binary, host=host, agent_type=agent_type,
        is_alive=is_alive, is_admin_state_up=is_admin_state_up,
    )


def _router(id, name="router", status="ACTIVE", is_admin_state_up=True, external_gateway_info=None):
    return types.SimpleNamespace(
        id=id, name=name, status=status, is_admin_state_up=is_admin_state_up,
        external_gateway_info=external_gateway_info,
    )


def _network(id, name="net", status="ACTIVE", is_admin_state_up=True, is_router_external=False):
    return types.SimpleNamespace(
        id=id, name=name, status=status, is_admin_state_up=is_admin_state_up,
        is_router_external=is_router_external,
    )


def _fip(id, router_id, status="ACTIVE", floating_ip_address="203.0.113.1", fixed_ip_address="10.0.0.5"):
    return types.SimpleNamespace(
        id=id, router_id=router_id, status=status,
        floating_ip_address=floating_ip_address, fixed_ip_address=fixed_ip_address,
    )


def _server(id, name="vm", status="ACTIVE", hypervisor_hostname=None):
    return types.SimpleNamespace(id=id, name=name, status=status, hypervisor_hostname=hypervisor_hostname)


def _port(
    id, name="port", status="ACTIVE", is_admin_state_up=True, device_id=None,
    device_owner="compute:nova", network_id="net-1", mac_address="fa:16:3e:00:00:01",
    fixed_ips=None,
):
    return types.SimpleNamespace(
        id=id, name=name, status=status, is_admin_state_up=is_admin_state_up,
        device_id=device_id, device_owner=device_owner, network_id=network_id,
        mac_address=mac_address, fixed_ips=fixed_ips or [],
    )


def _subnet(id, name="subnet", cidr="10.0.0.0/24", network_id="net-1"):
    return types.SimpleNamespace(id=id, name=name, cidr=cidr, network_id=network_id)


class _FakeConn:
    def __init__(
        self, agents=None, l3_hosting=None, dhcp_hosting=None, floating_ips=None,
        servers=None, ports=None, networks=None, subnets=None, routers=None,
    ):
        self._agents = agents or []
        self._l3_hosting = l3_hosting or {}
        self._dhcp_hosting = dhcp_hosting or {}
        self._floating_ips = floating_ips or []
        self._servers = servers or []
        self._ports = ports or []
        self._networks = networks or []
        self._subnets = subnets or []
        self._routers = routers or []

        def _agents_fn(**kw):
            return iter(self._agents)

        def _agent_hosted_routers(agent, **kw):
            return iter(self._l3_hosting.get(agent.id, []))

        def _dhcp_agent_hosting_networks(agent, **kw):
            return iter(self._dhcp_hosting.get(agent.id, []))

        def _ips(**kw):
            return iter(self._floating_ips)

        def _ports_fn(**kw):
            return iter(self._ports)

        def _networks_fn(**kw):
            return iter(self._networks)

        def _subnets_fn(**kw):
            return iter(self._subnets)

        def _routers_fn(**kw):
            return iter(self._routers)

        self.network = types.SimpleNamespace(
            agents=_agents_fn,
            agent_hosted_routers=_agent_hosted_routers,
            dhcp_agent_hosting_networks=_dhcp_agent_hosting_networks,
            ips=_ips,
            ports=_ports_fn,
            networks=_networks_fn,
            subnets=_subnets_fn,
            routers=_routers_fn,
        )

        def _servers_fn(**kw):
            return iter(self._servers)

        self.compute = types.SimpleNamespace(servers=_servers_fn)


def test_scopes_agents_to_the_requested_host_only():
    ovs_here = _agent("a1", "neutron-openvswitch-agent", "compute1-sim", "Open vSwitch agent")
    ovs_elsewhere = _agent("a2", "neutron-openvswitch-agent", "compute2-sim", "Open vSwitch agent")
    conn = _FakeConn(agents=[ovs_here, ovs_elsewhere])

    health = network_health.get_node_network_health("compute1-sim", conn=conn)

    assert [a["id"] for a in health["agents"]] == ["a1"]
    assert health["routers"] == []
    assert health["networks"] == []
    assert health["floating_ips"] == []
    assert health["instances"] == []


def test_l3_agent_pulls_only_its_own_hosted_routers_and_their_floating_ips():
    l3 = _agent("l3-1", "neutron-l3-agent", "controller-sim", "L3 agent")
    hosted_router = _router("r1", status="ACTIVE")
    # "r2" stands in for a router hosted by a *different* agent -- never
    # passed into l3_hosting below, so it never appears in health["routers"].
    fip_on_hosted = _fip("f1", router_id="r1")
    fip_on_other = _fip("f2", router_id="r2")
    conn = _FakeConn(
        agents=[l3],
        l3_hosting={"l3-1": [hosted_router]},
        floating_ips=[fip_on_hosted, fip_on_other],
    )

    health = network_health.get_node_network_health("controller-sim", conn=conn)

    assert [r["id"] for r in health["routers"]] == ["r1"]
    # Only the floating IP whose router_id matches a router *this host*
    # hosts should show up -- other_router's FIP is scoped out even though
    # conn.network.ips() itself returns both.
    assert [f["id"] for f in health["floating_ips"]] == ["f1"]


def test_dhcp_agent_pulls_only_its_own_hosted_networks():
    dhcp = _agent("dhcp-1", "neutron-dhcp-agent", "controller-sim", "DHCP agent")
    hosted_net = _network("n1", status="ACTIVE")
    conn = _FakeConn(agents=[dhcp], dhcp_hosting={"dhcp-1": [hosted_net]})

    health = network_health.get_node_network_health("controller-sim", conn=conn)

    assert [n["id"] for n in health["networks"]] == ["n1"]
    assert health["routers"] == []


def test_down_or_disabled_agent_is_reflected_verbatim():
    dead = _agent("a1", "neutron-openvswitch-agent", "compute1-sim", "Open vSwitch agent", is_alive=False)
    conn = _FakeConn(agents=[dead])

    health = network_health.get_node_network_health("compute1-sim", conn=conn)

    assert health["agents"][0]["alive"] is False


def test_host_with_no_neutron_agents_returns_empty_everything():
    conn = _FakeConn(agents=[])

    health = network_health.get_node_network_health("storage-09", conn=conn)

    assert health == {
        "hostname": "storage-09",
        "agents": [],
        "routers": [],
        "networks": [],
        "floating_ips": [],
        "instances": [],
    }


# --------------------------------------------------------------------
# Phase C (v0.10) -- instance/port data folded into get_node_network_health
# --------------------------------------------------------------------

def test_node_health_includes_hosted_instances_and_their_ports():
    healthy_vm = _server("i1", name="vm-1", status="ACTIVE", hypervisor_hostname="compute1-sim")
    elsewhere_vm = _server("i2", name="vm-2", status="ACTIVE", hypervisor_hostname="compute2-sim")
    healthy_port = _port("p1", device_id="i1", status="ACTIVE", is_admin_state_up=True)
    conn = _FakeConn(agents=[], servers=[healthy_vm, elsewhere_vm], ports=[healthy_port])

    health = network_health.get_node_network_health("compute1-sim", conn=conn)

    assert [i["id"] for i in health["instances"]] == ["i1"]
    assert health["instances"][0]["has_down_port"] is False
    assert [p["id"] for p in health["instances"][0]["ports"]] == ["p1"]


def test_node_health_flags_instance_with_down_port():
    vm = _server("i3", name="vm-3-broken", status="ERROR", hypervisor_hostname="compute1-sim")
    down_port = _port("p3", device_id="i3", status="DOWN", is_admin_state_up=True)
    conn = _FakeConn(agents=[], servers=[vm], ports=[down_port])

    health = network_health.get_node_network_health("compute1-sim", conn=conn)

    assert health["instances"][0]["has_down_port"] is True


def test_node_health_instance_with_no_ports_is_not_flagged_down():
    vm = _server("i4", name="vm-4", status="ACTIVE", hypervisor_hostname="compute1-sim")
    conn = _FakeConn(agents=[], servers=[vm], ports=[])

    health = network_health.get_node_network_health("compute1-sim", conn=conn)

    assert health["instances"][0]["ports"] == []
    assert health["instances"][0]["has_down_port"] is False


# --------------------------------------------------------------------
# Phase C (v0.10) -- entity-scoped reads
# --------------------------------------------------------------------

def test_get_network_instance_health_scopes_to_vm_owned_ports_on_that_network():
    vm = _server("i1", name="vm-1", status="ACTIVE")
    vm_port = _port("p1", device_id="i1", network_id="net-1", device_owner="compute:nova", status="ACTIVE")
    other_net_port = _port("p2", device_id="i1", network_id="net-2", device_owner="compute:nova")
    dhcp_port = _port("p3", device_id="router-1", network_id="net-1", device_owner="network:dhcp")
    conn = _FakeConn(servers=[vm], ports=[vm_port, other_net_port, dhcp_port])

    result = network_health.get_network_instance_health("net-1", conn=conn)

    assert len(result["instances"]) == 1
    assert result["instances"][0]["instance"]["id"] == "i1"
    assert result["instances"][0]["port_down"] is False


def test_get_network_instance_health_flags_down_port():
    vm = _server("i1", name="vm-1", status="ACTIVE")
    down_port = _port("p1", device_id="i1", network_id="net-1", device_owner="compute:nova", is_admin_state_up=False)
    conn = _FakeConn(servers=[vm], ports=[down_port])

    result = network_health.get_network_instance_health("net-1", conn=conn)

    assert result["instances"][0]["port_down"] is True


def test_get_subnet_port_health_scopes_ports_by_fixed_ip_subnet():
    on_subnet = _port("p1", fixed_ips=[{"subnet_id": "sub-1", "ip_address": "10.0.0.5"}])
    other_subnet = _port("p2", fixed_ips=[{"subnet_id": "sub-2", "ip_address": "10.0.1.5"}])
    conn = _FakeConn(ports=[on_subnet, other_subnet], subnets=[_subnet("sub-1", network_id="net-1")])

    result = network_health.get_subnet_port_health("sub-1", conn=conn)

    assert [p["id"] for p in result["ports"]] == ["p1"]
    assert result["down_ports"] == []


def test_get_subnet_port_health_flags_down_ports_and_dead_dhcp_agent():
    down_port = _port("p1", status="DOWN", fixed_ips=[{"subnet_id": "sub-1", "ip_address": "10.0.0.5"}])
    dead_dhcp = _agent("dhcp-1", "neutron-dhcp-agent", "controller-sim", "DHCP agent", is_alive=False)
    conn = _FakeConn(
        ports=[down_port],
        subnets=[_subnet("sub-1", network_id="net-1")],
        agents=[dead_dhcp],
        dhcp_hosting={"dhcp-1": [_network("net-1")]},
    )

    result = network_health.get_subnet_port_health("sub-1", conn=conn)

    assert [p["id"] for p in result["down_ports"]] == ["p1"]
    assert [a["id"] for a in result["dhcp_agents"]] == ["dhcp-1"]
    assert result["dhcp_agents"][0]["alive"] is False


def test_get_instance_connectivity_flags_down_port():
    vm = _server("i1", name="vm-1", status="ERROR")
    down_port = _port("p1", device_id="i1", network_id="net-1", status="DOWN")
    conn = _FakeConn(servers=[vm], ports=[down_port], networks=[_network("net-1")])

    result = network_health.get_instance_connectivity("i1", conn=conn)

    assert result["instance"]["status"] == "ERROR"
    assert result["ports"][0]["port_down"] is True


def test_get_instance_connectivity_reports_gateway_router_and_floating_ip():
    vm = _server("i1", name="vm-1", status="ACTIVE")
    port = _port(
        "p1", device_id="i1", network_id="net-1", status="ACTIVE",
        fixed_ips=[{"subnet_id": "sub-1", "ip_address": "10.0.0.5"}],
    )
    # get_instance_connectivity matches gateway_routers by the PORT's own
    # network_id against a router's external_gateway_info, so the router
    # needs to be gatewaying net-1 specifically for this test's intent.
    router = _router("r1", external_gateway_info={"network_id": "net-1"})
    fip = _fip("f1", router_id="r1", fixed_ip_address="10.0.0.5")
    conn = _FakeConn(
        servers=[vm], ports=[port], networks=[_network("net-1")],
        routers=[router], floating_ips=[fip],
    )

    result = network_health.get_instance_connectivity("i1", conn=conn)

    report = result["ports"][0]
    assert [r["id"] for r in report["gateway_routers"]] == ["r1"]
    assert [f["id"] for f in report["floating_ips"]] == ["f1"]


def test_get_instance_connectivity_instance_with_no_ports():
    vm = _server("i1", name="vm-1", status="ACTIVE")
    conn = _FakeConn(servers=[vm], ports=[])

    result = network_health.get_instance_connectivity("i1", conn=conn)

    assert result["ports"] == []


# --------------------------------------------------------------------
# Phase C (v0.10) -- candidate lists for network_resolver.py
# --------------------------------------------------------------------

def test_list_known_networks_subnets_instances():
    conn = _FakeConn(
        networks=[_network("net-1", name="sandbox-net")],
        subnets=[_subnet("sub-1", name="sandbox-subnet", cidr="10.0.0.0/24")],
        servers=[_server("i1", name="sandbox-vm-1")],
    )

    assert network_health.list_known_networks(conn=conn) == [{"id": "net-1", "name": "sandbox-net"}]
    assert network_health.list_known_subnets(conn=conn) == [
        {"id": "sub-1", "name": "sandbox-subnet", "cidr": "10.0.0.0/24"}
    ]
    assert network_health.list_known_instances(conn=conn) == [{"id": "i1", "name": "sandbox-vm-1"}]
