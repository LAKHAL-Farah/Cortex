import logging
from datetime import timezone

from fastapi import APIRouter, Depends, HTTPException, status
from neo4j.exceptions import Neo4jError, ServiceUnavailable
from sqlalchemy.orm import Session
from .. import models
from ..db import get_db
from ..services import alert_correlation, rca_suggester

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/anomalies", tags=["anomalies"])


def _graph_unavailable(exc: Exception) -> HTTPException:
    """Same pattern as routers/topology.py's _graph_unavailable -- this
    endpoint depends on Neo4j being up even though it's mounted under the
    anomalies router (it's a derived view *of* anomalies, not of the
    graph itself), so it needs the same failure handling.
    """
    logger.exception("anomalies API: Neo4j query failed (RCA suggestions)")
    return HTTPException(
        status.HTTP_503_SERVICE_UNAVAILABLE,
        "topology graph is temporarily unavailable",
    )


def _iso_utc(dt) -> str | None:
    """Serialize a stored datetime as an unambiguous UTC ISO string.

    detected_at/started_at/resolved_at are stored as naive UTC (see
    datetime.utcnow() in anomaly_detector.py) so dt.isoformat() alone
    produces a string with no timezone marker, e.g. "2026-08-01T14:32:10".
    A browser's `new Date(...)` treats a marker-less string as LOCAL time,
    not UTC, so every timestamp silently shifted by the viewer's UTC offset
    -- in UTC+1 that meant a just-detected anomaly always showed "1h ago".
    Attaching tzinfo explicitly before formatting fixes that everywhere,
    regardless of what timezone the browser is in.
    """
    if dt is None:
        return None
    return dt.replace(tzinfo=timezone.utc).isoformat()


@router.get("")
def list_anomalies(db: Session = Depends(get_db)):
    """Retourne toutes les anomalies actives (severity != normal)."""
    rows = db.query(models.AnomalyFlag).filter(models.AnomalyFlag.severity != "normal").all()
    return [
        {
            "hostname": r.hostname,
            "metric_name": r.metric_name,
            "current_value": r.current_value,
            "z_score": r.z_score,
            "severity": r.severity,
            "method": r.method,
            "baseline_n": r.baseline_n,
            "detected_at": _iso_utc(r.detected_at),
        }
        for r in rows
    ]


@router.get("/history")
def list_anomaly_history(hostname: str | None = None, limit: int = 200, db: Session = Depends(get_db)):
    """Past anomaly episodes (resolved and still-active), newest first.

    Backs the Alerts > History page: unlike /api/v1/anomalies (which only
    ever shows the current state per host/metric), this reads from
    AnomalyEvent, which keeps one row per episode instead of overwriting it.
    """
    query = db.query(models.AnomalyEvent)
    if hostname:
        query = query.filter(models.AnomalyEvent.hostname == hostname)
    rows = query.order_by(models.AnomalyEvent.started_at.desc()).limit(min(limit, 1000)).all()
    return [
        {
            "id": str(r.id),
            "hostname": r.hostname,
            "metric_name": r.metric_name,
            "current_value": r.current_value,
            "z_score": r.z_score,
            "severity": r.severity,
            "method": r.method,
            "baseline_n": r.baseline_n,
            "started_at": _iso_utc(r.started_at),
            "resolved_at": _iso_utc(r.resolved_at),
            "is_active": r.resolved_at is None,
        }
        for r in rows
    ]


@router.get("/incidents")
def list_anomaly_incidents(db: Session = Depends(get_db)):
    """Phase 6: open anomalies grouped into incidents via the topology
    graph, instead of one unrelated row per (hostname, metric_name).

    Every open alert from GET /api/v1/anomalies comes back exactly once,
    nested under an incident -- an alert with no correlated peer is
    still its own incident, just with `member_count: 1`, so the web app
    can group by `incident_id` uniformly rather than special-casing
    "ungrouped" (see AlertsView.tsx). /api/v1/anomalies and /history stay
    exactly as they are: this endpoint is a grouped *view* over the same
    AnomalyFlag rows, not a replacement for them.

    Degrades to one incident per alert (identical to today's ungrouped
    behavior) if the topology graph itself is unreachable, rather than
    a 503 -- see alert_correlation.build_incidents's docstring. Postgres
    alerting has to keep working even when Neo4j or its sync loop is
    down.
    """
    return alert_correlation.build_incidents(db)


@router.get("/rca")
def list_rca_suggestions(db: Session = Depends(get_db)):
    """Basic causal RCA suggestions ("X caused Y") over currently-open
    alerts: for every pair of currently-anomalous, graph-adjacent
    vertices, one templated sentence naming the connecting topology
    relationship. See services/rca_suggester.py and
    docs/architecture/plan-rca-causal-suggestion.md.

    Route order note: this must stay declared above `/{hostname}` below,
    or FastAPI matches that catch-all first and `/api/v1/anomalies/rca`
    gets swallowed as `hostname="rca"`.

    Depends on Neo4j the same way /incidents' underlying graph reads do
    -- unlike /incidents (which degrades to ungrouped alerts if the graph
    is unreachable), this endpoint has nothing meaningful to return
    without the graph, so it surfaces a 503 instead of a silent empty
    list, matching every graph-backed endpoint in routers/topology.py.
    """
    try:
        return rca_suggester.find_causal_suggestions(db)
    except (Neo4jError, ServiceUnavailable) as exc:
        raise _graph_unavailable(exc) from exc


@router.get("/{hostname}")
def get_anomaly(hostname: str, db: Session = Depends(get_db)):
    rows = db.query(models.AnomalyFlag).filter_by(hostname=hostname).all()
    return [
        {
            "metric_name": r.metric_name,
            "current_value": r.current_value,
            "z_score": r.z_score,
            "severity": r.severity,
            "method": r.method,
            "baseline_n": r.baseline_n,
            "detected_at": _iso_utc(r.detected_at),
        }
        for r in rows
    ]