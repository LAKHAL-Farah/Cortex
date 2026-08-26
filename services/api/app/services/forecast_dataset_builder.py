import csv
import logging
import os
from datetime import datetime, timedelta
import requests

logger = logging.getLogger(__name__)

PROMETHEUS_URL = os.getenv("PROMETHEUS_BASE_URL", "http://prometheus:9090")    
LOOKBACK_DAYS = 14  # utilisé seulement pour le tout premier run (bootstrap)
RETENTION_DAYS = 90  # combien de temps on garde dans le CSV avant purge

METRIC_QUERIES = {
    "cpu_percent":    '100 - (avg by(instance) (rate(node_cpu_seconds_total{mode="idle"}[5m])) * 100)',
    "memory_percent": '100 * (1 - node_memory_MemAvailable_bytes / node_memory_MemTotal_bytes)',
    "disk_percent":   '100 * (1 - node_filesystem_avail_bytes{mountpoint="/"} / node_filesystem_size_bytes{mountpoint="/"})',
}

FIELDNAMES = ["timestamp", "hostname", "metric", "value"]


def fetch_range(promql_query: str, start: datetime, end: datetime, step: str = "5m") -> list[dict]:
    resp = requests.get(
        f"{PROMETHEUS_URL}/api/v1/query_range",
        params={
            "query": promql_query,
            "start": start.timestamp(),
            "end": end.timestamp(),
            "step": step,
        },
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()["data"]["result"]


def get_last_timestamp(output_path: str) -> datetime | None:
    if not os.path.isfile(output_path):
        return None
    last_ts = None
    with open(output_path, "r", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            ts = datetime.fromisoformat(row["timestamp"])
            if last_ts is None or ts > last_ts:
                last_ts = ts
    return last_ts


def purge_old_rows(output_path: str, cutoff: datetime) -> None:
    """Réécrit le CSV en ne gardant que les lignes plus récentes que `cutoff`.
    Empêche le fichier de grossir indéfiniment au-delà de RETENTION_DAYS."""
    if not os.path.isfile(output_path):
        return

    kept_rows = []
    with open(output_path, "r", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if datetime.fromisoformat(row["timestamp"]) >= cutoff:
                kept_rows.append(row)

    with open(output_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
        writer.writeheader()
        writer.writerows(kept_rows)

    logger.info("Purge effectuée : %d lignes conservées (cutoff=%s)", len(kept_rows), cutoff.isoformat())


def build_dataset(output_path: str) -> None:
    """Récupère les nouvelles données depuis le dernier point collecté,
    les ajoute au CSV, puis purge tout ce qui dépasse RETENTION_DAYS."""
    end = datetime.utcnow()
    last_ts = get_last_timestamp(output_path)

    if last_ts is None:
        start = end - timedelta(days=LOOKBACK_DAYS)
        logger.info("Premier run : bootstrap sur %d jours", LOOKBACK_DAYS)
    else:
        start = last_ts
        logger.info("Collecte incrémentale depuis %s", last_ts.isoformat())

    rows = []
    for metric_name, query in METRIC_QUERIES.items():
        try:
            series_list = fetch_range(query, start, end)
        except requests.exceptions.RequestException as exc:
            logger.error("Échec de la collecte pour %s: %s", metric_name, exc)
            continue

        for series in series_list:
            hostname = series["metric"].get("instance", "unknown").split(":")[0]
            for timestamp, value in series["values"]:
                point_ts = datetime.utcfromtimestamp(float(timestamp))
                if last_ts is not None and point_ts <= last_ts:
                    continue
                rows.append({
                    "timestamp": point_ts.isoformat(),
                    "hostname": hostname,
                    "metric": metric_name,
                    "value": float(value),
                })

        logger.info("Collecté %s : %d nouveaux points", metric_name, len(series_list))

    file_exists = os.path.isfile(output_path)
    with open(output_path, "a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
        if not file_exists:
            writer.writeheader()
        writer.writerows(rows)

    logger.info("Dataset mis à jour : %s (+%d nouvelles lignes)", output_path, len(rows))

    # Purge tout ce qui dépasse la fenêtre de rétention
    cutoff = end - timedelta(days=RETENTION_DAYS)
    purge_old_rows(output_path, cutoff)


if __name__ == "__main__":
    build_dataset("/app/forecast_dataset.csv")
