from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from .. import crud
from ..db import get_db
from ..services.metrics_collector import collect_metrics

router = APIRouter(prefix="/api/v1/dashboard", tags=["dashboard"])


@router.get("")
def get_dashboard(db: Session = Depends(get_db)):
    nodes = crud.list_nodes(db)
    live_by_instance = {m["instance"]: m for m in collect_metrics()}

    result = []
    for n in nodes:
        instance = f"{n.ip_address}:{n.exporter_port}"
        live = live_by_instance.get(instance)
        result.append({
            "id": str(n.id),
            "hostname": n.hostname,
            "ip_address": n.ip_address,
            "role": n.role,
            "exporter_port": n.exporter_port,
            "is_active": n.is_active,
            "instance": instance,
            "has_metrics": live is not None,
            "metrics": live,  # None until Prometheus has scraped it at least once
        })
    return result