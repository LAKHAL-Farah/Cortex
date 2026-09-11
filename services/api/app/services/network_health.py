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
- **Instance/port health, scoped to that host as a hypervisor** (v0.10,
  Phase C) -- every instance whose `hypervisor_hostname` is this node,
  each with its own Neutron port(s) and whether any is down/disabled.
  Independent of the agent-scoped reads above -- a compute-only node
  commonly runs neither neutron-l3-agent nor neutron-dhcp-agent at all,
  but can still host instances with a broken port, and that's just as
  much this node's "network health" as a down agent is.

Deliberately **not** modeled here: security groups, floating-IP
association read directly off an instance's own port (covered instead by
the FloatingIP vertex's own `fixed_ip_address`/`router_id`, same as
topology_sync.py's graph) -- see the Phase C functions' own docstrings for
what each one scopes to.

v0.10 (Phase C, "make the network agent actually smarter") adds three
kinds of read this module didn't have before, now that Nova
instance/Neutron port data actually exists to read (openstack-sim's
SERVERS/PORTS, topology_sync.py's Phase 6 sync of the same data into the
graph):

1. Instance/port data folded into `get_node_network_health` above (the
   node-scoped case -- "which instances live on this node, which of their
   ports are down").
2. Three *entity*-scoped reads -- `get_network_instance_health`,
   `get_subnet_port_health`, `get_instance_connectivity` -- for questions
   that name a network, subnet, or instance instead of a physical node
   ("which VMs are on network X", "is anything down on subnet Z", "why
   can't instance Y reach the internet"). node_resolver.py has no way to
   answer these (it only matches physical hostnames); see
   agents/network_resolver.py for the parallel matching path that does,
   and nodes/network.py for how the agent picks between the two.
3. Three `list_known_*` functions that build the *candidate* lists
   network_resolver.py's matching tiers need (every known network/subnet/
   instance name) -- deliberately just id/name/cidr, not a health read,
   since resolving *which* entity a question means is a different job
   from reading that entity's health (the functions in bullet 2 do the
   latter, once resolution has already picked one).

Every function here follows the same rule the original agent-scoped reads
already do, forced by what openstack-sim's API surface actually supports
(see infra/openstack-sim/app.py -- only hypervisors get a per-id GET
route; everything else is list-only, filters ignored): always
`list(conn.X.Y())` the whole collection, then filter/index in Python.
Never `conn.network.get_network(id)` / `find_server()` / a server-side
`?network_id=` filter -- those aren't just a style preference here, they
would 404 or silently no-op against the sim this module is developed and
tested against.
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
        # Phase C addition -- Neutron's own "provider network" flag, same
        # field topology_sync.py's graph_networks already reads (as
        # `router_external`, off `is_router_external`). Needed by
        # get_instance_connectivity to tell "this network IS the external
        # one" apart from "this network needs a gatewayed router to reach
        # the external one" -- two different reasons an instance might (or
        # might not) have outside reachability.
        "router_external": getattr(network, "is_router_external", None),
    }


def _floating_ip_to_dict(fip) -> dict:
    return {
        "id": getattr(fip, "id", None),
        "floating_ip_address": getattr(fip, "floating_ip_address", None),
        "fixed_ip_address": getattr(fip, "fixed_ip_address", None),
        "status": getattr(fip, "status", None),
        "router_id": getattr(fip, "router_id", None),
    }


def _instance_to_dict(server) -> dict:
    """Phase C addition. `hypervisor_hostname` is openstacksdk's own
    mapping of Nova's `OS-EXT-SRV-ATTR:hypervisor_hostname` (see
    topology_sync.py's Phase 6 docstring on why this can legitimately come
    back None on a cloud that gates it admin-only) -- kept under that
    exact name here too, rather than renamed, so raw_data reads the same
    whichever module produced it.
    """
    return {
        "id": getattr(server, "id", None),
        "name": getattr(server, "name", None),
        "status": getattr(server, "status", None),
        "hypervisor_hostname": getattr(server, "hypervisor_hostname", None),
    }


def _port_to_dict(port) -> dict:
    """Phase C addition. Richer than the minimal shape
    topology_sync._sync_ports_to_graph writes into Neo4j (which flattens
    `fixed_ips` down to a single display string, since Neo4j properties
    can't hold a list of maps) -- this module has no such constraint, so
    the full `fixed_ips` list is kept, since get_instance_connectivity
    needs every fixed IP on a port, not just the first.
    """
    return {
        "id": getattr(port, "id", None),
        "name": getattr(port, "name", None),
        "status": getattr(port, "status", None),
        "admin_state_up": bool(getattr(port, "is_admin_state_up", False)),
        "mac_address": getattr(port, "mac_address", None),
        "device_id": getattr(port, "device_id", None),
        "device_owner": getattr(port, "device_owner", None),
        "network_id": getattr(port, "network_id", None),
        "fixed_ips": getattr(port, "fixed_ips", None) or [],
    }


def _is_port_down(port_dict: dict) -> bool:
    """A port is only genuinely healthy when *both* its operational status
    is ACTIVE and it's administratively enabled -- see port-down's catalog
    entry (openstack_expert_catalog.py) for why these are two independent
    failure modes, not one."""
    return port_dict["status"] != "ACTIVE" or not port_dict["admin_state_up"]


def get_node_network_health(hostname: str, conn=None) -> dict:
    """One host's worth of Neutron control-plane health: whichever agents
    run on it, plus (for an L3/DHCP agent) the specific routers/networks/
    floating IPs it's responsible for, plus (v0.10, Phase C) whichever
    instances it hosts as a hypervisor and each one's own port health.

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

    # Phase C -- instances hosted on this node, each with its own port(s).
    # A separate pass from the agent loop above (not folded into it): an
    # instance lives here because this node is its *hypervisor*, which has
    # nothing to do with which Neutron agents (if any) also happen to run
    # on this same host -- a compute-only node runs neither L3 nor DHCP,
    # but still hosts instances whose ports can be down.
    all_instances = list(conn.compute.servers())
    host_instances = [i for i in all_instances if getattr(i, "hypervisor_hostname", None) == hostname]

    all_ports = list(conn.network.ports())
    ports_by_device_id: dict[str, list] = {}
    for port in all_ports:
        device_id = getattr(port, "device_id", None)
        if device_id:
            ports_by_device_id.setdefault(device_id, []).append(port)

    instances: list[dict] = []
    for inst in host_instances:
        inst_dict = _instance_to_dict(inst)
        inst_ports = [_port_to_dict(p) for p in ports_by_device_id.get(inst_dict["id"], [])]
        inst_dict["ports"] = inst_ports
        inst_dict["has_down_port"] = any(_is_port_down(p) for p in inst_ports)
        instances.append(inst_dict)

    return {
        "hostname": hostname,
        "agents": [_agent_to_dict(a) for a in host_agents],
        "routers": routers,
        "networks": networks,
        "floating_ips": floating_ips,
        "instances": instances,
    }


# --------------------------------------------------------------------
# Phase C -- entity-scoped reads (network / subnet / instance, not node)
# --------------------------------------------------------------------

def get_network_instance_health(network_id: str, conn=None) -> dict:
    """Every VM-owned port on this network, plus its owning instance --
    'which VMs are on network X' / 'is anything down on network X'. Scoped
    by `port.network_id`, not by which node anything happens to run on --
    the network-first counterpart to get_node_network_health's node-first
    scoping, for exactly the question shapes a physical-host resolver
    can't answer (see agents/network_resolver.py's module docstring).

    Only `device_owner` starting with "compute:" counts as a VM port here
    -- same filter topology_sync.py's own HAS_PORT sync already applies --
    so a DHCP or router-interface port on this network doesn't show up as
    a phantom "instance".
    """
    conn = conn or _connect()

    all_ports = list(conn.network.ports())
    vm_ports = [
        p for p in all_ports
        if getattr(p, "network_id", None) == network_id
        and (getattr(p, "device_owner", None) or "").startswith("compute:")
    ]

    all_instances = {i.id: i for i in conn.compute.servers()}

    instances: list[dict] = []
    for port in vm_ports:
        port_dict = _port_to_dict(port)
        server = all_instances.get(port_dict["device_id"])
        instances.append({
            "instance": _instance_to_dict(server) if server is not None else None,
            "port": port_dict,
            "port_down": _is_port_down(port_dict),
        })

    return {"network_id": network_id, "instances": instances}


def get_subnet_port_health(subnet_id: str, conn=None) -> dict:
    """Every port with a fixed IP on this subnet, plus whether the DHCP
    agent hosting the subnet's own network is alive -- 'is anything down
    on subnet Z'. A subnet has no agents/routers of its own in Neutron's
    model (those attach at the network/router level), so "down" here
    means either a port on it (VM, DHCP, or router-interface alike -- an
    operator asking about a subnet cares about all of them, not just the
    VM-owned subset get_network_instance_health filters to) or the DHCP
    agent responsible for handing out its addresses.
    """
    conn = conn or _connect()

    all_ports = list(conn.network.ports())
    subnet_ports = [
        p for p in all_ports
        if any(fip.get("subnet_id") == subnet_id for fip in (getattr(p, "fixed_ips", None) or []))
    ]
    port_dicts = [_port_to_dict(p) for p in subnet_ports]

    all_subnets = {s.id: s for s in conn.network.subnets()}
    subnet = all_subnets.get(subnet_id)
    network_id = getattr(subnet, "network_id", None) if subnet is not None else None

    dhcp_agents: list[dict] = []
    if network_id:
        all_dhcp_agents = [a for a in conn.network.agents() if getattr(a, "agent_type", None) == "DHCP agent"]
        for agent in all_dhcp_agents:
            hosted = list(conn.network.dhcp_agent_hosting_networks(agent))
            if any(getattr(n, "id", None) == network_id for n in hosted):
                dhcp_agents.append(_agent_to_dict(agent))

    return {
        "subnet_id": subnet_id,
        "network_id": network_id,
        "ports": port_dicts,
        "down_ports": [p for p in port_dicts if _is_port_down(p)],
        "dhcp_agents": dhcp_agents,
    }


def get_instance_connectivity(instance_id: str, conn=None) -> dict:
    """One instance's own reachability chain -- 'why can't instance Y
    reach the internet': its port(s), each port's own health, the network
    each sits on, whether a router gateways that network onto an
    external/provider network, and any floating IP tied to one of the
    instance's fixed IPs.

    Deliberately walks the whole chain per port rather than stopping at
    the first hop -- a healthy port on a network with no external-
    gatewayed router is just as unreachable as a down port on a
    well-routed one, and those need completely different fixes. The
    reasoning about *which* of these actually explains "no internet"
    belongs to the caller (nodes/network.py) -- this function only reads
    and reports the chain, same "no LLM, no inference, in the data path"
    rule every other function in this module follows.
    """
    conn = conn or _connect()

    all_instances = {i.id: i for i in conn.compute.servers()}
    server = all_instances.get(instance_id)

    all_ports = list(conn.network.ports())
    instance_ports = [p for p in all_ports if getattr(p, "device_id", None) == instance_id]

    all_networks = {n.id: n for n in conn.network.networks()}
    all_routers = list(conn.network.routers())
    all_fips = list(conn.network.ips())

    port_reports = []
    for port in instance_ports:
        port_dict = _port_to_dict(port)
        network = all_networks.get(port_dict["network_id"])
        network_dict = _network_to_dict(network) if network is not None else None

        gateway_routers = [
            r for r in all_routers
            if (getattr(r, "external_gateway_info", None) or {}).get("network_id") == port_dict["network_id"]
        ]
        fixed_ip_addresses = {fip.get("ip_address") for fip in port_dict["fixed_ips"]}
        matching_fips = [f for f in all_fips if getattr(f, "fixed_ip_address", None) in fixed_ip_addresses]

        port_reports.append({
            "port": port_dict,
            "port_down": _is_port_down(port_dict),
            "network": network_dict,
            "network_is_external": bool(network_dict and network_dict.get("router_external")),
            "gateway_routers": [_router_to_dict(r) for r in gateway_routers],
            "floating_ips": [_floating_ip_to_dict(f) for f in matching_fips],
        })

    return {
        "instance_id": instance_id,
        "instance": _instance_to_dict(server) if server is not None else None,
        "ports": port_reports,
    }


# --------------------------------------------------------------------
# Phase C -- candidate lists for agents/network_resolver.py's matching
# tiers. Deliberately id/name(/cidr)-only: resolving *which* entity a
# question refers to is a different job from reading that entity's
# health, which the functions above do once resolution has already
# picked one.
# --------------------------------------------------------------------

def list_known_networks(conn=None) -> list[dict]:
    conn = conn or _connect()
    return [{"id": n.id, "name": getattr(n, "name", None)} for n in conn.network.networks()]


def list_known_subnets(conn=None) -> list[dict]:
    conn = conn or _connect()
    return [
        {"id": s.id, "name": getattr(s, "name", None), "cidr": getattr(s, "cidr", None)}
        for s in conn.network.subnets()
    ]


def list_known_instances(conn=None) -> list[dict]:
    conn = conn or _connect()
    return [{"id": i.id, "name": getattr(i, "name", None)} for i in conn.compute.servers()]


# --------------------------------------------------------------------
# v0.9 -- bulk scope lookup for the incident fan-out (agents/nodes/
# anomaly.py's _incident_scope_from_living_model). Deliberately a single
# no-argument bulk read (one `conn.network.agents()` call), not a
# per-hostname helper looped over every known node -- the same "cheap and
# fast enough to call unconditionally when scoping a broad incident
# question" property list_all_open_anomaly_flag_hostnames already has.
# --------------------------------------------------------------------

def list_hosts_with_down_agents(conn=None) -> list[str]:
    """Every hostname currently running at least one dead/disabled Neutron
    agent -- a down neutron-openvswitch-agent (or l3/dhcp-agent) is exactly
    as much "this node is worth investigating" as a scored metric anomaly
    is, and shouldn't have to wait on a coincidental CPU/RAM side effect
    (if there even is one) before a broad "is anything wrong" question
    picks it up. Mirrors `_check_neutron`'s own down_agents filter in
    nodes/network.py, just across every host at once instead of one."""
    conn = conn or _connect()
    down = {
        getattr(a, "host", None)
        for a in conn.network.agents()
        if not getattr(a, "is_alive", False) or not getattr(a, "is_admin_state_up", False)
    }
    down.discard(None)
    return sorted(down)
