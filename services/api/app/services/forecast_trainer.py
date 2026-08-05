import csv
import os
import pickle
import logging
from collections import defaultdict

from statsmodels.tsa.holtwinters import Holt
from statsmodels.tsa.arima.model import ARIMA

logger = logging.getLogger(__name__)

DATASET_PATH = os.getenv("FORECAST_DATASET_PATH", "/app/forecast_dataset.csv")
MODELS_DIR = os.getenv("FORECAST_MODELS_DIR", "/app/models")

BEST_MODEL_PER_METRIC = {
    "cpu_percent": "arima",
    "memory_percent": "holt",
    "disk_percent": "holt",
}

MIN_POINTS_REQUIRED = 20


def load_series_by_host_metric(dataset_path: str) -> dict:
    series = defaultdict(list)
    with open(dataset_path, "r", newline="") as f:
        reader = csv.DictReader(f)
        rows = sorted(reader, key=lambda r: r["timestamp"])
        for row in rows:
            key = (row["hostname"], row["metric"])
            series[key].append(float(row["value"]))
    return series


def train_all_models() -> None:
    if not os.path.isfile(DATASET_PATH):
        logger.warning("Dataset introuvable (%s), entraînement ignoré", DATASET_PATH)
        return

    os.makedirs(MODELS_DIR, exist_ok=True)
    series = load_series_by_host_metric(DATASET_PATH)

    trained_count = 0
    for (hostname, metric_name), values in series.items():
        model_type = BEST_MODEL_PER_METRIC.get(metric_name)
        if model_type is None or len(values) < MIN_POINTS_REQUIRED:
            continue

        try:
            if model_type == "holt":
                fitted = Holt(values).fit()
                # Holt : léger nativement, pas besoin d'allègement particulier
                payload = {"type": "holt", "model": fitted}

            elif model_type == "arima":
                fitted = ARIMA(values, order=(2, 1, 2)).fit()
                # ARIMA : on ne garde que les paramètres appris + le dernier
                # segment de la série (nécessaire pour initialiser les
                # prédictions futures), pas tout l'historique complet.
                payload = {
                    "type": "arima",
                    "params": fitted.params,
                    "order": (2, 1, 2),
                    # ARIMA a besoin d'un minimum d'observations récentes pour
                    # démarrer une prédiction (dépend de l'ordre p,d,q) --
                    # on garde large (100 derniers points) pour rester sûr.
                    "last_observations": values[-100:],
                }

        except Exception as exc:
            logger.error("Échec entraînement %s/%s: %s", hostname, metric_name, exc)
            continue

        safe_hostname = hostname.replace(".", "_")
        filename = os.path.join(MODELS_DIR, f"{metric_name}_{safe_hostname}.pkl")
        with open(filename, "wb") as f:
            pickle.dump(payload, f)
        trained_count += 1

    logger.info("Entraînement terminé : %d modèles sauvegardés", trained_count)


if __name__ == "__main__":
    train_all_models()
