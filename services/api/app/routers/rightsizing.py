"""Roadmap 4.10 -- right-sizing recommendations API.

Read-only on purpose: there is no POST/PUT/DELETE here, and nothing this
router calls can modify a VM. Cortex *recommends*; the owner (or the
approval-gated remediation flow, if it ever gains a resize action) acts.
See services/rightsizing.py for how VMs are judged.
"""
import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from ..db import get_db
from ..services import rightsizing_service

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/rightsizing", tags=["rightsizing"])


@router.get("")
def get_rightsizing(
    window_days: int = Query(rightsizing_service.DEFAULT_WINDOW_DAYS, ge=1, le=rightsizing_service.MAX_WINDOW_DAYS),
    source: str = Query("auto", description="auto/live = real VM metrics; synthetic = deterministic demo fleet"),
    project_id: str | None = Query(None, description="Only VMs of this OpenStack project"),
    db: Session = Depends(get_db),
):
    """Under-utilised VMs with the suggested smaller size, the estimated
    reclaimable monthly cost, a risk label (low/medium/high) and an
    efficiency score (0-100). `available: false` + `reason` when no per-VM
    utilisation source exists -- never fabricated numbers."""
    try:
        return rightsizing_service.build_recommendations(db, window_days, source, project_id)
    except ValueError as exc:
        raise HTTPException(422, str(exc))
