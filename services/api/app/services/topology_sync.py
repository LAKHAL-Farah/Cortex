"""Phase 2 + Phase 3 of the topology-graph feature.

Phase 2: Nova hypervisors/services and Cinder services -> the `nodes` table
(Postgres) and (:Node)/(:Service)-[:RUNS_ON]->(:Node) (Neo4j).

Phase 3: Neutron networks/subnets/routers/floating IPs -> (:Network)/
(:Subnet)/(:Router)/(:FloatingIP), plus Neutron agents (same (:Service)
label as Nova/Cinder, RUNS_ON the same as Phase 2) and the DHCP/L3
hosting-endpoint calls (`dhcp_agent_hosting_networks`, `agent_hosted_routers`)
-> (:Service)-[:SERVES]->(:Network|:Router) edges. `CONNECTS` edges capture
the Neutron entities' own structural links to each other -- Subnet to its
Network, a Router's external gateway to its Network, a FloatingIP to its
Network and (if associated) its Router.

Both phases share one mark-and-sweep pass that removes graph vertices (and,
for the edges that can legitimately change target -- router gateways,
floating IP associations, agent hosting assignments -- the edges too) for
anything OpenStack no longer reports.

Phase 4 (`prometheus_health.py`, see
docs/architecture/adr-0003-prometheus-cross-check.md) builds on top of
this: it reads the `Service.openstack_state` this module writes below and
reconciles it against the Prometheus-observed health of the `Node` a
service `RUNS_ON` into `Service.state`. This module only ever writes
`openstack_state` (OpenStack's own raw report) -- `state` is owned by
Phase 4 and deliberately left untouched here.

This is the one and only OpenStack polling loop (see
docs/architecture/adr-0002-topology-graph.md, decision 3). It supersedes
openstack_discovery.discover_new_computes(): registering a previously-unseen
hypervisor as a `Node` row is still the same create_node ->
add_host_to_inventory -> install_node_exporter -> regenerate_file_sd
pipeline, it is just the first step of this pass instead of an independent
schedule writing to Postgres while a separate one writes to Neo4j.

Auth is whatever adr-0002 decided: `openstack.connect()` with no extra
arguments picks up OS_CLIENT_CONFIG_FILE/OS_CLOUD from the environment
(clouds.yaml mounted read-only, `cortex-reader` cloud, reader-only role) --
nothing here should ever need to pass credentials explicitly.
"""
import logging
import os
from typing import Any

import openstack
from sqlalchemy.orm import Session

from .. import crud, graph_db, schemas
from .ansible_runner import install_node_exporter
from .inventory_manager import add_host_to_inventory
from .prometheus_sd import regenerate_file_sd

logger = logging.getLogger(__name__)

# Matches the `OS_CLOUD` default already set for the api container in
# infra/docker-compose*.yml -- kept as a module default too so this is
# callable (e.g. from tests or a one-off shell) without depending on that.
OS_CLOUD = os.environ.get("OS_CLOUD", "cortex-reader")


def _connect():
    """Thin wrapper so tests can monkeypatch the connection without
    reaching into openstacksdk itself."""
    return openstack.connect(cloud=OS_CLOUD)


def _hypervisor_hostname(hv: Any) -> str | None:
    # openstacksdk maps the Nova `hypervisor_hostname` field onto the
    # resource's `.name` attribute -- keeping this as its own helper (rather
    # than inlining `hv.name`) documents that non-obvious mapping in one
    # place instead of at every call site.
    return getattr(hv, "name", None)


def _parse_cinder_host(raw_host: str) -> tuple[str, str | None]:
    """Cinder's `host` field is sometimes `hostname@backend-name` -- one
    physical host can run several backends (e.g. `storage@lvmdriver-1`).
    Split that off so the RUNS_ON edge points at the actual machine, not a
    synthetic `storage@lvmdriver-1` Node vertex. Nova service hosts are
    never suffixed this way, so this is only ever used for Cinder.
    """
    if "@" in raw_host:
        host, _, backend = raw_host.partition("@")
        return host, backend
    return raw_host, None


def _neutron_agent_service_id(binary: str, host: str) -> str:
    """Same `{binary}@{host}` scheme Nova/Cinder services already use (see
    the `id` built in the tagged_services loop below) -- kept as a named
    helper because the DHCP/L3 hosting-endpoint lookups need to compute the
    same id again afterward, from just the agent, to know which :Service
    vertex a SERVES edge should start from.
    """
    return f"{binary}@{host}"


def _gateway_network_id(router: Any) -> str | None:
    """A Router's `external_gateway_info` is a dict (or None if the router
    has no external gateway attached) -- pull the network id out of it once,
    here, rather than inlining the `.get()` at every call site.
    """
    info = getattr(router, "external_gateway_info", None)
    if not info:
        return None
    return info.get("network_id")


def _register_new_hypervisor(db: Session, hostname: str, ip_address: str) -> schemas.NodeOut | None:
    """Absorbed from openstack_discovery.discover_new_computes(): register
    a hypervisor Cortex hasn't seen before as a `Node` row, then run the
    same install/inventory/file_sd side effects a manually-added node would
    get. Returns the created Node, or None if registration failed (already
    logged) -- the caller falls back to treating the host as unresolved.
    """
    try:
        node = crud.create_node(
            db,
            schemas.NodeCreate(hostname=hostname, ip_address=ip_address, role="compute"),
        )
    except crud.DuplicateNodeError:
        # Raced with something else (manual add, a previous partial sync
        # pass) between the existing-nodes snapshot and this insert --
        # re-read rather than treat it as a failure.
        logger.warning("topology sync: %s already registered, re-reading", hostname)
        return crud.get_node_by_hostname(db, hostname)
    except Exception:
        logger.exception("topology sync: could not register new hypervisor %s", hostname)
        return None

    add_host_to_inventory(node.hostname, node.ip_address, "compute")
    if install_node_exporter(node.hostname):
        regenerate_file_sd(db)
    else:
        logger.warning(
            "topology sync: node_exporter install failed for %s; it is registered "
            "but Prometheus file_sd was not regenerated for it",
            node.hostname,
        )
    return node


def _empty_graph_node(hostname: str) -> dict:
    return {
        "id": hostname,
        "hostname": hostname,
        "ip_address": None,
        "role": None,
        "vcpus": None,
        "vcpus_used": None,
        "memory_mb": None,
        "memory_mb_used": None,
        "hypervisor_state": None,
        "hypervisor_status": None,
    }


def _sync_nodes_to_graph(session, nodes_by_id: dict[str, dict]) -> None:
    session.run(
        """
        UNWIND $nodes AS n
        MERGE (node:Node {id: n.id})
        SET node.hostname = n.hostname,
            node.ip_address = n.ip_address,
            node.role = n.role,
            node.vcpus = n.vcpus,
            node.vcpus_used = n.vcpus_used,
            node.memory_mb = n.memory_mb,
            node.memory_mb_used = n.memory_mb_used,
            node.hypervisor_state = n.hypervisor_state,
            node.hypervisor_status = n.hypervisor_status,
            node.last_synced_at = datetime()
        """,
        nodes=list(nodes_by_id.values()),
    )


def _sync_services_to_graph(session, services: list[dict]) -> None:
    # MATCH (not MERGE) on the Node side: every host a service reports
    # against was already added to $nodes in the same pass (see
    # sync_topology below), so RUNS_ON always has somewhere real to land
    # instead of silently creating a second, property-less Node vertex.
    #
    # `openstack_state`, not `state`: Phase 4 (prometheus_health.py, see
    # docs/architecture/adr-0003-prometheus-cross-check.md) owns `state`
    # as a value reconciled against the host's Prometheus-observed
    # health -- this pass only ever writes OpenStack's own raw, unmodified
    # report. Leaving `state` untouched here (rather than also setting it
    # as a placeholder) is deliberate: a stale reconciled value would be
    # actively misleading if left in place after a real disagreement, so
    # it's better left absent until the next Phase 4 pass computes it
    # fresh (adr-0003, consequences).
    session.run(
        """
        UNWIND $services AS svc
        MERGE (s:Service {id: svc.id})
        SET s.binary = svc.binary,
            s.host = svc.host,
            s.backend = svc.backend,
            s.source = svc.source,
            s.zone = svc.zone,
            s.status = svc.status,
            s.openstack_state = svc.state,
            s.last_synced_at = datetime()
        WITH s, svc
        MATCH (n:Node {id: svc.node_id})
        MERGE (s)-[:RUNS_ON]->(n)
        """,
        services=services,
    )


def _sync_networks_to_graph(session, networks: list[dict]) -> None:
    session.run(
        """
        UNWIND $networks AS net
        MERGE (n:Network {id: net.id})
        SET n.name = net.name,
            n.status = net.status,
            n.admin_state_up = net.admin_state_up,
            n.shared = net.shared,
            n.project_id = net.project_id,
            n.last_synced_at = datetime()
        """,
        networks=networks,
    )


def _sync_subnets_to_graph(session, subnets: list[dict]) -> None:
    # A subnet's network_id is fixed at creation time (a subnet can't be
    # moved to a different network), so a plain MERGE is enough here --
    # unlike the router-gateway/floating-IP-association edges below, there's
    # no "target changed" case to guard against, only "subnet/network
    # vanished", which the vertex sweep already handles.
    session.run(
        """
        UNWIND $subnets AS sub
        MERGE (s:Subnet {id: sub.id})
        SET s.name = sub.name,
            s.cidr = sub.cidr,
            s.ip_version = sub.ip_version,
            s.gateway_ip = sub.gateway_ip,
            s.last_synced_at = datetime()
        WITH s, sub
        MATCH (net:Network {id: sub.network_id})
        MERGE (s)-[:CONNECTS]->(net)
        """,
        subnets=subnets,
    )


def _sync_routers_to_graph(session, routers: list[dict]) -> None:
    session.run(
        """
        UNWIND $routers AS r
        MERGE (router:Router {id: r.id})
        SET router.name = r.name,
            router.status = r.status,
            router.admin_state_up = r.admin_state_up,
            router.project_id = r.project_id,
            router.last_synced_at = datetime()
        """,
        routers=routers,
    )


def _sync_router_gateways_to_graph(session, routers: list[dict]) -> None:
    """A router's external gateway can be attached, detached, or switched to
    a different external network at any time, so (unlike Subnet->Network)
    this can't be a plain MERGE -- that would leave a stale CONNECTS edge
    pointing at the old gateway network if it ever changes. Every router in
    `routers` (the full list synced this pass, not just the ones currently
    gatewayed) gets its existing outgoing CONNECTS->Network edge cleared
    first, then recreated if it currently has a gateway.
    """
    session.run(
        """
        UNWIND $routers AS r
        MATCH (router:Router {id: r.id})
        OPTIONAL MATCH (router)-[old:CONNECTS]->(:Network)
        DELETE old
        """,
        routers=routers,
    )
    gatewayed = [r for r in routers if r["gateway_network_id"]]
    if gatewayed:
        session.run(
            """
            UNWIND $routers AS r
            MATCH (router:Router {id: r.id})
            MATCH (net:Network {id: r.gateway_network_id})
            MERGE (router)-[:CONNECTS]->(net)
            """,
            routers=gatewayed,
        )


def _sync_floating_ips_to_graph(session, floating_ips: list[dict]) -> None:
    # floating_network_id is a required field (a floating IP is always
    # carved out of some external network) and, practically, never changes
    # after creation -- a plain MERGE is enough for this edge, same
    # reasoning as Subnet->Network above. The Router association is a
    # different story; see _sync_floating_ip_routers_to_graph.
    session.run(
        """
        UNWIND $fips AS fip
        MERGE (f:FloatingIP {id: fip.id})
        SET f.floating_ip_address = fip.floating_ip_address,
            f.fixed_ip_address = fip.fixed_ip_address,
            f.status = fip.status,
            f.last_synced_at = datetime()
        WITH f, fip
        MATCH (net:Network {id: fip.network_id})
        MERGE (f)-[:CONNECTS]->(net)
        """,
        fips=floating_ips,
    )


def _sync_floating_ip_routers_to_graph(session, floating_ips: list[dict]) -> None:
    """A floating IP's router association changes whenever it's
    disassociated/reassociated with a VM behind a different router, so this
    needs the same clear-then-recreate treatment as router gateways."""
    session.run(
        """
        UNWIND $fips AS fip
        MATCH (f:FloatingIP {id: fip.id})
        OPTIONAL MATCH (f)-[old:CONNECTS]->(:Router)
        DELETE old
        """,
        fips=floating_ips,
    )
    associated = [f for f in floating_ips if f["router_id"]]
    if associated:
        session.run(
            """
            UNWIND $fips AS fip
            MATCH (f:FloatingIP {id: fip.id})
            MATCH (r:Router {id: fip.router_id})
            MERGE (f)-[:CONNECTS]->(r)
            """,
            fips=associated,
        )


def _sync_dhcp_hosting_to_graph(session, agent_ids: list[str], hosting: list[dict]) -> None:
    """SERVES edges from a DHCP agent's :Service vertex to every :Network it
    currently hosts. `agent_ids` is every DHCP agent whose hosting call
    succeeded this pass (see sync_topology) -- each of those gets its
    existing outgoing SERVES->Network edges cleared before `hosting`
    (agent_id, network_id pairs) is applied, so a network the agent stopped
    hosting doesn't linger. An agent whose hosting call failed this tick is
    simply left out of `agent_ids`, so its edges from the last successful
    pass are untouched rather than wrongly cleared.
    """
    if agent_ids:
        session.run(
            """
            UNWIND $agent_ids AS aid
            MATCH (s:Service {id: aid})
            OPTIONAL MATCH (s)-[old:SERVES]->(:Network)
            DELETE old
            """,
            agent_ids=agent_ids,
        )
    if hosting:
        session.run(
            """
            UNWIND $hosting AS h
            MATCH (s:Service {id: h.service_id})
            MATCH (net:Network {id: h.network_id})
            MERGE (s)-[:SERVES]->(net)
            """,
            hosting=hosting,
        )


def _sync_l3_hosting_to_graph(session, agent_ids: list[str], hosting: list[dict]) -> None:
    """Same as _sync_dhcp_hosting_to_graph, but L3 agents SERVES Routers."""
    if agent_ids:
        session.run(
            """
            UNWIND $agent_ids AS aid
            MATCH (s:Service {id: aid})
            OPTIONAL MATCH (s)-[old:SERVES]->(:Router)
            DELETE old
            """,
            agent_ids=agent_ids,
        )
    if hosting:
        session.run(
            """
            UNWIND $hosting AS h
            MATCH (s:Service {id: h.service_id})
            MATCH (r:Router {id: h.router_id})
            MERGE (s)-[:SERVES]->(r)
            """,
            hosting=hosting,
        )


def _sweep_stale_services(session, seen_ids: set[str]) -> int:
    """Removes any :Service vertex not touched by this pass -- a service
    that no longer shows up in Nova's or Cinder's service list (binary
    uninstalled, host decommissioned, etc.). Only ever called when this
    pass's OpenStack listings all succeeded (see sync_topology) -- sweeping
    against a partial picture would delete vertices for services we simply
    failed to re-observe this tick, not ones that actually went away.
    """
    result = session.run(
        """
        MATCH (s:Service)
        WHERE NOT s.id IN $seen_ids
        WITH s
        DETACH DELETE s
        RETURN count(*) AS removed
        """,
        seen_ids=list(seen_ids),
    )
    record = result.single()
    return record["removed"] if record else 0


def _sweep_stale_nodes(session, seen_ids: set[str]) -> int:
    """Same as _sweep_stale_services, but for :Node vertices no longer
    reported as a hypervisor or as any service's host this pass."""
    result = session.run(
        """
        MATCH (n:Node)
        WHERE NOT n.id IN $seen_ids
        WITH n
        DETACH DELETE n
        RETURN count(*) AS removed
        """,
        seen_ids=list(seen_ids),
    )
    record = result.single()
    return record["removed"] if record else 0


def _sweep_stale_vertices(session, label: str, seen_ids: set[str]) -> int:
    """Generic mark-and-sweep for the Phase 3 vertex labels (Network,
    Subnet, Router, FloatingIP) -- same DETACH DELETE pattern as
    _sweep_stale_nodes/_sweep_stale_services, just parameterized on the
    label since Cypher can't take a label as a query parameter (it has to
    be interpolated into the query text, which is safe here because `label`
    only ever comes from the fixed calls in sync_topology below, never from
    OpenStack data).
    """
    result = session.run(
        f"""
        MATCH (v:{label})
        WHERE NOT v.id IN $seen_ids
        WITH v
        DETACH DELETE v
        RETURN count(*) AS removed
        """,
        seen_ids=list(seen_ids),
    )
    record = result.single()
    return record["removed"] if record else 0


def sync_topology(db: Session) -> dict:
    """One full pass: discover hypervisors/Nova services/Cinder services and
    Neutron networks/subnets/routers/floating IPs/agents from OpenStack,
    register any new hypervisor as a Postgres Node (see
    _register_new_hypervisor), upsert the current picture into the graph
    (Node/Service/Network/Subnet/Router/FloatingIP vertices, RUNS_ON/SERVES/
    CONNECTS edges), then mark-and-sweep -- delete any vertex (and, where a
    target can legitimately change over time, any edge) this pass didn't
    touch, so decommissioned hosts, removed services, and deleted Neutron
    resources don't linger in the graph forever.

    Safe to call on a fixed interval (see main.py) -- every upsert is a
    MERGE keyed on a stable id, so re-running with unchanged OpenStack
    state is a no-op beyond bumping last_synced_at, and the sweep only ever
    removes what this pass explicitly failed to see again.
    """
    conn = _connect()

    # Each listing call is independent and wrapped separately: an outage in
    # one OpenStack service (or a cloud that simply doesn't run it) shouldn't
    # take down the rest of the sync. `*_ok` tracks whether we got a
    # genuinely fresh, complete picture this pass -- see the mark-and-sweep
    # guards below.
    hypervisors: list = []
    nova_services: list = []
    cinder_services: list = []
    networks: list = []
    subnets: list = []
    routers: list = []
    floating_ips: list = []
    neutron_agents: list = []
    hypervisors_ok = nova_services_ok = cinder_services_ok = False
    networks_ok = subnets_ok = routers_ok = floating_ips_ok = neutron_agents_ok = False

    try:
        hypervisors = list(conn.compute.hypervisors(details=True))
        hypervisors_ok = True
    except Exception:
        logger.exception("topology sync: failed to list Nova hypervisors")

    try:
        nova_services = list(conn.compute.services())
        nova_services_ok = True
    except Exception:
        logger.exception("topology sync: failed to list Nova services")

    try:
        cinder_services = list(conn.block_storage.services())
        cinder_services_ok = True
    except Exception:
        logger.exception("topology sync: failed to list Cinder services")

    try:
        networks = list(conn.network.networks())
        networks_ok = True
    except Exception:
        logger.exception("topology sync: failed to list Neutron networks")

    try:
        subnets = list(conn.network.subnets())
        subnets_ok = True
    except Exception:
        logger.exception("topology sync: failed to list Neutron subnets")

    try:
        routers = list(conn.network.routers())
        routers_ok = True
    except Exception:
        logger.exception("topology sync: failed to list Neutron routers")

    try:
        floating_ips = list(conn.network.ips())
        floating_ips_ok = True
    except Exception:
        logger.exception("topology sync: failed to list Neutron floating IPs")

    try:
        neutron_agents = list(conn.network.agents())
        neutron_agents_ok = True
    except Exception:
        logger.exception("topology sync: failed to list Neutron agents")

    existing_by_hostname = {n.hostname: n for n in crud.list_nodes(db)}

    graph_nodes: dict[str, dict] = {}
    new_computes = 0

    for hv in hypervisors:
        hostname = _hypervisor_hostname(hv)
        if not hostname:
            logger.warning("topology sync: hypervisor %r has no hostname, skipping", getattr(hv, "id", "?"))
            continue

        pg_node = existing_by_hostname.get(hostname)
        if pg_node is None:
            pg_node = _register_new_hypervisor(db, hostname, getattr(hv, "host_ip", None))
            if pg_node is not None:
                existing_by_hostname[hostname] = pg_node
                new_computes += 1

        graph_node = _empty_graph_node(hostname)
        if pg_node is not None:
            graph_node["ip_address"] = pg_node.ip_address
            graph_node["role"] = pg_node.role
        else:
            graph_node["role"] = "compute"  # best guess: we know it's a hypervisor even if Postgres registration failed
        graph_node["vcpus"] = getattr(hv, "vcpus", None)
        graph_node["vcpus_used"] = getattr(hv, "vcpus_used", None)
        graph_node["memory_mb"] = getattr(hv, "memory_size", None)
        graph_node["memory_mb_used"] = getattr(hv, "memory_used", None)
        graph_node["hypervisor_state"] = getattr(hv, "state", None)
        graph_node["hypervisor_status"] = getattr(hv, "status", None)
        graph_nodes[hostname] = graph_node

    graph_services: list[dict] = []
    unresolved_hosts: set[str] = set()
    # agent.id (Neutron's own UUID) -> the {binary}@{host} id we give its
    # :Service vertex -- the DHCP/L3 hosting-endpoint calls below only have
    # the agent object to work from, and need this to know which :Service
    # vertex a SERVES edge starts from.
    neutron_service_id_by_agent_id: dict[str, str] = {}

    # Nova, Cinder, and Neutron agents all share the same shape (binary/
    # host/zone/status/state) once Cinder's optional `@backend` suffix is
    # peeled off and Neutron's is_alive/is_admin_state_up booleans are
    # mapped onto the same up/down + enabled/disabled vocabulary Nova/Cinder
    # already use, so all three sources feed the same loop -- tagged with
    # `source` so the graph can tell them apart.
    tagged_services = (
        [(svc, "nova") for svc in nova_services]
        + [(svc, "cinder") for svc in cinder_services]
        + [(svc, "neutron") for svc in neutron_agents]
    )

    for svc, source in tagged_services:
        raw_host = getattr(svc, "host", None)
        binary = getattr(svc, "binary", None)
        if not raw_host or not binary:
            logger.warning("topology sync: %s service %r missing host/binary, skipping", source, getattr(svc, "id", "?"))
            continue

        if source == "cinder":
            link_host, backend = _parse_cinder_host(raw_host)
        else:
            link_host, backend = raw_host, None

        if source == "neutron":
            # Agent resources don't have Nova/Cinder's status/state strings
            # -- they report is_admin_state_up/is_alive booleans instead.
            # Map them onto the same enabled/disabled + up/down vocabulary
            # so the graph doesn't need a different shape per source.
            status = "enabled" if getattr(svc, "is_admin_state_up", None) else "disabled"
            state = "up" if getattr(svc, "is_alive", None) else "down"
            zone = getattr(svc, "availability_zone", None)
            service_id = _neutron_agent_service_id(binary, raw_host)
            agent_id = getattr(svc, "id", None)
            if agent_id:
                neutron_service_id_by_agent_id[agent_id] = service_id
        else:
            status = getattr(svc, "status", None)
            state = getattr(svc, "state", None)
            zone = getattr(svc, "availability_zone", None)
            service_id = f"{binary}@{raw_host}"

        if link_host not in graph_nodes:
            # A service host we haven't seen as a hypervisor -- e.g.
            # nova-scheduler/nova-conductor and cinder-scheduler/
            # cinder-volume run on the controller/storage nodes, which are
            # never in the hypervisor list. Fall back to whatever Postgres
            # already knows about that hostname (seeded from file_sd, or
            # added manually); if it knows nothing either, write a bare
            # placeholder so the RUNS_ON edge still has somewhere to land
            # rather than dropping the service from the graph.
            pg_node = existing_by_hostname.get(link_host)
            graph_node = _empty_graph_node(link_host)
            if pg_node is not None:
                graph_node["ip_address"] = pg_node.ip_address
                graph_node["role"] = pg_node.role
            else:
                unresolved_hosts.add(link_host)
            graph_nodes[link_host] = graph_node

        graph_services.append(
            {
                "id": service_id,
                "binary": binary,
                "host": raw_host,
                "backend": backend,
                "source": source,
                "zone": zone,
                "status": status,
                "state": state,
                "node_id": link_host,
            }
        )

    if unresolved_hosts:
        logger.warning(
            "topology sync: %d service host(s) have no Postgres Node record and were "
            "graphed as bare placeholders: %s",
            len(unresolved_hosts),
            ", ".join(sorted(unresolved_hosts)),
        )

    # DHCP/L3 hosting-endpoint calls: one openstacksdk call per agent
    # (there's no bulk "hosting" listing), so each is wrapped individually.
    # A single agent's hosting call failing shouldn't discard every other
    # agent's edges -- `dhcp_synced_agent_ids`/`l3_synced_agent_ids` (only
    # the agents whose call succeeded this pass) is what gates the
    # clear-then-recreate in _sync_dhcp_hosting_to_graph/
    # _sync_l3_hosting_to_graph, so a failed agent's edges from the last
    # successful pass are simply left alone rather than wrongly cleared.
    dhcp_hosting: list[dict] = []
    l3_hosting: list[dict] = []
    dhcp_synced_agent_ids: list[str] = []
    l3_synced_agent_ids: list[str] = []

    for agent in neutron_agents:
        agent_id = getattr(agent, "id", None)
        service_id = neutron_service_id_by_agent_id.get(agent_id)
        if not service_id:
            continue  # already logged above as missing host/binary

        agent_type = getattr(agent, "agent_type", None)
        if agent_type == "DHCP agent":
            try:
                hosted_networks = list(conn.network.dhcp_agent_hosting_networks(agent))
            except Exception:
                logger.exception("topology sync: failed to list networks hosted by DHCP agent %s", service_id)
                continue
            dhcp_synced_agent_ids.append(service_id)
            for net in hosted_networks:
                dhcp_hosting.append({"service_id": service_id, "network_id": net.id})
        elif agent_type == "L3 agent":
            try:
                hosted_routers = list(conn.network.agent_hosted_routers(agent))
            except Exception:
                logger.exception("topology sync: failed to list routers hosted by L3 agent %s", service_id)
                continue
            l3_synced_agent_ids.append(service_id)
            for router in hosted_routers:
                l3_hosting.append({"service_id": service_id, "router_id": router.id})

    graph_networks = [
        {
            "id": net.id,
            "name": getattr(net, "name", None),
            "status": getattr(net, "status", None),
            "admin_state_up": getattr(net, "is_admin_state_up", None),
            "shared": getattr(net, "is_shared", None),
            "project_id": getattr(net, "project_id", None),
        }
        for net in networks
    ]
    graph_subnets = [
        {
            "id": sub.id,
            "name": getattr(sub, "name", None),
            "cidr": getattr(sub, "cidr", None),
            "ip_version": getattr(sub, "ip_version", None),
            "gateway_ip": getattr(sub, "gateway_ip", None),
            "network_id": getattr(sub, "network_id", None),
        }
        for sub in subnets
    ]
    graph_routers = [
        {
            "id": router.id,
            "name": getattr(router, "name", None),
            "status": getattr(router, "status", None),
            "admin_state_up": getattr(router, "is_admin_state_up", None),
            "project_id": getattr(router, "project_id", None),
            "gateway_network_id": _gateway_network_id(router),
        }
        for router in routers
    ]
    graph_floating_ips = [
        {
            "id": fip.id,
            "floating_ip_address": getattr(fip, "floating_ip_address", None),
            "fixed_ip_address": getattr(fip, "fixed_ip_address", None),
            "status": getattr(fip, "status", None),
            "network_id": getattr(fip, "floating_network_id", None),
            "router_id": getattr(fip, "router_id", None),
        }
        for fip in floating_ips
    ]

    # Only trust this pass to sweep if every listing it depends on actually
    # succeeded -- a partial picture (e.g. Cinder unreachable this tick)
    # must never be used to delete vertices that are still real, just
    # un-observed this time around. Node/Service sweep depends on every
    # source that can populate either (hypervisors and all three service
    # sources, since Neutron agents can register placeholder Nodes exactly
    # like Nova/Cinder services do). Network/Subnet/Router/FloatingIP sweep
    # is its own independent guard -- a Neutron outage shouldn't block
    # Nova/Cinder's sweep, and vice versa.
    complete_picture = hypervisors_ok and nova_services_ok and cinder_services_ok and neutron_agents_ok
    network_topology_ok = networks_ok and subnets_ok and routers_ok and floating_ips_ok
    swept_nodes = 0
    swept_services = 0
    swept_networks = 0
    swept_subnets = 0
    swept_routers = 0
    swept_floating_ips = 0

    with graph_db.driver.session() as session:
        if graph_nodes:
            _sync_nodes_to_graph(session, graph_nodes)
        if graph_services:
            _sync_services_to_graph(session, graph_services)

        if graph_networks:
            _sync_networks_to_graph(session, graph_networks)
        if graph_subnets:
            _sync_subnets_to_graph(session, graph_subnets)
        if graph_routers:
            _sync_routers_to_graph(session, graph_routers)
            _sync_router_gateways_to_graph(session, graph_routers)
        if graph_floating_ips:
            _sync_floating_ips_to_graph(session, graph_floating_ips)
            _sync_floating_ip_routers_to_graph(session, graph_floating_ips)
        _sync_dhcp_hosting_to_graph(session, dhcp_synced_agent_ids, dhcp_hosting)
        _sync_l3_hosting_to_graph(session, l3_synced_agent_ids, l3_hosting)

        if complete_picture:
            swept_services = _sweep_stale_services(session, {s["id"] for s in graph_services})
            swept_nodes = _sweep_stale_nodes(session, set(graph_nodes.keys()))
        else:
            logger.warning(
                "topology sync: skipping node/service mark-and-sweep this pass -- incomplete "
                "picture (hypervisors_ok=%s, nova_services_ok=%s, cinder_services_ok=%s, "
                "neutron_agents_ok=%s)",
                hypervisors_ok, nova_services_ok, cinder_services_ok, neutron_agents_ok,
            )

        if network_topology_ok:
            swept_networks = _sweep_stale_vertices(session, "Network", {n["id"] for n in graph_networks})
            swept_subnets = _sweep_stale_vertices(session, "Subnet", {s["id"] for s in graph_subnets})
            swept_routers = _sweep_stale_vertices(session, "Router", {r["id"] for r in graph_routers})
            swept_floating_ips = _sweep_stale_vertices(session, "FloatingIP", {f["id"] for f in graph_floating_ips})
        else:
            logger.warning(
                "topology sync: skipping network-topology mark-and-sweep this pass -- "
                "incomplete picture (networks_ok=%s, subnets_ok=%s, routers_ok=%s, "
                "floating_ips_ok=%s)",
                networks_ok, subnets_ok, routers_ok, floating_ips_ok,
            )

    logger.info(
        "topology sync: %d hypervisor(s), %d Nova service(s), %d Cinder service(s), "
        "%d Neutron agent(s), %d network(s), %d subnet(s), %d router(s), %d floating IP(s), "
        "%d new compute node(s) registered, %d unresolved service host(s), "
        "%d stale node(s)/%d stale service(s)/%d stale network(s)/%d stale subnet(s)/"
        "%d stale router(s)/%d stale floating IP(s) swept",
        len(hypervisors), len(nova_services), len(cinder_services),
        len(neutron_agents), len(networks), len(subnets), len(routers), len(floating_ips),
        new_computes, len(unresolved_hosts), swept_nodes, swept_services,
        swept_networks, swept_subnets, swept_routers, swept_floating_ips,
    )

    return {
        "hypervisors": len(hypervisors),
        "nova_services": len(nova_services),
        "cinder_services": len(cinder_services),
        "neutron_agents": len(neutron_agents),
        "networks": len(networks),
        "subnets": len(subnets),
        "routers": len(routers),
        "floating_ips": len(floating_ips),
        "dhcp_hosting_edges": len(dhcp_hosting),
        "l3_hosting_edges": len(l3_hosting),
        "new_computes": new_computes,
        "graph_nodes": len(graph_nodes),
        "graph_services": len(graph_services),
        "unresolved_hosts": len(unresolved_hosts),
        "complete_picture": complete_picture,
        "network_topology_ok": network_topology_ok,
        "swept_nodes": swept_nodes,
        "swept_services": swept_services,
        "swept_networks": swept_networks,
        "swept_subnets": swept_subnets,
        "swept_routers": swept_routers,
        "swept_floating_ips": swept_floating_ips,
    }
