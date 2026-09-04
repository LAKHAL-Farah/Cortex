"""Tests for services/network_health.py -- Neutron control-plane health
scoped to one host's agents. Same faking style as test_topology_sync.py:
a tiny SimpleNamespace stand-in for openstacksdk resources, and a fake
`conn.network` namespace passed straight into get_node_network_health
(no need to monkeypatch _connect() at all, since the function accepts an
optional `conn`)."""
import types

from app.services import network_health


def _agent(id, binary, host, agent_type, is_alive=True, is_admin_state_up=True):
    return types.SimpleNamespace(
        id=id, binary=binary, host=host, agent_type=agent_type,
        is_alive=is_alive, is_admin_state_up=is_admin_state_up,
    )


def _router(id, name="router", status="ACTIVE", is_admin_state_up=True):
    return types.SimpleNamespace(id=id, name=name, status=status, is_admin_state_up=is_admin_state_up)


def _network(id, name="net", status="ACTIVE", is_admin_state_up=True):
    return types.SimpleNamespace(id=id, name=name, status=status, is_admin_state_up=is_admin_state_up)


def _fip(id, router_id, status="ACTIVE", floating_ip_address="203.0.113.1", fixed_ip_address="10.0.0.5"):
    return types.SimpleNamespace(
        id=id, router_id=router_id, status=status,
        floating_ip_address=floating_ip_address, fixed_ip_address=fixed_ip_address,
    )


class _FakeConn:
    def __init__(self, agents=None, l3_hosting=None, dhcp_hosting=None, floating_ips=None):
        self._agents = agents or []
        self._l3_hosting = l3_hosting or {}
        self._dhcp_hosting = dhcp_hosting or {}
        self._floating_ips = floating_ips or []

        def _agents_fn(**kw):
            return iter(self._agents)

        def _agent_hosted_routers(agent, **kw):
            return iter(self._l3_hosting.get(agent.id, []))

        def _dhcp_agent_hosting_networks(agent, **kw):
            return iter(self._dhcp_hosting.get(agent.id, []))

        def _ips(**kw):
            return iter(self._floating_ips)

        self.network = types.SimpleNamespace(
            agents=_agents_fn,
            agent_hosted_routers=_agent_hosted_routers,
            dhcp_agent_hosting_networks=_dhcp_agent_hosting_networks,
            ips=_ips,
        )


def test_scopes_agents_to_the_requested_host_only():
    ovs_here = _agent("a1", "neutron-openvswitch-agent", "compute1-sim", "Open vSwitch agent")
    ovs_elsewhere = _agent("a2", "neutron-openvswitch-agent", "compute2-sim", "Open vSwitch agent")
    conn = _FakeConn(agents=[ovs_here, ovs_elsewhere])

    health = network_health.get_node_network_health("compute1-sim", conn=conn)

    assert [a["id"] for a in health["agents"]] == ["a1"]
    assert health["routers"] == []
    assert health["networks"] == []
    assert health["floating_ips"] == []


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
    }
