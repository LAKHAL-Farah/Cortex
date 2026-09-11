"""Neo4j driver + schema bootstrap for the topology graph.

Mirrors db.py's role for Postgres: one place that owns the connection
config and is imported everywhere else that needs to talk to the graph.
The graph itself is a derived read-model (see
docs/architecture/adr-0002-topology-graph.md and the topology design doc)
-- Postgres/OpenStack remain the sources of truth, this is just where the
synced copy lives.
"""
import logging
import os
from typing import Any

from neo4j import GraphDatabase, Driver
from neo4j.time import DateTime as Neo4jDateTime

logger = logging.getLogger(__name__)

NEO4J_URI = os.environ.get("NEO4J_URI", "bolt://neo4j:7687")
NEO4J_USER = os.environ.get("NEO4J_USER", "neo4j")
NEO4J_PASSWORD = os.environ.get("NEO4J_PASSWORD", "")

# One driver, reused for the process lifetime -- the driver already pools
# connections internally, so there's no need for a session-per-request
# factory the way SessionLocal is for SQLAlchemy.
driver: Driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))

# One constraint per vertex label from the topology data model (see the
# design doc, sec. 5.3). IF NOT EXISTS makes this safe to run on every
# startup, not just the first one.
SCHEMA_CONSTRAINTS = [
    "CREATE CONSTRAINT node_id IF NOT EXISTS FOR (n:Node) REQUIRE n.id IS UNIQUE",
    "CREATE CONSTRAINT service_id IF NOT EXISTS FOR (s:Service) REQUIRE s.id IS UNIQUE",
    "CREATE CONSTRAINT network_id IF NOT EXISTS FOR (n:Network) REQUIRE n.id IS UNIQUE",
    "CREATE CONSTRAINT subnet_id IF NOT EXISTS FOR (s:Subnet) REQUIRE s.id IS UNIQUE",
    "CREATE CONSTRAINT router_id IF NOT EXISTS FOR (r:Router) REQUIRE r.id IS UNIQUE",
    "CREATE CONSTRAINT fip_id IF NOT EXISTS FOR (f:FloatingIP) REQUIRE f.id IS UNIQUE",
    # Phase 6 (topology_sync.py's instance/port sync) additions.
    "CREATE CONSTRAINT instance_id IF NOT EXISTS FOR (i:Instance) REQUIRE i.id IS UNIQUE",
    "CREATE CONSTRAINT port_id IF NOT EXISTS FOR (p:Port) REQUIRE p.id IS UNIQUE",
]


def apply_schema_constraints() -> None:
    """Idempotently (re)create the uniqueness constraints. Call once at
    startup (see main.py's lifespan) -- cheap no-op on every call after
    the first since constraints already exist.
    """
    with driver.session() as session:
        for statement in SCHEMA_CONSTRAINTS:
            session.run(statement)
    logger.info("topology graph: schema constraints applied")


def close_driver() -> None:
    driver.close()


# --------------------------------------------------------------------------
# Phase 5 (API) read helpers.
#
# Everything topology_sync.py/prometheus_health.py write above is a plain
# property graph over eight vertex labels (Node, Service, Network, Subnet,
# Router, FloatingIP, and, as of Phase 6, Instance and Port) and four
# relationship types (RUNS_ON, SERVES, CONNECTS, and, as of Phase 6,
# HAS_PORT) -- see docs/architecture/adr-0002-topology-graph.md and
# adr-0003-prometheus-cross-check.md. These functions are the read side of
# that same graph for routers/topology.py: no writes, no schema changes,
# just Cypher that mirrors the shapes the sync code above already
# produces. They use plain pattern comprehensions (`[(a)-[r]->(b) | ...]`)
# rather than `OPTIONAL MATCH` + `collect()` so a vertex with zero matching
# neighbors comes back as an empty list instead of a single all-null entry
# -- no APOC required, works on Neo4j 5 Community as already deployed
# (see infra/docker-compose.yml).
# --------------------------------------------------------------------------


def _serialize(value: Any) -> Any:
    """Recursively converts Neo4j-native values (temporal types, nested
    maps/lists as returned by properties()/pattern comprehensions) into
    plain JSON-serializable Python values. `last_synced_at` (set via
    Cypher's `datetime()` in every _sync_*_to_graph function) is the only
    temporal value in this graph today, but this walks any shape so a
    future property doesn't silently break serialization.
    """
    if isinstance(value, Neo4jDateTime):
        return value.iso_format()
    if isinstance(value, dict):
        return {k: _serialize(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_serialize(v) for v in value]
    return value


def fetch_graph() -> dict:
    """The whole topology graph, shaped for a generic graph-visualization
    client: a flat list of vertices (id/label/properties) and a flat list
    of directed edges (source/target/type). Every vertex label in this
    graph carries a stable `id` property (see adr-0002/adr-0003 and the
    SCHEMA_CONSTRAINTS above), so `properties(n).id` is always present.
    """
    with driver.session() as session:
        node_records = session.run(
            """
            MATCH (n)
            RETURN properties(n).id AS id, labels(n)[0] AS label, properties(n) AS properties
            ORDER BY label, id
            """
        )
        nodes = [
            {
                "id": record["id"],
                "label": record["label"],
                "properties": _serialize(record["properties"]),
            }
            for record in node_records
        ]

        edge_records = session.run(
            """
            MATCH (a)-[r]->(b)
            RETURN properties(a).id AS source, type(r) AS type, properties(b).id AS target
            ORDER BY type, source, target
            """
        )
        edges = [
            {"source": record["source"], "target": record["target"], "type": record["type"]}
            for record in edge_records
        ]

    return {"nodes": nodes, "edges": edges}


def fetch_vertex_detail(vertex_id: str) -> dict | None:
    """One vertex (any label) plus its immediate neighbors in both
    directions. Returns None if no vertex with that id exists -- the
    router turns that into a 404 rather than a 200 with an empty shell.
    """
    with driver.session() as session:
        result = session.run(
            """
            MATCH (n) WHERE n.id = $vertex_id
            RETURN properties(n) AS properties, labels(n)[0] AS label,
                   [(n)-[out]->(m) | {id: m.id, label: labels(m)[0], relationship: type(out), direction: 'outgoing'}] AS outgoing,
                   [(p)-[inc]->(n) | {id: p.id, label: labels(p)[0], relationship: type(inc), direction: 'incoming'}] AS incoming
            """,
            vertex_id=vertex_id,
        )
        record = result.single()
        if record is None:
            return None
        return {
            "id": vertex_id,
            "label": record["label"],
            "properties": _serialize(record["properties"]),
            "neighbors": _serialize(list(record["outgoing"]) + list(record["incoming"])),
        }


def fetch_services() -> list[dict]:
    """Every :Service vertex plus the id of the :Node it RUNS_ON (None for
    the rare unresolved-host placeholder case -- see
    topology_sync._register_new_hypervisor's caller and
    `unresolved_hosts` in sync_topology's summary).
    """
    with driver.session() as session:
        records = session.run(
            """
            MATCH (s:Service)
            RETURN properties(s) AS service,
                   [(s)-[:RUNS_ON]->(n:Node) | n.id][0] AS node_id
            ORDER BY s.id
            """
        )
        return [
            {**_serialize(record["service"]), "node_id": record["node_id"]}
            for record in records
        ]


def fetch_network_topology(network_id: str) -> dict | None:
    """One :Network's Horizon-style topology, purpose-shaped for the
    per-network diagram (see NetworkTopologyDiagram.tsx): this network's
    own properties, the :Router(s) gatewayed onto it, and each :Subnet
    carved from it with the VM-facing :Port(s) that sit on that subnet --
    each port's owning :Instance where one exists (None for a DHCP/
    router-owned port -- see topology_sync.py's Phase 6 docstring on why
    those get a Port vertex but no HAS_PORT edge) and, on that instance,
    its hypervisor host where visible (None if this cloud gates
    OS-EXT-SRV-ATTR:hypervisor_hostname behind an admin-only policy --
    same docstring).

    A purpose-built shape rather than a filtered view of fetch_graph()'s
    flat list, same reasoning fetch_networks() above already applies one
    level up: this is what one specific network's own diagram needs;
    fetch_networks() is what the /networks list view needs. Returns None
    if no :Network with that id exists, same 404-vs-empty-shell
    convention as fetch_vertex_detail.

    Uses Cypher map projections (`variable {.*, key: expr}`) rather than
    this module's usual `properties(x)` + manual dict-building -- plain
    openCypher, no APOC, same Neo4j-5-Community constraint as everywhere
    else in this file, just not previously needed here since nothing else
    nests four levels deep (network -> subnet -> port -> instance).
    """
    with driver.session() as session:
        result = session.run(
            """
            MATCH (net:Network) WHERE net.id = $network_id
            RETURN net {.*} AS network,
                   [(r:Router)-[:CONNECTS]->(net) | r {.*}] AS gateway_routers,
                   [(sub:Subnet)-[:CONNECTS]->(net) | sub {
                       .*,
                       ports: [(port:Port)-[:CONNECTS]->(sub) | port {
                           .*,
                           instance: head([(i:Instance)-[:HAS_PORT]->(port) | i {
                               .*,
                               hypervisor_hostname: head([(i)-[:RUNS_ON]->(n:Node) | n.id])
                           }])
                       }]
                   }] AS subnets
            """,
            network_id=network_id,
        )
        record = result.single()
        if record is None:
            return None
        return {
            **_serialize(record["network"]),
            "gateway_routers": _serialize(record["gateway_routers"]),
            "subnets": _serialize(record["subnets"]),
        }


def fetch_topology_map() -> dict:
    """Whole-topology, Horizon-style network map for
    NetworkTopologyCanvas.tsx's provider-vs-self-service view: every
    :Network (nested the same subnet -> port -> instance shape
    fetch_network_topology() builds for one network), plus every
    standalone :Router, plus enough router linkage on each network to
    place a router between the two sides it actually sits on:

    - `gateway_router_ids`: :Router(s) CONNECTS-ed onto this network --
      i.e. this network is that router's *external* gateway (the
      provider side, same edge fetch_network_topology's
      `gateway_routers` already reads one network at a time).
    - `interface_router_ids`: id of any :Router whose internal
      router-interface :Port sits on one of this network's subnets --
      i.e. this network is that router's *internal* side (the
      self-service side). Read off each port's own `device_id`/
      `device_owner` (see topology_sync.py's _sync_ports_to_graph
      docstring on why `device_id` is stored at all), filtered to
      "network:router_interface"-prefixed owners the same way
      _sync_instance_ports_to_graph filters the "compute:"-owned case
      down the hall -- Neutron uses that prefix for a plain router
      interface, an HA-router replica's interface, and a DVR interface
      alike, so a prefix match (not an exact one) is deliberate here.

    A network with neither list populated (Neutron's own `shared`/
    `router_external` flags both false and no router touches it at all)
    is a genuinely stranded self-service network -- the topology map
    still needs to render it as its own unattached trunk rather than
    dropping it, the same way NetworkTopologyDiagram.tsx already renders
    a single network with "No gateway router" instead of hiding it.

    Two flat queries (networks, then routers) rather than one nested
    one: a :Router with no gateway and no interface on anything synced
    yet (freshly created, mid-provisioning) would never appear in the
    first query's pattern comprehensions at all, and the topology map
    still wants to show it as an unattached router card rather than
    silently omitting it.
    """
    with driver.session() as session:
        network_records = session.run(
            """
            MATCH (net:Network)
            RETURN net {.*} AS network,
                   [(r:Router)-[:CONNECTS]->(net) | r.id] AS gateway_router_ids,
                   [(port:Port)-[:CONNECTS]->(:Subnet)-[:CONNECTS]->(net)
                       WHERE port.device_owner STARTS WITH 'network:router_interface'
                       | port.device_id] AS interface_router_ids,
                   [(sub:Subnet)-[:CONNECTS]->(net) | sub {
                       .*,
                       ports: [(port:Port)-[:CONNECTS]->(sub) | port {
                           .*,
                           instance: head([(i:Instance)-[:HAS_PORT]->(port) | i {
                               .*,
                               hypervisor_hostname: head([(i)-[:RUNS_ON]->(n:Node) | n.id])
                           }])
                       }]
                   }] AS subnets
            ORDER BY net.id
            """
        )
        networks = [
            {
                **_serialize(record["network"]),
                "gateway_router_ids": sorted(set(record["gateway_router_ids"])),
                "interface_router_ids": sorted({rid for rid in record["interface_router_ids"] if rid}),
                "subnets": _serialize(record["subnets"]),
            }
            for record in network_records
        ]

        router_records = session.run(
            """
            MATCH (r:Router)
            RETURN r {.*} AS router,
                   head([(r)-[:CONNECTS]->(net:Network) | net.id]) AS gateway_network_id
            ORDER BY r.id
            """
        )
        routers = [
            {**_serialize(record["router"]), "gateway_network_id": record["gateway_network_id"]}
            for record in router_records
        ]

        return {"networks": networks, "routers": routers}


def fetch_networks() -> list[dict]:
    """Every :Network vertex with its structural neighbors nested inline
    (subnets carved from it, routers gatewayed onto it, floating IPs
    carved from it, and the DHCP/L3 agent :Service vertices that SERVES
    it) -- see the CONNECTS/SERVES edges topology_sync.py builds in
    _sync_subnets_to_graph/_sync_router_gateways_to_graph/
    _sync_floating_ips_to_graph/_sync_dhcp_hosting_to_graph.
    """
    with driver.session() as session:
        records = session.run(
            """
            MATCH (net:Network)
            RETURN properties(net) AS network,
                   [(sub:Subnet)-[:CONNECTS]->(net) | properties(sub)] AS subnets,
                   [(r:Router)-[:CONNECTS]->(net) | properties(r)] AS gateway_routers,
                   [(fip:FloatingIP)-[:CONNECTS]->(net) | properties(fip)] AS floating_ips,
                   [(svc:Service)-[:SERVES]->(net) | properties(svc)] AS serving_agents
            ORDER BY net.id
            """
        )
        return [
            {
                **_serialize(record["network"]),
                "subnets": _serialize(record["subnets"]),
                "gateway_routers": _serialize(record["gateway_routers"]),
                "floating_ips": _serialize(record["floating_ips"]),
                "serving_agents": _serialize(record["serving_agents"]),
            }
            for record in records
        ]


def fetch_network_anomalies() -> dict:
    """Return the unhealthy router, floating IP, and port graph entries."""
    with driver.session() as session:
        routers_down = session.run(
            """
            MATCH (r:Router)
            WHERE r.status IS NOT NULL AND r.status <> 'ACTIVE'
            RETURN properties(r) AS router
            ORDER BY r.id
            """
        )
        floating_ips_orphaned = session.run(
            """
            MATCH (f:FloatingIP)
            WHERE NOT (f)-[:CONNECTS]->(:Router)
            RETURN properties(f) AS fip
            ORDER BY f.id
            """
        )
        ports_down = session.run(
            """
            MATCH (p:Port)
            WHERE p.status IS NOT NULL AND p.status <> 'ACTIVE'
            OPTIONAL MATCH (p)-[:CONNECTS]->(net:Network)
            OPTIONAL MATCH (p)-[:ATTACHED_TO]->(device)
            RETURN properties(p) AS port,
                   properties(net) AS network,
                   properties(device) AS device
            ORDER BY p.id
            """
        )

        return {
            "routers_down": [_serialize(record["router"]) for record in routers_down],
            "floating_ips_orphaned": [_serialize(record["fip"]) for record in floating_ips_orphaned],
            "ports_down": [
                {
                    **_serialize(record["port"]),
                    "network": _serialize(record["network"]) if record["network"] else None,
                    "device": _serialize(record["device"]) if record["device"] else None,
                }
                for record in ports_down
            ],
        }
