from datetime import timezone

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from .. import models
from ..db import get_db

router = APIRouter(prefix="/api/v1/anomalies", tags=["anomalies"])


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