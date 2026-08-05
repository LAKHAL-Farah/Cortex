import os
import pickle
import logging
import numpy as np
from statsmodels.tsa.holtwinters import Holt
from statsmodels.tsa.arima.model import ARIMA

logger = logging.getLogger(__name__)

MODELS_DIR = os.getenv("FORECAST_MODELS_DIR", "/app/models")

STEPS_PER_HORIZON = {
    "tomorrow": 288,
    "7_days": 2016,
    "30_days": 8640,
}


def load_model(hostname: str, metric_name: str):
    safe_hostname = hostname.replace(".", "_")
    filename = os.path.join(MODELS_DIR, f"{metric_name}_{safe_hostname}.pkl")

    if not os.path.isfile(filename):
        return None

    with open(filename, "rb") as f:
        return pickle.load(f)


def get_forecast(hostname: str, metric_name: str) -> dict | None:
    payload = load_model(hostname, metric_name)
    if payload is None:
        logger.warning("Aucun modèle trouvé pour %s / %s", hostname, metric_name)
        return None

    max_steps = max(STEPS_PER_HORIZON.values())

    if payload["type"] == "holt":
        full_forecast = payload["model"].forecast(max_steps)

    elif payload["type"] == "arima":
        model = ARIMA(payload["last_observations"], order=payload["order"])
        fitted = model.filter(payload["params"])
        full_forecast = fitted.forecast(max_steps)

    else:
        return None

    result = {"hostname": hostname, "metric": metric_name, "forecast": []}
    for label, steps in STEPS_PER_HORIZON.items():
        value = float(np.asarray(full_forecast)[steps - 1])
        result["forecast"].append({"day": label, "value": round(value, 2)})

    return result
