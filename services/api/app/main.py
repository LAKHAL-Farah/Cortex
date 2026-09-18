import asyncio
import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime
from fastapi import FastAPI, Depends
from sqlalchemy import text
from .db import engine, SessionLocal
from fastapi.staticfiles import StaticFiles
from .routers import nodes
from .routers import metrics
from .routers import dashboard
from .routers import logs
from .routers import anomalies
from .routers import baselines
from .routers import topology
from .routers import network
from .routers import knowledge
from .routers import conversations
from .routers import settings
from .routers import quotas
from .routers import agents
from .routers import security
from .routers import openstack_docs
from .routers import openstack_expert
from .routers import auth as auth_router
from .auth import get_current_user, hash_password
from . import models
from .services.anomaly_detector import detect_anomalies
from .services.quota_budget_monitor import check_quota_and_budget
from .services.baseline_builder import compute_baselines
from .services.security_snapshot_builder import capture_security_group_snapshots
from .services.security_scan_cache import run_security_scan
from .services.node_seeder import seed_nodes_from_file_sd
from .services.forecast_dataset_builder import build_dataset
from .routers import forecast
from .services.forecast_trainer import train_all_models
from .services.topology_sync import sync_topology
from .services.prometheus_health import sync_prometheus_health
from . import crud, graph_db
logger = logging.getLogger(__name__)

# How often detect_anomalies() re-scores the latest Prometheus values. The
# EWMA fallback's alpha (see anomaly_detector.EWMA_ALPHA) is tuned assuming
# ~1-minute ticks, so this is the default.
ANOMALY_DETECTION_INTERVAL_SECONDS = int(os.getenv("ANOMALY_DETECTION_INTERVAL_SECONDS", "60"))
# How often the (weekday, hour) baselines table is rebuilt from Prometheus
# history. Hourly is plenty -- a single slot's stats don't meaningfully change
# faster than that, and it keeps the range-query load on Prometheus low.
BASELINE_REFRESH_INTERVAL_SECONDS = int(os.getenv("BASELINE_REFRESH_INTERVAL_SECONDS", "3600"))
# How often security_snapshot_builder.capture_security_group_snapshots()
# re-captures every known node's security groups into
# `security_group_snapshots` (Phase Sec-1). Deliberately its own interval
# rather than reusing TOPOLOGY_SYNC_INTERVAL_SECONDS or
# BASELINE_REFRESH_INTERVAL_SECONDS -- it's neither the Nova/Cinder/
# Neutron structural sync topology_sync.py owns (adr-0002's "one and only
# OpenStack polling loop" is about that one job, not every OpenStack read
# in this codebase, the same exception security_audit.py's own module
# docstring already carves out) nor a Prometheus-history rebuild. Default
# well under an hour: unlike a (weekday, hour) baseline slot, a security-
# group rule can change at any moment and the whole point of this phase is
# to notice that promptly.
SECURITY_SNAPSHOT_INTERVAL_SECONDS = int(os.getenv("SECURITY_SNAPSHOT_INTERVAL_SECONDS", "900"))

# How often run_security_scan() (services/security_scan_cache.py) re-runs
# the Security Agent's four sub-checks for every known node and refreshes
# `security_finding_cache` -- what GET /api/v1/security/findings and the
# /security, /security/security-groups dashboard pages actually read.
# Deliberately its own interval, much shorter than
# SECURITY_SNAPSHOT_INTERVAL_SECONDS above: the snapshot job only feeds
# *drift* history and doesn't need to run often, but this cache is the
# thing a person is staring at waiting for it to update, the same
# "shouldn't feel stale while you're looking at it" bar
# PROMETHEUS_HEALTH_SYNC_INTERVAL_SECONDS sets for the topology page.
# POST /api/v1/security/resync (the Security page's manual "Rescan" button)
# triggers an out-of-band pass on top of this without waiting for the tick.
SECURITY_SCAN_INTERVAL_SECONDS = int(os.getenv("SECURITY_SCAN_INTERVAL_SECONDS", "45"))

# How often the forecasting dataset (cpu/memory/disk history CSV) is rebuilt
# from Prometheus. build_dataset() is incremental (it only fetches since the
# last stored point), so an hourly cadence is cheap on Prometheus and is what
# lets the "value_now"/short-lag features forecast_service.py builds actually
# reflect the last hour, not yesterday's data -- see adr-0006.
FORECAST_DATASET_REFRESH_INTERVAL_SECONDS = int(os.getenv("FORECAST_DATASET_REFRESH_INTERVAL_SECONDS", "3600"))

# How often forecasting models are retrained from the latest dataset. Same
# hourly cadence as the dataset rebuild -- the pooled HistGradientBoosting
# quantile models train in low single-digit seconds even with several hosts'
# worth of history, so there's no cost reason to lag the dataset refresh.
FORECAST_TRAINING_INTERVAL_SECONDS = int(os.getenv("FORECAST_TRAINING_INTERVAL_SECONDS", "3600"))

# How often topology_sync polls Nova for hypervisors/services and upserts
# the topology graph. This is the one OpenStack polling loop (see
# adr-0002) -- 5 minutes is frequent enough to notice a new hypervisor or a
# service flipping state without hammering the OpenStack API.
TOPOLOGY_SYNC_INTERVAL_SECONDS = int(os.getenv("TOPOLOGY_SYNC_INTERVAL_SECONDS", "300"))

# How often sync_prometheus_health() overlays `up{job="node_exporter"}"
# onto :Node.health and recomputes :Service.state (see adr-0003). Runs far
# more often than TOPOLOGY_SYNC_INTERVAL_SECONDS -- it only talks to
# Prometheus (20s scrape interval, see infra/prometheus/prometheus.yml)
# and Neo4j, never OpenStack, so there's no reason to tie its cadence to
# the OpenStack poll.
PROMETHEUS_HEALTH_SYNC_INTERVAL_SECONDS = int(os.getenv("PROMETHEUS_HEALTH_SYNC_INTERVAL_SECONDS", "30"))

# How often check_quota_and_budget() re-polls Nova/Cinder limits per
# project. Quotas/estimated spend don't swing nearly as fast as raw
# node_exporter metrics, so this defaults far less frequent than
# ANOMALY_DETECTION_INTERVAL_SECONDS -- 5 minutes, same cadence as
# TOPOLOGY_SYNC_INTERVAL_SECONDS since it's the same class of "poll
# OpenStack" job.
QUOTA_BUDGET_CHECK_INTERVAL_SECONDS = int(os.getenv("QUOTA_BUDGET_CHECK_INTERVAL_SECONDS", "300"))

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


def _topology_sync_status(summary: dict) -> str:
    """topology_sync.sync_topology()'s summary carries two independent
    "did we get a complete picture" flags (see its docstring/return value):
    `complete_picture` for the Node/Service sweep, `network_topology_ok`
    for the Network/Subnet/Router/FloatingIP sweep. Either being False
    means at least one OpenStack listing failed this pass but the pass
    itself still ran and produced a (partial) summary -- "degraded", not
    "failed". "failed" is reserved for the pass raising before returning
    anything at all (caught in _run_periodic_recorded below).
    """
    if summary.get("complete_picture") and summary.get("network_topology_ok"):
        return "ok"
    return "degraded"


def _prometheus_health_status(summary: dict) -> str:
    """prometheus_health.sync_prometheus_health() returns
    {"queried": False, ...} (rather than raising) when Prometheus itself
    was unreachable this pass -- see that function's docstring. That's a
    normal, self-recovering skip, not a crash, so it's "degraded" here
    too, on the same reasoning as _topology_sync_status above.
    """
    if summary.get("queried"):
        return "ok"
    return "degraded"


def _security_scan_status(summary: dict) -> str:
    """run_security_scan()'s summary (services/security_scan_cache.py)
    carries `degraded` -- True if any known node's scan had at least one
    sub-check fail/time out this pass, or the pass itself couldn't reach
    a node at all. Same "ran, but incompletely" vs. "ran cleanly"
    distinction _topology_sync_status/_prometheus_health_status already
    draw for their own loops.
    """
    if summary.get("degraded"):
        return "degraded"
    return "ok"


async def _run_periodic_recorded(
    fn, interval_seconds: float, name: str, sync_type: str, status_fn, record_fn=None,
) -> None:
    """Same shape as _run_periodic, but for periodic passes whose outcome
    also gets appended to a run-history table so a `/health` endpoint has
    real run history to answer from, not just a snapshot of whatever the
    pass produces (see models.TopologySyncRun / models.SecurityScanRun's
    own docstrings for why that distinction matters).

    `status_fn(summary) -> "ok"|"degraded"` classifies a successful pass's
    own summary dict; a pass that raises is always recorded as "failed"
    regardless of status_fn, since there's no summary to classify.

    `record_fn` defaults to `crud.record_topology_sync_run` (this
    function's original two callers, the OpenStack/Prometheus sync
    loops, both write to `topology_sync_runs` keyed by `sync_type`) --
    pass `crud.record_security_scan_run` instead for a loop that writes
    to its own table with no `sync_type` column at all (there's only one
    kind of security scan pass, so a discriminator would be constant).
    """
    record_fn = record_fn or crud.record_topology_sync_run
    while True:
        started_at = datetime.utcnow()
        db = SessionLocal()
        summary: dict | None = None
        error: str | None = None
        try:
            summary = await asyncio.to_thread(fn, db)
            run_status = status_fn(summary)
        except Exception as exc:
            logger.exception("%s pass failed", name)
            run_status = "failed"
            error = repr(exc)
        finally:
            finished_at = datetime.utcnow()
            try:
                if record_fn is crud.record_topology_sync_run:
                    record_fn(
                        db,
                        sync_type=sync_type,
                        status=run_status,
                        summary=summary,
                        error=error,
                        started_at=started_at,
                        finished_at=finished_at,
                    )
                else:
                    record_fn(
                        db,
                        status=run_status,
                        summary=summary,
                        error=error,
                        started_at=started_at,
                        finished_at=finished_at,
                    )
            except Exception:
                # Recording the run is itself best-effort: a Postgres hiccup
                # here shouldn't take the sync loop down, and the next tick
                # will just append another row on top of whatever the last
                # successfully-recorded one was.
                logger.exception("failed to record sync-run metadata for %s", name)
            db.close()
        await asyncio.sleep(interval_seconds)

def _seed_bootstrap_admin(db) -> None:
    """Creates the first admin account if the `users` table is empty --
    otherwise there's a chicken-and-egg problem where you need an admin
    account to create the first admin account. Username/password come from
    CORTEX_ADMIN_USERNAME/CORTEX_ADMIN_PASSWORD (defaulting to admin/admin
    for local/dev use, see infra/.env.example); must_change_password is
    always forced True so that default can't silently stick around.

    No-ops the moment any user exists, including after the bootstrap admin's
    password has been changed -- this only ever fires once per fresh DB.
    """
    if crud.count_users(db) > 0:
        return
    username = os.environ.get("CORTEX_ADMIN_USERNAME", "admin")
    password = os.environ.get("CORTEX_ADMIN_PASSWORD", "admin")
    crud.create_user(
        db,
        username=username,
        password_hash=hash_password(password),
        role="admin",
        must_change_password=True,
    )
    logger.warning(
        "Cortex had no accounts -- created bootstrap admin user '%s'. "
        "Log in and change the password immediately (you'll be forced to).",
        username,
    )


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

    db = SessionLocal()
    try:
        await asyncio.to_thread(_seed_bootstrap_admin, db)
    except Exception:
        logger.exception("bootstrap admin seeding failed")
    finally:
        db.close()

    # Best-effort initial security scan so `security_finding_cache` isn't
    # empty for the first person to load /security after a fresh deploy --
    # without this, GET /findings' per-host fallback in routers/security.py
    # still covers it (an on-demand live scan per host), just slower than
    # having the fleet already warm. Same "seed once at startup, then let
    # the periodic loop take over" idiom as seed_nodes_from_file_sd above.
    db = SessionLocal()
    try:
        await asyncio.to_thread(run_security_scan, db)
    except Exception:
        logger.exception("startup security scan failed")
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
            _run_periodic(
                capture_security_group_snapshots, SECURITY_SNAPSHOT_INTERVAL_SECONDS, "security-group snapshot"
            )
        ),
        asyncio.create_task(
            _run_periodic_no_db(build_dataset, FORECAST_DATASET_REFRESH_INTERVAL_SECONDS, "forecast dataset build")
        ),
        asyncio.create_task(
            _run_periodic_no_args(train_all_models, FORECAST_TRAINING_INTERVAL_SECONDS, "forecast model training")
),
        asyncio.create_task(
            _run_periodic_recorded(
                sync_topology,
                TOPOLOGY_SYNC_INTERVAL_SECONDS,
                "topology sync",
                sync_type="openstack",
                status_fn=_topology_sync_status,
            )
        ),
        asyncio.create_task(
            _run_periodic_recorded(
                sync_prometheus_health,
                PROMETHEUS_HEALTH_SYNC_INTERVAL_SECONDS,
                "prometheus health sync",
                sync_type="prometheus_health",
                status_fn=_prometheus_health_status,
            )
        ),
        asyncio.create_task(
            _run_periodic(
                check_quota_and_budget, QUOTA_BUDGET_CHECK_INTERVAL_SECONDS, "quota/budget check"
            )
        ),
        asyncio.create_task(
            _run_periodic_recorded(
                run_security_scan,
                SECURITY_SCAN_INTERVAL_SECONDS,
                "security scan",
                sync_type="security_scan",
                status_fn=_security_scan_status,
                record_fn=crud.record_security_scan_run,
            )
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

# Login is the one endpoint that must be reachable without already being
# logged in -- everything else below gets Depends(get_current_user) applied
# at include_router time, so no individual route has to remember to guard
# itself. (Admin-only endpoints, e.g. POST /auth/users or node/knowledge
# mutations, layer require_admin on top of that -- see auth.py.)
app.include_router(auth_router.router)

_auth_required = [Depends(get_current_user)]
app.include_router(nodes.router, dependencies=_auth_required)
app.include_router(metrics.router, dependencies=_auth_required)
app.include_router(dashboard.router, dependencies=_auth_required)
app.include_router(logs.router, dependencies=_auth_required)
app.include_router(anomalies.router, dependencies=_auth_required)
app.include_router(baselines.router, dependencies=_auth_required)
app.include_router(forecast.router, dependencies=_auth_required)
app.include_router(topology.router, dependencies=_auth_required)
app.include_router(network.router, dependencies=_auth_required)
app.include_router(quotas.router, dependencies=_auth_required)
# Not added to the periodic lifespan tasks above on purpose -- unlike anomaly
# detection/baselines/forecasting/topology sync, the knowledge base doesn't
# drift on a schedule (see adr-0004), so ingestion is triggered on demand via
# POST /api/v1/knowledge/ingest (or the ingest_knowledge CLI script) instead
# of a background timer.
app.include_router(knowledge.router, dependencies=_auth_required)
# Server-side persistence for Copilot chat threads (see routers/conversations.py's
# module docstring) -- like knowledge.router, not added to the periodic
# lifespan tasks above since it's plain request/response CRUD, nothing to
# poll on a schedule.
app.include_router(conversations.router, dependencies=_auth_required)
app.include_router(agents.router, dependencies=_auth_required)
# Phase Sec-2: direct, pollable GET endpoints for the Security Agent's
# findings, the same shape network.router gives the Network agent's data
# -- see routers/security.py's module docstring for why this doesn't
# re-implement agents.router's RBAC redaction (services/security_rbac.py
# is the shared helper both routers call).
app.include_router(security.router, dependencies=_auth_required)
app.include_router(settings.router, dependencies=_auth_required)
# v1.0 (docs/architecture/adr-0010-openstack-expert-v1-expansion.md): the
# official-docs embedding pipeline (its own on-demand /ingest, same
# rationale as knowledge.router above -- upstream OpenStack docs don't
# drift on this deployment's clock either) and the catalog-entry
# feedback loop. Both admin-only actions (publishing an entry, running a
# docs ingest) are gated per-route with require_admin, same pattern
# knowledge.router's own /ingest already uses -- login alone is enough to
# read/draft, not enough to change what the live agent matches against.
app.include_router(openstack_docs.router, dependencies=_auth_required)
app.include_router(openstack_expert.router, dependencies=_auth_required)
app.mount("/ui", StaticFiles(directory="app/static", html=True), name="ui")

@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/health/db")
def health_db():
    with engine.connect() as conn:
        conn.execute(text("SELECT 1"))
    return {"db": "ok"}
