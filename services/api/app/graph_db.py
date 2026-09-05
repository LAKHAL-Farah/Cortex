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
