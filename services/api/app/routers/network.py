"""Story 3.6: read-only network-health panel -- router/floating-IP/port
anomalies (from the topology graph, see graph_db.fetch_network_anomalies)
plus a live inter-node latency pass (network_latency.py). Deliberately
separate from routers/topology.py: that module exposes the full graph
structure (healthy and unhealthy alike); this is specifically the
condensed "what's wrong right now" read the panel needs.
"""
import logging

from fastapi import APIRouter, Depends
from neo4j.exceptions import Neo4jError, ServiceUnavailable
from sqlalchemy.orm import Session

from .. import graph_db, schemas
from ..db import get_db
from ..services.network_latency import measure_node_latencies

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/network", tags=["network"])


@router.get("/health", response_model=schemas.NetworkHealthOut)
def get_network_health(db: Session = Depends(get_db)):
    """Router/floating-IP/port anomalies plus current inter-node latency,
    condensed into one panel-ready response (story 3.6 acceptance
    criterion: "Network agent panel shows real Neutron-sourced status").

    Anomaly detection reads the topology graph (already kept in sync by
    topology_sync.py's periodic pass, see routers/topology.py) rather than
    calling OpenStack directly -- consistent with adr-0002's "one and only
    OpenStack polling loop" decision. Latency, unlike the graph read, is a
    live TCP-timing pass each call -- it isn't something topology_sync.py
    tracks, and it's cheap enough (a handful of TCP handshakes) to measure
    fresh on every request rather than caching it.
    """
    try:
        anomalies = graph_db.fetch_network_anomalies()
        graph_available = True
    except (Neo4jError, ServiceUnavailable):
        logger.exception("network health: graph query failed")
        anomalies = {"routers_down": [], "floating_ips_orphaned": [], "ports_down": []}
        graph_available = False

    latencies = measure_node_latencies(db)

    has_anomalies = bool(
        anomalies["routers_down"] or anomalies["floating_ips_orphaned"] or anomalies["ports_down"]
    )
    has_unreachable = any(not entry["reachable"] for entry in latencies)
    status = "degraded" if (not graph_available or has_anomalies or has_unreachable) else "ok"

    return schemas.NetworkHealthOut(
        status=status,
        graph_available=graph_available,
        routers_down=anomalies["routers_down"],
        floating_ips_orphaned=anomalies["floating_ips_orphaned"],
        ports_down=anomalies["ports_down"],
        latencies=latencies,
    )
