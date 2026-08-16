import logging
from datetime import timezone

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from .. import models
from ..db import get_db
from ..services.quota_budget_monitor import check_quota_and_budget

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/quotas", tags=["quotas"])

_SEVERITY_RANK = {"critical": 2, "warning": 1, "normal": 0}


def _iso_utc(dt) -> str | None:
    """Same fix as routers/anomalies.py::_iso_utc -- detected_at is stored
    naive-UTC, so it needs an explicit tzinfo before .isoformat() or a
    browser's `new Date(...)` reads it as local time."""
    if dt is None:
        return None
    return dt.replace(tzinfo=timezone.utc).isoformat()


def _serialize(row: models.QuotaAlert) -> dict:
    return {
        "project_id": row.project_id,
        "project_name": row.project_name,
        "breach_type": row.breach_type,  # "capacity_cap" | "budget_cap"
        "resource": row.resource,
        "used": row.used,
        "limit": row.limit,
        "ratio": row.ratio,
        "severity": row.severity,
        "message": row.message,
        "detected_at": _iso_utc(row.detected_at),
    }


@router.get("/alerts")
def list_quota_alerts(db: Session = Depends(get_db)):
    """Currently-breached quota/budget slots (severity != "normal"),
    most severe first. Each row's `breach_type` and `message` say
    explicitly whether this is a `capacity_cap` (OpenStack quota) or a
    `budget_cap` (estimated spend) breach -- see
    services/quota_budget_monitor.py's module docstring for why those
    are kept distinct rather than folded into one generic alert.
    """
    rows = (
        db.query(models.QuotaAlert)
        .filter(models.QuotaAlert.severity != "normal")
        .all()
    )
    rows.sort(key=lambda r: _SEVERITY_RANK.get(r.severity, 0), reverse=True)
    return [_serialize(r) for r in rows]


@router.get("/alerts/{project_id}")
def get_project_quota_alerts(project_id: str, db: Session = Depends(get_db)):
    """Every checked slot for one project, including ones currently
    "normal" -- lets a UI show full quota/budget headroom for a project,
    not just what's actively breached.
    """
    rows = db.query(models.QuotaAlert).filter_by(project_id=project_id).all()
    return [_serialize(r) for r in rows]


@router.post("/resync")
def trigger_quota_resync(db: Session = Depends(get_db)):
    """Manual trigger for check_quota_and_budget(), same intent as
    POST /api/v1/topology/resync -- run a pass immediately instead of
    waiting for the periodic loop's next tick.
    """
    summary = check_quota_and_budget(db)
    return {"status": "ok", "summary": summary}
