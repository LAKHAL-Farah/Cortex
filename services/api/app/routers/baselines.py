from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from .. import models
from ..db import get_db

router = APIRouter(prefix="/api/v1/baselines", tags=["baselines"])


@router.get("/{hostname}")
def get_baseline(hostname: str, metric_name: str, db: Session = Depends(get_db)):
    """Returns the full (weekday, hour) baseline curve for one node/metric.

    `baseline_builder.compute_baselines()` has populated the `baselines` table
    hourly since the 1.6 work (see main.py's periodic task), but nothing
    previously read it back out over the API -- anomaly_detector.py queries
    it directly for scoring, but there was no way for a client (e.g. the 1.7
    dashboard, or a curl check of the 1.8 acceptance criterion) to see the
    curve itself. This is that missing read path.

    median/mad is the primary (robust) estimator per ADR-0001; mean/stddev
    is included alongside it for comparison/debugging, not as the source of
    truth for anomaly scoring.
    """
    rows = (
        db.query(models.Baseline)
        .filter_by(hostname=hostname, metric_name=metric_name)
        .order_by(models.Baseline.weekday, models.Baseline.hour)
        .all()
    )
    return [
        {
            "weekday": r.weekday,
            "hour": r.hour,
            "median": r.median,
            "mad": r.mad,
            "mean": r.mean,
            "stddev": r.stddev,
            "sample_count": r.sample_count,
            "updated_at": r.updated_at.isoformat() if r.updated_at else None,
        }
        for r in rows
    ]
