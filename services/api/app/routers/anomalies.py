from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from .. import models
from ..db import get_db

router = APIRouter(prefix="/api/v1/anomalies", tags=["anomalies"])

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
            "detected_at": r.detected_at.isoformat(),
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
            "detected_at": r.detected_at.isoformat(),
        }
        for r in rows
    ]