from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from .. import crud
from ..db import get_db
from ..services.forecast_service import get_forecast

router = APIRouter(prefix="/api/v1/forecast", tags=["forecast"])


@router.get("/{hostname}/{metric_name}")
def forecast(hostname: str, metric_name: str, db: Session = Depends(get_db)):
    node = crud.get_node_by_hostname(db, hostname)
    if node is None:
        raise HTTPException(status_code=404, detail="Nœud inconnu")

    result = get_forecast(node.ip_address, metric_name)
    if result is None:
        raise HTTPException(status_code=404, detail="Aucun modèle disponible pour ce nœud/métrique")

    # On renvoie le hostname logique demandé, pas l'IP interne utilisée
    # pour retrouver le fichier modèle — le frontend n'a pas besoin de le savoir.
    result["hostname"] = hostname
    return result
