import asyncio
import logging
import os
from contextlib import asynccontextmanager
from fastapi import FastAPI
from sqlalchemy import text
from .db import engine, SessionLocal
from fastapi.staticfiles import StaticFiles
from .routers import nodes
from .routers import metrics
from .routers import dashboard
from .routers import logs
from .routers import anomalies
from .routers import baselines
from .services.anomaly_detector import detect_anomalies
from .services.baseline_builder import compute_baselines
from .services.node_seeder import seed_nodes_from_file_sd
from .services.forecast_dataset_builder import build_dataset
from .routers import forecast
from .services.forecast_trainer import train_all_models
from .services.topology_sync import sync_topology
from . import graph_db
logger = logging.getLogger(__name__)

# How often detect_anomalies() re-scores the latest Prometheus values. The
# EWMA fallback's alpha (see anomaly_detector.EWMA_ALPHA) is tuned assuming
# ~1-minute ticks, so this is the default.
ANOMALY_DETECTION_INTERVAL_SECONDS = int(os.getenv("ANOMALY_DETECTION_INTERVAL_SECONDS", "60"))
# How often the (weekday, hour) baselines table is rebuilt from Prometheus
# history. Hourly is plenty -- a single slot's stats don't meaningfully change
# faster than that, and it keeps the range-query load on Prometheus low.
BASELINE_REFRESH_INTERVAL_SECONDS = int(os.getenv("BASELINE_REFRESH_INTERVAL_SECONDS", "3600"))

# How often the forecasting dataset (cpu/memory/disk history CSV) is rebuilt
# from Prometheus. Forecasting looks at day/week-scale trends (tomorrow, 7d,
# 30d out), so unlike anomaly detection or baselines this doesn't need to be
# frequent -- daily is enough to track the trend and keeps the 14-day
# range-query cheap and infrequent on Prometheus.
FORECAST_DATASET_REFRESH_INTERVAL_SECONDS = int(os.getenv("FORECAST_DATASET_REFRESH_INTERVAL_SECONDS", "86400"))

# How often forecasting models are retrained from the latest dataset.
# Same cadence as the dataset rebuild -- no point retraining more often
# than the data itself changes.
FORECAST_TRAINING_INTERVAL_SECONDS = int(os.getenv("FORECAST_TRAINING_INTERVAL_SECONDS", "86400"))

# How often topology_sync polls Nova for hypervisors/services and upserts
# the topology graph. This is the one OpenStack polling loop (see
# adr-0002) -- 5 minutes is frequent enough to notice a new hypervisor or a
# service flipping state without hammering the OpenStack API.
TOPOLOGY_SYNC_INTERVAL_SECONDS = int(os.getenv("TOPOLOGY_SYNC_INTERVAL_SECONDS", "300"))

async def _run_periodic(fn, interval_seconds: float, name: str) -> None:
    """Runs fn(db) in a worker thread on a fixed interval, forever.

    Previously nothing called detect_anomalies()/compute_baselines() at all
    outside of tests, so anomaly_flags rows were written once (or never) and
    then went stale -- repeated GETs to /api/v1/anomalies/{hostname} kept
    returning the same value/timestamp no matter what was actually happening
    on the host. A failed pass is logged and retried on the next tick instead
    of killing the loop, since a transient Prometheus/DB hiccup shouldn't take
    detection down permanently.
    """
    while True:
        db = SessionLocal()
        try:
            await asyncio.to_thread(fn, db)
        except Exception:
            logger.exception("%s pass failed", name)
        finally:
            db.close()
        await asyncio.sleep(interval_seconds)


async def _run_periodic_no_db(fn, interval_seconds: float, name: str) -> None:
    """Same as _run_periodic, but for jobs that don't take a db session
    (forecast dataset builder writes straight to a CSV, not the database).
    """
    while True:
        try:
            await asyncio.to_thread(fn, "/app/forecast_dataset.csv")
        except Exception:
            logger.exception("%s pass failed", name)
        await asyncio.sleep(interval_seconds)

async def _run_periodic_no_args(fn, interval_seconds: float, name: str) -> None:
    """Same as _run_periodic, but for jobs that take no arguments at all
    (forecast_trainer reads its own dataset path from env, writes .pkl files)."""
    while True:
        try:
            await asyncio.to_thread(fn)
        except Exception:
            logger.exception("%s pass failed", name)
        await asyncio.sleep(interval_seconds)

@asynccontextmanager
async def lifespan(app: FastAPI):
    # If the DB is empty (fresh deploy, or infra that was provisioned by
    # Ansible before Cortex existed), backfill it from the Prometheus
    # file_sd file so /nodes isn't empty despite Prometheus already having
    # real targets. No-ops once any node exists. See node_seeder.py.
    db = SessionLocal()
    try:
        await asyncio.to_thread(seed_nodes_from_file_sd, db)
    except Exception:
        logger.exception("startup node seeding failed")
    finally:
        db.close()

    try:
        await asyncio.to_thread(graph_db.apply_schema_constraints)
    except Exception:
        # Non-fatal: the rest of the API doesn't depend on the topology
        # graph, and topology_sync's own MERGE calls will keep working even
        # without constraints (just without the uniqueness guarantee) --
        # log and move on rather than blocking startup.
        logger.exception("topology graph schema bootstrap failed")

    tasks = [
        asyncio.create_task(
            _run_periodic(detect_anomalies, ANOMALY_DETECTION_INTERVAL_SECONDS, "anomaly detection")
        ),
        asyncio.create_task(
            _run_periodic(compute_baselines, BASELINE_REFRESH_INTERVAL_SECONDS, "baseline refresh")
        ),
        asyncio.create_task(
            _run_periodic_no_db(build_dataset, FORECAST_DATASET_REFRESH_INTERVAL_SECONDS, "forecast dataset build")
        ),
        asyncio.create_task(
            _run_periodic_no_args(train_all_models, FORECAST_TRAINING_INTERVAL_SECONDS, "forecast model training")
),
        asyncio.create_task(
            _run_periodic(sync_topology, TOPOLOGY_SYNC_INTERVAL_SECONDS, "topology sync")
        ),
    ]
    try:
        yield
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        graph_db.close_driver()


app = FastAPI(title="Cortex API", version="0.1.0", lifespan=lifespan)
app.include_router(nodes.router)
app.include_router(metrics.router)
app.include_router(dashboard.router) 
app.include_router(logs.router)
app.include_router(anomalies.router)
app.include_router(baselines.router)
app.include_router(forecast.router)
app.mount("/ui", StaticFiles(directory="app/static", html=True), name="ui")


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/health/db")
def health_db():
    with engine.connect() as conn:
        conn.execute(text("SELECT 1"))
    return {"db": "ok"}
