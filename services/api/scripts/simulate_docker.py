"""
services/api/scripts/simulate_docker.py

Run this INSIDE the running api container:

    docker compose exec api python3 scripts/simulate_docker.py

Generates a fresh forecast_dataset.csv for the nodes that are *already
registered* in your running stack (whatever's in the `nodes` table right
now -- no new fake hosts created) and retrains on it. One of those existing
nodes is made to already be over its cpu_percent threshold (90% by default),
so /api/v1/forecast/warnings and the dashboard's "Threshold warnings" panel
have something to show immediately -- no waiting on model extrapolation,
since "already over" only depends on the latest observed value.

Every other existing node gets ordinary flat/idle metrics, so the rest of
your fleet keeps looking normal.

Safe to re-run: fully replaces forecast_dataset.csv (backed up to
forecast_dataset.csv.bak first) and retrains from scratch each time.
"""
from __future__ import annotations

import csv
import os
import shutil
import sys
from datetime import datetime, timedelta, timezone

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.services.forecast_trainer import DATASET_PATH, MODELS_DIR  # noqa: E402

STEP_MINUTES = 5
DAYS_OF_HISTORY = 10
NOW = datetime.now(timezone.utc).replace(microsecond=0)

BREACH_METRIC = "cpu_percent"
BREACH_THRESHOLD = 90.0


def _timestamps():
    n_steps = DAYS_OF_HISTORY * 24 * (60 // STEP_MINUTES)
    return [NOW - timedelta(minutes=STEP_MINUTES * i) for i in range(n_steps, 0, -1)]


def _daily_wave(ts_list, mean, amplitude):
    return np.array([
        mean + amplitude * np.sin(2 * np.pi * (ts.hour + ts.minute / 60) / 24)
        for ts in ts_list
    ])


def _rows_for_host(ip, metric, values, ts_list):
    return [
        {"timestamp": ts.isoformat(), "hostname": ip, "metric": metric, "value": round(float(v), 2)}
        for ts, v in zip(ts_list, values)
    ]


def get_existing_nodes() -> list[tuple[str, str]]:
    """Returns [(hostname, ip_address), ...] for every node already in the
    real DB -- nothing new is created here."""
    from app.db import SessionLocal
    from app import models

    db = SessionLocal()
    try:
        nodes = db.query(models.Node).all()
        return [(n.hostname, n.ip_address) for n in nodes]
    finally:
        db.close()


def build_dataset_rows(nodes: list[tuple[str, str]]) -> list[dict]:
    ts_list = _timestamps()
    n = len(ts_list)
    rng = np.random.default_rng(7)
    rows = []

    breaching_hostname, breaching_ip = nodes[0]
    print(f"  -> {breaching_hostname} ({breaching_ip}) will be the breaching node ({BREACH_METRIC} > {BREACH_THRESHOLD}%)")

    for i, (hostname, ip) in enumerate(nodes):
        if ip == breaching_ip:
            # Already over threshold right now -- "already_breached" fires
            # off the latest value alone, no model extrapolation needed.
            cpu = np.clip(np.linspace(72, 94, n) + rng.normal(0, 1.2, n), 0, 100)
        else:
            # Ordinary idle-ish node: flat with mild diurnal variation, well
            # under threshold, on all three metrics.
            cpu = np.clip(_daily_wave(ts_list, 20 + 5 * (i % 3), 3) + rng.normal(0, 1.5, n), 0, 100)
        rows += _rows_for_host(ip, "cpu_percent", cpu, ts_list)
        rows += _rows_for_host(ip, "memory_percent",
                                np.clip(_daily_wave(ts_list, 35 + 5 * (i % 3), 3) + rng.normal(0, 1, n), 0, 100), ts_list)
        rows += _rows_for_host(ip, "disk_percent",
                                np.clip(_daily_wave(ts_list, 25 + 5 * (i % 3), 0.5) + rng.normal(0, 0.3, n), 0, 100), ts_list)

    return rows


def write_dataset(rows: list[dict]) -> None:
    if os.path.isfile(DATASET_PATH):
        shutil.copy2(DATASET_PATH, DATASET_PATH + ".bak")
        print(f"[1/3] Backed up existing dataset to {DATASET_PATH}.bak")

    os.makedirs(os.path.dirname(DATASET_PATH) or ".", exist_ok=True)
    with open(DATASET_PATH, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["timestamp", "hostname", "metric", "value"])
        writer.writeheader()
        writer.writerows(rows)
    print(f"[1/3] Wrote {len(rows)} rows to {DATASET_PATH}")


def train_models() -> None:
    from app.services.forecast_trainer import train_all_models
    train_all_models()
    trained = sorted(os.listdir(MODELS_DIR)) if os.path.isdir(MODELS_DIR) else []
    print(f"[2/3] Retrained models in {MODELS_DIR}: {trained}")


def report(nodes: list[tuple[str, str]]) -> None:
    from app.services.forecast_service import get_threshold_warning

    print("[3/3] Threshold check:")
    for hostname, ip in nodes:
        w = get_threshold_warning(ip, BREACH_METRIC)
        if w is None:
            print(f"  {hostname}: no warning data (unexpected)")
            continue
        status = "already over" if w["already_breached"] else (
            f"in ~{w['eta_days']}d" if w["will_breach"] else "safe, no breach projected"
        )
        print(f"  {hostname:20s} {BREACH_METRIC}: {w['current_value']:5.1f}% now, "
              f"threshold {w['threshold']}%, {status} (model={w['model_type']})")

    print("""
Done. From your host machine:
    curl -s http://localhost:8000/api/v1/forecast/warnings | python3 -m json.tool

The dashboard's "Threshold warnings" panel and the /forecast page (pick the
breaching node above) should now show it.
""")


def main():
    nodes = get_existing_nodes()
    if not nodes:
        print("No nodes found in the DB -- register at least one node first "
              "(this script only generates data for nodes that already exist).")
        return

    rows = build_dataset_rows(nodes)
    write_dataset(rows)
    train_models()
    report(nodes)


if __name__ == "__main__":
    main()
