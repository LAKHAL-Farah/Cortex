from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from .. import crud
from ..db import get_db
from ..services.forecast_service import (
    get_forecast,
    get_threshold_warning,
    list_threshold_warnings,
)

router = APIRouter(prefix="/api/v1/forecast", tags=["forecast"])


@router.get("/{hostname}/{metric_name}")
def forecast(
    hostname: str,
    metric_name: str,
    horizon_days: int = 7,
    db: Session = Depends(get_db),
):
    """`horizon_days` (2.8: "Extend forecast horizon to 30/90 days") selects
    how far out to forecast, 1-90 days; the served horizon set widens (fewer,
    more spread-out points) beyond 7 days -- see
    forecast_features.build_serve_horizons(). Defaults to 7 for callers that
    predate this."""
    if not (1 <= horizon_days <= 90):
        raise HTTPException(status_code=400, detail="horizon_days doit être compris entre 1 et 90")

    node = crud.get_node_by_hostname(db, hostname)
    if node is None:
        raise HTTPException(status_code=404, detail="Nœud inconnu")

    result = get_forecast(node.ip_address, metric_name, horizon_days=horizon_days)
    if result is None:
        raise HTTPException(status_code=404, detail="Pas assez de données pour ce nœud/métrique")

    # On renvoie le hostname logique demandé, pas l'IP interne utilisée
    # pour retrouver le fichier modèle/dataset — le frontend n'a pas besoin
    # de le savoir. Everything else (model_type, forecast[], actual[], ...)
    # comes straight from forecast_service.get_forecast().
    result["hostname"] = hostname
    return result


@router.get("/{hostname}/{metric_name}/threshold")
def forecast_threshold(
    hostname: str,
    metric_name: str,
    threshold: float | None = None,
    horizon_days: int | None = None,
    db: Session = Depends(get_db),
):
    """First-passage-time ETA for one node/metric: 'X will hit threshold in
    ~N days' (2.5), computed from the same forecast trajectory GET
    /{hostname}/{metric_name} serves. `threshold` defaults per-metric
    (forecast_service.DEFAULT_THRESHOLDS) but can be overridden, e.g.
    `?threshold=80`. `horizon_days` (2.8) extends how far out the ETA search
    looks (up to 90 days) instead of the default 7."""
    if horizon_days is not None and not (1 <= horizon_days <= 90):
        raise HTTPException(status_code=400, detail="horizon_days doit être compris entre 1 et 90")

    node = crud.get_node_by_hostname(db, hostname)
    if node is None:
        raise HTTPException(status_code=404, detail="Nœud inconnu")

    warning = get_threshold_warning(node.ip_address, metric_name, threshold, horizon_days=horizon_days)
    if warning is None:
        raise HTTPException(
            status_code=404,
            detail="Pas assez de données, ou pas de seuil défini pour cette métrique",
        )

    warning["hostname"] = hostname
    return warning


@router.get("/warnings")
def forecast_warnings(threshold: float | None = None, db: Session = Depends(get_db)):
    """Fleet-wide scan backing the dashboard's threshold-breach banner (2.5):
    every (node, metric) pair actually projected to cross its threshold
    within the served 7-day forecast horizon, soonest first. Resources not
    heading toward their threshold are left out entirely -- an empty list
    here means "nothing to warn about", not "no data"."""
    nodes = crud.list_nodes(db)
    pairs = [(n.hostname, n.ip_address) for n in nodes]
    return list_threshold_warnings(pairs, threshold=threshold)
