"""Roadmap 4.11 -- on-demand professional PDF reports.

GET /api/v1/reports                     -> the available variants
GET /api/v1/reports/{variant}.pdf       -> the PDF (executive|financial|technical)

Unlike the weekly digest (emailed on a schedule) these are generated when
asked for, for any past or current cost period. See services/reports.py for
what feeds each variant and how a failing data source degrades.
"""
import logging

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy.orm import Session

from ..db import get_db
from ..services import reports

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/reports", tags=["reports"])


@router.get("")
def list_reports():
    return {"variants": reports.list_variants()}


@router.get("/{variant}.pdf")
def get_report_pdf(
    variant: str,
    period: str | None = Query(None, description="Cost period YYYY-MM (default: current month)"),
    ai: bool = Query(False, description="Executive variant only: LLM-written summary (falls back to a deterministic one)"),
    rightsizing_source: str = Query("auto", description="auto/live = real VM metrics; synthetic = labelled demo fleet"),
    db: Session = Depends(get_db),
):
    if variant not in reports.VARIANTS:
        raise HTTPException(404, f"Unknown report '{variant}'. Available: {', '.join(reports.VARIANTS)}")
    try:
        pdf, filename = reports.render_report(db, variant, period, use_ai=ai, rightsizing_source=rightsizing_source)
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    except Exception:
        logger.exception("report %s failed to render", variant)
        raise HTTPException(500, "The report could not be generated. Check the API logs.")
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"', "Cache-Control": "no-store"},
    )
