"""Phase 2 of the topology-graph feature: Nova hypervisors/services and
Cinder services -> the `nodes` table (Postgres) and
(:Node)/(:Service)-[:RUNS_ON]->(:Node) (Neo4j), with a mark-and-sweep pass
that removes graph vertices for anything OpenStack no longer reports.

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
            s.state = svc.state,
            s.last_synced_at = datetime()
        WITH s, svc
        MATCH (n:Node {id: svc.node_id})
        MERGE (s)-[:RUNS_ON]->(n)
        """,
        services=services,
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


def sync_topology(db: Session) -> dict:
    """One full pass: discover hypervisors/Nova services/Cinder services
    from OpenStack, register any new hypervisor as a Postgres Node (see
    _register_new_hypervisor), upsert the current picture into the graph as
    Node/Service vertices and RUNS_ON edges, then mark-and-sweep -- delete
    any Node/Service vertex this pass didn't touch, so decommissioned hosts
    and removed services don't linger in the graph forever.

    Safe to call on a fixed interval (see main.py) -- every upsert is a
    MERGE keyed on a stable id, so re-running with unchanged OpenStack
    state is a no-op beyond bumping last_synced_at, and the sweep only ever
    removes what this pass explicitly failed to see again.
    """
    conn = _connect()

    # Each listing call is independent and wrapped separately: a Cinder
    # outage (or a cloud that simply doesn't run Cinder) shouldn't take
    # down Nova's half of the sync, and a Nova failure shouldn't take down
    # Cinder's. `*_ok` tracks whether we got a genuinely fresh, complete
    # picture this pass -- see the mark-and-sweep guard below.
    hypervisors: list = []
    nova_services: list = []
    cinder_services: list = []
    hypervisors_ok = nova_services_ok = cinder_services_ok = False

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

    # Nova and Cinder services share the same shape (binary/host/zone/
    # status/state) once Cinder's optional `@backend` suffix is peeled off,
    # so both sources feed the same loop -- tagged with `source` so the
    # graph can tell them apart.
    tagged_services = [(svc, "nova") for svc in nova_services] + [(svc, "cinder") for svc in cinder_services]

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
                "id": f"{binary}@{raw_host}",
                "binary": binary,
                "host": raw_host,
                "backend": backend,
                "source": source,
                "zone": getattr(svc, "availability_zone", None),
                "status": getattr(svc, "status", None),
                "state": getattr(svc, "state", None),
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

    # Only trust this pass to sweep if every listing it depends on actually
    # succeeded -- a partial picture (e.g. Cinder unreachable this tick)
    # must never be used to delete vertices that are still real, just
    # un-observed this time around.
    complete_picture = hypervisors_ok and nova_services_ok and cinder_services_ok
    swept_nodes = 0
    swept_services = 0

    with graph_db.driver.session() as session:
        if graph_nodes:
            _sync_nodes_to_graph(session, graph_nodes)
        if graph_services:
            _sync_services_to_graph(session, graph_services)

        if complete_picture:
            swept_services = _sweep_stale_services(session, {s["id"] for s in graph_services})
            swept_nodes = _sweep_stale_nodes(session, set(graph_nodes.keys()))
        else:
            logger.warning(
                "topology sync: skipping mark-and-sweep this pass -- incomplete picture "
                "(hypervisors_ok=%s, nova_services_ok=%s, cinder_services_ok=%s)",
                hypervisors_ok, nova_services_ok, cinder_services_ok,
            )

    logger.info(
        "topology sync: %d hypervisor(s), %d Nova service(s), %d Cinder service(s), "
        "%d new compute node(s) registered, %d unresolved service host(s), "
        "%d stale node(s)/%d stale service(s) swept",
        len(hypervisors), len(nova_services), len(cinder_services),
        new_computes, len(unresolved_hosts), swept_nodes, swept_services,
    )

    return {
        "hypervisors": len(hypervisors),
        "nova_services": len(nova_services),
        "cinder_services": len(cinder_services),
        "new_computes": new_computes,
        "graph_nodes": len(graph_nodes),
        "graph_services": len(graph_services),
        "unresolved_hosts": len(unresolved_hosts),
        "complete_picture": complete_picture,
        "swept_nodes": swept_nodes,
        "swept_services": swept_services,
    }
