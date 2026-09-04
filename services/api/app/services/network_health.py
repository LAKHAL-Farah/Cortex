"""Neutron-side data gathering for the Network Agent (v0.9,
agents/nodes/network.py) -- router/floating-IP/agent health, scoped to one
node's Neutron agents (neutron-l3-agent, neutron-dhcp-agent,
neutron-openvswitch-agent), the same host-scoping every other per-node
check in this codebase uses (see topology_sync.py's Nova/Cinder/Neutron
`tagged_services` loop, keyed on the same `host` field).

Auth/connection: same `openstack.connect(cloud=OS_CLOUD)` pattern as
topology_sync.py's `_connect()` / quota_budget_monitor.py's `_connect()`
-- a third, independent OpenStack polling call site, kept in its own
module (rather than reusing topology_sync's periodic sync) so a slow or
failing on-demand network-agent question never blocks, or gets blocked
by, the periodic topology sync loop -- the same reasoning
quota_budget_monitor.py's own docstring already gives for keeping its own
`_connect()` rather than importing topology_sync's.

What this scopes to, and what it deliberately doesn't:

- **Neutron agent health** -- every agent (`conn.network.agents()`) whose
  `host` matches the node's hostname (neutron-openvswitch-agent on any
  compute host; neutron-l3-agent/neutron-dhcp-agent on whichever host
  actually runs them, typically the controller).
- **Router health, scoped to that host's L3 agent** -- if the host runs
  neutron-l3-agent, the specific routers *that agent* hosts
  (`conn.network.agent_hosted_routers`, same call topology_sync.py's
  SERVES-edge sync already uses), not every router in the project -- a
  router this host isn't responsible for isn't this host's network
  health.
- **Floating IP health, scoped to those same routers** -- any floating IP
  whose `router_id` is one of the routers just found, since a floating
  IP's reachability depends on the router that NATs it.
- **Network/DHCP health, scoped to that host's DHCP agent** -- if the
  host runs neutron-dhcp-agent, the specific networks it hosts DHCP for
  (`conn.network.dhcp_agent_hosting_networks`).

Deliberately **not** modeled here: literal Neutron "port" resources.
openstack-sim (infra/openstack-sim/app.py) doesn't expose a `/ports`
endpoint yet -- there's nothing real to read there, only something that
would have to be invented -- so "port health" for now is covered at the
layer this data actually supports: the agent that wires ports up
(neutron-openvswitch-agent) and the router/floating-IP layer above it.
Worth revisiting once port-level data exists to read.
"""
import logging
import os

import openstack

logger = logging.getLogger(__name__)

OS_CLOUD = os.environ.get("OS_CLOUD", "cortex-reader")


def _connect():
    """Thin wrapper so tests can monkeypatch the connection, same as
    topology_sync._connect() / quota_budget_monitor._connect()."""
    return openstack.connect(cloud=OS_CLOUD)


def _agent_to_dict(agent) -> dict:
    return {
        "id": getattr(agent, "id", None),
        "binary": getattr(agent, "binary", None),
        "agent_type": getattr(agent, "agent_type", None),
        "host": getattr(agent, "host", None),
        "alive": bool(getattr(agent, "is_alive", False)),
        "admin_state_up": bool(getattr(agent, "is_admin_state_up", False)),
    }


def _router_to_dict(router) -> dict:
    return {
        "id": getattr(router, "id", None),
        "name": getattr(router, "name", None),
        "status": getattr(router, "status", None),
        "admin_state_up": bool(getattr(router, "is_admin_state_up", False)),
    }


def _network_to_dict(network) -> dict:
    return {
        "id": getattr(network, "id", None),
        "name": getattr(network, "name", None),
        "status": getattr(network, "status", None),
        "admin_state_up": bool(getattr(network, "is_admin_state_up", False)),
    }


def _floating_ip_to_dict(fip) -> dict:
    return {
        "id": getattr(fip, "id", None),
        "floating_ip_address": getattr(fip, "floating_ip_address", None),
        "fixed_ip_address": getattr(fip, "fixed_ip_address", None),
        "status": getattr(fip, "status", None),
        "router_id": getattr(fip, "router_id", None),
    }


def get_node_network_health(hostname: str, conn=None) -> dict:
    """One host's worth of Neutron control-plane health: whichever agents
    run on it, plus (for an L3/DHCP agent) the specific routers/networks/
    floating IPs it's responsible for.

    Raises on a genuine connection/API failure rather than swallowing it --
    the caller (agents/nodes/network.py's `_check_neutron`) wraps this call
    in `resilience.get_breaker`, same as anomaly.py's `_check_logs` wraps
    its own Loki call, so a Neutron outage degrades that one node's
    finding rather than raising all the way out of the agent.
    """
    conn = conn or _connect()

    all_agents = list(conn.network.agents())
    host_agents = [a for a in all_agents if getattr(a, "host", None) == hostname]

    routers: list[dict] = []
    networks: list[dict] = []
    floating_ips: list[dict] = []

    for agent in host_agents:
        agent_type = getattr(agent, "agent_type", None)
        if agent_type == "L3 agent":
            hosted_routers = list(conn.network.agent_hosted_routers(agent))
            routers.extend(_router_to_dict(r) for r in hosted_routers)
            router_ids = {r.id for r in hosted_routers}
            if router_ids:
                all_fips = list(conn.network.ips())
                floating_ips.extend(
                    _floating_ip_to_dict(f)
                    for f in all_fips
                    if getattr(f, "router_id", None) in router_ids
                )
        elif agent_type == "DHCP agent":
            hosted_networks = list(conn.network.dhcp_agent_hosting_networks(agent))
            networks.extend(_network_to_dict(n) for n in hosted_networks)

    return {
        "hostname": hostname,
        "agents": [_agent_to_dict(a) for a in host_agents],
        "routers": routers,
        "networks": networks,
        "floating_ips": floating_ips,
    }
