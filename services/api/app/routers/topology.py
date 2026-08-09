"""Phase 5 of the topology-graph feature: read-only HTTP surface over the
graph topology_sync.py (Phases 2/3) and prometheus_health.py (Phase 4)
build in Neo4j, plus a sync-health endpoint backed by the new
`topology_sync_runs` Postgres table (see models.TopologySyncRun).

Every endpoint here is read-only -- nothing in this module writes to
Neo4j or Postgres. The graph itself stays a derived read-model (see
graph_db.py's module docstring); this is just the HTTP-facing read path
onto it that nothing exposed before Phase 5.
"""
import logging

from fastapi import APIRouter, Depends, HTTPException, status
from neo4j.exceptions import Neo4jError, ServiceUnavailable
from sqlalchemy.orm import Session

from .. import crud, graph_db, schemas
from ..db import get_db

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/topology", tags=["topology"])

# Higher = worse. Used by get_topology_health to reduce two independent
# sync loops' latest-run statuses down to one overall status.
_STATUS_SEVERITY = {"ok": 0, "unknown": 1, "degraded": 2, "failed": 3}


def _graph_unavailable(exc: Exception) -> HTTPException:
    logger.exception("topology API: Neo4j query failed")
    return HTTPException(
        status.HTTP_503_SERVICE_UNAVAILABLE,
        "topology graph is temporarily unavailable",
    )


@router.get("/graph", response_model=schemas.TopologyGraphOut)
def get_topology_graph():
    """The full topology graph (every Node/Service/Network/Subnet/Router/
    FloatingIP vertex, every RUNS_ON/SERVES/CONNECTS edge), flattened for
    a generic graph-visualization client. See graph_db.fetch_graph.
    """
    try:
        return graph_db.fetch_graph()
    except (Neo4jError, ServiceUnavailable) as exc:
        raise _graph_unavailable(exc) from exc


@router.get("/services", response_model=list[schemas.TopologyServiceOut])
def list_topology_services():
    """Every :Service vertex (Nova/Cinder/Neutron-agent), with the id of
    the :Node it RUNS_ON. Includes both `openstack_state` (Phase 2/3's raw
    report) and `state` (Phase 4's Prometheus-reconciled value) -- see
    adr-0003-prometheus-cross-check.md for why the two can disagree.
    """
    try:
        return graph_db.fetch_services()
    except (Neo4jError, ServiceUnavailable) as exc:
        raise _graph_unavailable(exc) from exc


@router.get("/networks", response_model=list[schemas.TopologyNetworkOut])
def list_topology_networks():
    """Every :Network vertex with its structural neighbors nested inline:
    the :Subnet(s) carved from it, the :Router(s) gatewayed onto it, the
    :FloatingIP(s) carved from it, and the DHCP/L3 agent :Service(s) that
    SERVES it. See graph_db.fetch_networks.
    """
    try:
        return graph_db.fetch_networks()
    except (Neo4jError, ServiceUnavailable) as exc:
        raise _graph_unavailable(exc) from exc


@router.get("/nodes/{vertex_id}", response_model=schemas.TopologyVertexDetailOut)
def get_topology_vertex(vertex_id: str):
    """One vertex of any label (a hypervisor :Node, a :Service, a
    :Network, ...) plus its immediate neighbors in both directions.
    `vertex_id` matches the graph's own `id` property -- a hostname for
    :Node, `{binary}@{host}` for :Service, and the OpenStack resource UUID
    for :Network/:Subnet/:Router/:FloatingIP (see topology_sync.py).
    """
    try:
        vertex = graph_db.fetch_vertex_detail(vertex_id)
    except (Neo4jError, ServiceUnavailable) as exc:
        raise _graph_unavailable(exc) from exc
    if vertex is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no such vertex in the topology graph")
    return vertex


@router.get("/health", response_model=schemas.TopologyHealthOut)
def get_topology_health(db: Session = Depends(get_db)):
    """Sync-loop health, backed by the `topology_sync_runs` table main.py
    writes to after every pass of either periodic job (see
    _run_periodic_recorded in main.py) -- NOT a live Neo4j query. This is
    deliberate: the graph itself can look perfectly fine (last successful
    pass's data still sitting there) even if the sync loop that produces
    it has been silently failing for an hour, so this endpoint answers
    "is the sync healthy" from actual run history instead of guessing
    from a snapshot of the graph.
    """
    syncs: dict[str, schemas.TopologySyncRunOut | None] = {}
    worst = "ok"
    for sync_type in (schemas.SyncType.openstack, schemas.SyncType.prometheus_health):
        run = crud.get_latest_topology_sync_run(db, sync_type.value)
        if run is None:
            syncs[sync_type.value] = None
            run_status = "unknown"
        else:
            syncs[sync_type.value] = schemas.TopologySyncRunOut.model_validate(run)
            run_status = run.status
        if _STATUS_SEVERITY[run_status] > _STATUS_SEVERITY[worst]:
            worst = run_status

    return schemas.TopologyHealthOut(status=worst, syncs=syncs)
