"""Glue for roadmap 4.10: pick a utilisation source, price it with the FinOps
unit rates, run the pure analysis, return one JSON-serialisable payload.

Used by the /api/v1/rightsizing endpoint *and* by the PDF reports (4.11), so
the savings figure in a financial report is the same number the UI shows.
"""
from __future__ import annotations

import logging
import os
import time
from datetime import datetime

from sqlalchemy.orm import Session

from .. import models
from . import finops, rightsizing, vm_utilization

logger = logging.getLogger(__name__)

SOURCES = ("auto", "live", "synthetic")
MIN_WINDOW_DAYS, MAX_WINDOW_DAYS, DEFAULT_WINDOW_DAYS = 1, 30, 7
_CACHE_TTL_SECONDS = float(os.environ.get("RIGHTSIZING_CACHE_TTL_SECONDS", "120"))
_cache: dict[tuple, tuple[float, dict]] = {}


def _latest_cloud_sample(db: Session) -> models.FinopsCloudSample | None:
    return db.query(models.FinopsCloudSample).order_by(models.FinopsCloudSample.sampled_at.desc()).first()


def resolve_rates(db: Session, allow_reference: bool = False) -> rightsizing.UnitRates:
    """The same unit rates the showback/chargeback report uses: the latest
    metered cloud sample. If Cortex has never metered (fresh install) the
    savings are reported as unpriced -- except for the synthetic demo, which
    passes ``allow_reference`` to use the documented reference-cloud rates."""
    cloud = _latest_cloud_sample(db)
    if cloud is not None and cloud.rate_vcpu_eur is not None and cloud.rate_ram_gb_eur is not None:
        return rightsizing.UnitRates(
            vcpu=cloud.rate_vcpu_eur, ram_gb=cloud.rate_ram_gb_eur, source="finops-metered"
        )
    if allow_reference:
        return vm_utilization.synthetic_rates()
    return rightsizing.UnitRates(vcpu=None, ram_gb=None, source="unavailable")


def _project_names(db: Session) -> dict[str, str]:
    cloud = _latest_cloud_sample(db)
    if cloud is None:
        return {}
    rows = db.query(models.FinopsProjectSample).filter_by(sampled_at=cloud.sampled_at).all()
    return {r.project_id: r.project_name for r in rows}


def _unavailable(reason: str, source: str, window_days: int) -> dict:
    return {
        "available": False,
        "reason": reason,
        "source": source,
        "window_days": window_days,
        "currency": finops.CURRENCY,
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "recommendation_only": True,
        "recommendations": [],
        "others": [],
        "summary": None,
    }


def build_recommendations(
    db: Session,
    window_days: int = DEFAULT_WINDOW_DAYS,
    source: str = "auto",
    project_id: str | None = None,
    use_cache: bool = True,
) -> dict:
    if source not in SOURCES:
        raise ValueError(f"source must be one of {', '.join(SOURCES)}")
    window_days = max(MIN_WINDOW_DAYS, min(int(window_days), MAX_WINDOW_DAYS))
    # "auto" means live: it never quietly becomes synthetic data.
    resolved = "synthetic" if source == "synthetic" else "live"

    key = (resolved, window_days)
    if use_cache and resolved == "live":
        hit = _cache.get(key)
        if hit and time.monotonic() - hit[0] < _CACHE_TTL_SECONDS:
            return _filter_project(hit[1], project_id)

    policy = rightsizing.load_policy()
    if resolved == "synthetic":
        fleet = vm_utilization.synthetic_fleet()
        rates = resolve_rates(db, allow_reference=True)
        # Metered rates win when they exist; the reference rates are only the
        # fallback so the demo still shows euro figures on a fresh database.
        catalog = None
    else:
        try:
            fleet = vm_utilization.live_fleet(window_days, project_names=_project_names(db))
        except vm_utilization.NoUtilizationData as exc:
            return _unavailable(str(exc), resolved, window_days)
        except Exception as exc:
            logger.warning("right-sizing: live utilisation unavailable", exc_info=True)
            return _unavailable(f"Could not read VM utilisation: {exc}", resolved, window_days)
        rates = resolve_rates(db)
        catalog = vm_utilization.nova_flavors()

    result = rightsizing.analyze_fleet(fleet, rates, catalog=catalog, policy=policy, currency=finops.CURRENCY)
    payload = {
        "available": True,
        "source": resolved,
        "window_days": window_days,
        "currency": finops.CURRENCY,
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "recommendation_only": True,
        "flavor_catalog": "nova" if catalog else "ladder",
        "policy": {
            "idle_cpu_p95": policy.idle_cpu_p95,
            "under_cpu_p95": policy.under_cpu_p95,
            "under_mem_p95": policy.under_mem_p95,
            "saturated_p95": policy.saturated_p95,
            "cpu_target": policy.cpu_target,
            "mem_target": policy.mem_target,
            "peak_ceiling": policy.peak_ceiling,
        },
        "summary": result.summary,
        "recommendations": result.recommendations,
        "others": result.others,
        "caveat": (
            "Savings are reclaimable run-rate at the cloud's derived unit rates. The fixed monthly bill only "
            "falls when the freed capacity is removed or put to other use."
        ),
    }
    if resolved == "live":
        _cache[key] = (time.monotonic(), payload)
    return _filter_project(payload, project_id)


def _filter_project(payload: dict, project_id: str | None) -> dict:
    """Project filter applied *after* the (cached) fleet-wide analysis, so one
    cache entry serves every project view."""
    if not project_id:
        return payload
    out = dict(payload)
    out["recommendations"] = [r for r in payload["recommendations"] if r.get("project_id") == project_id]
    out["others"] = [r for r in payload["others"] if r.get("project_id") == project_id]
    out["project_filter"] = project_id
    if payload.get("summary"):
        s = dict(payload["summary"])
        s["flagged"] = len(out["recommendations"])
        s["vms_analyzed"] = len(out["recommendations"]) + len(out["others"])
        if s.get("priced"):
            total = sum(r["estimated_monthly_savings_eur"] or 0.0 for r in out["recommendations"])
            s["estimated_monthly_savings_eur"] = round(total, 2)
            s["estimated_annual_savings_eur"] = round(total * 12, 2)
            s["low_risk_monthly_savings_eur"] = round(
                sum(r["estimated_monthly_savings_eur"] or 0.0 for r in out["recommendations"] if r["risk"]["level"] == "low"), 2
            )
        s["by_project"] = [p for p in s.get("by_project", []) if p.get("project_id") == project_id]
        counts = {k: 0 for k in s["by_classification"]}
        for row in out["recommendations"] + out["others"]:
            counts[row["classification"]] = counts.get(row["classification"], 0) + 1
        s["by_classification"] = counts
        s["by_risk"] = {lvl: sum(1 for r in out["recommendations"] if r["risk"]["level"] == lvl) for lvl in ("low", "medium", "high")}
        # Unweighted mean here: per-VM cost weights aren't carried on the rows.
        scored = [r["efficiency_score"] for r in out["recommendations"] + out["others"] if r["efficiency_score"] is not None]
        s["fleet_efficiency_score"] = round(sum(scored) / len(scored), 1) if scored else None
        out["summary"] = s
    return out


def clear_cache() -> None:
    _cache.clear()
