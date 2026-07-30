from fastapi import APIRouter, HTTPException
import logging

from app.services.metrics_collector import collect_metrics, get_history


router = APIRouter()

logger = logging.getLogger(__name__)


@router.get("/metrics")
def metrics():
    return collect_metrics()


@router.get("/api/v1/nodes/{instance}/history")
def node_history(instance: str, minutes: int = 60):
    try:
        return get_history(instance, minutes=minutes)
    except Exception:
        logger.exception("error while fetching history for %s", instance)
        raise HTTPException(status_code=502, detail="failed to fetch node history from Prometheus")