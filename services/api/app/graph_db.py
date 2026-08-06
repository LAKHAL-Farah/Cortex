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

from neo4j import GraphDatabase, Driver

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
