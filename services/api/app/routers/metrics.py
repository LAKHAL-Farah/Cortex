from fastapi import APIRouter

from app.services.metrics_collector import collect_metrics



router=APIRouter()



@router.get("/metrics")
def metrics():

    return collect_metrics()
