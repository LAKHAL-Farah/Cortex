from fastapi import APIRouter

from app.services.metrics_collector import collect_metrics



router=APIRouter()



@router.get("/metrics")
def metrics():

    return collect_metrics()




@router.get("/api/v1/nodes/{instance}/history")
def node_history(instance: str, minutes: int = 60):
    return get_history(instance, minutes=minutes)