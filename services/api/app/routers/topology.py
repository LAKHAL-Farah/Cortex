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
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, status
from neo4j.exceptions import Neo4jError, ServiceUnavailable
from sqlalchemy.orm import Session

from .. import crud, graph_db, schemas
from ..db import get_db
from ..services.topology_sync import sync_topology

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


def _resync_run_status(summary: dict) -> str:
    """Same classification main.py's periodic loop applies to a
    sync_topology() pass (see main.py::_topology_sync_status) -- duplicated
    rather than imported, since main.py imports this router (topology.py
    -> main.py would be circular). Kept in sync by hand, same reasoning
    rca_suggester.py's own duplicated _SEVERITY_RANK gives for why that's
    an acceptable tradeoff at this size.
    """
    if summary.get("complete_picture") and summary.get("network_topology_ok"):
        return "ok"
    return "degraded"


@router.post("/resync", response_model=schemas.TopologySyncRunOut)
def trigger_topology_resync(db: Session = Depends(get_db)):
    """Manually kick one immediate OpenStack topology-sync pass -- the same
    `sync_topology()` main.py's periodic loop already runs every
    TOPOLOGY_SYNC_INTERVAL_SECONDS -- for the "Reconverge" control in the
    web UI's topology top bar. Runs synchronously (a pass is a handful of
    OpenStack list calls, not a long-running job) and records the outcome
    to `topology_sync_runs` the exact same way the periodic loop does (see
    main.py::_run_periodic_recorded), so GET /health reflects a manual
    resync immediately and it's indistinguishable in history from a
    scheduled one.

    Deliberately does NOT also trigger a prometheus_health pass -- that
    loop runs every PROMETHEUS_HEALTH_SYNC_INTERVAL_SECONDS (30s by
    default, see main.py), fast enough that a manual OpenStack resync
    doesn't need to drag it along too.

    Returns the recorded run (200) even when the pass itself failed --
    the *request* to resync succeeded (we did attempt it and recorded
    what happened); the caller reads `status`/`error` on the body to tell
    a completed-but-degraded pass from an outright failure, same as
    /health already expects callers to do.
    """
    started_at = datetime.utcnow()
    summary: dict | None = None
    error: str | None = None
    try:
        summary = sync_topology(db)
        run_status = _resync_run_status(summary)
    except Exception as exc:
        logger.exception("manual topology resync failed")
        run_status = "failed"
        error = repr(exc)
    finished_at = datetime.utcnow()

    return crud.record_topology_sync_run(
        db,
        sync_type=schemas.SyncType.openstack.value,
        status=run_status,
        summary=summary,
        error=error,
        started_at=started_at,
        finished_at=finished_at,
    )
