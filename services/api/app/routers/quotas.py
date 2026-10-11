import logging
import time
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from .. import models
from ..auth import require_admin
from ..db import get_db
from ..services import finops, finops_report
from ..services.quota_budget_monitor import check_quota_and_budget

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/quotas", tags=["quotas"])

_SEVERITY_RANK = {"critical": 2, "warning": 1, "normal": 0}


def _iso_utc(dt) -> str | None:
    """Same fix as routers/anomalies.py::_iso_utc -- detected_at is stored
    naive-UTC, so it needs an explicit tzinfo before .isoformat() or a
    browser's `new Date(...)` reads it as local time."""
    if dt is None:
        return None
    return dt.replace(tzinfo=timezone.utc).isoformat()


def _serialize(row: models.QuotaAlert) -> dict:
    return {
        "project_id": row.project_id,
        "project_name": row.project_name,
        "breach_type": row.breach_type,  # "capacity_cap" | "budget_cap"
        "resource": row.resource,
        "used": row.used,
        "limit": row.limit,
        "ratio": row.ratio,
        "severity": row.severity,
        "message": row.message,
        "detected_at": _iso_utc(row.detected_at),
    }


@router.get("/alerts")
def list_quota_alerts(db: Session = Depends(get_db)):
    """Currently-breached quota/budget slots (severity != "normal"),
    most severe first. Each row's `breach_type` and `message` say
    explicitly whether this is a `capacity_cap` (OpenStack quota) or a
    `budget_cap` (estimated spend) breach -- see
    services/quota_budget_monitor.py's module docstring for why those
    are kept distinct rather than folded into one generic alert.
    """
    rows = (
        db.query(models.QuotaAlert)
        .filter(models.QuotaAlert.severity != "normal")
        .all()
    )
    rows.sort(key=lambda r: _SEVERITY_RANK.get(r.severity, 0), reverse=True)
    return [_serialize(r) for r in rows]


@router.get("/alerts/{project_id}")
def get_project_quota_alerts(project_id: str, db: Session = Depends(get_db)):
    """Every checked slot for one project, including ones currently
    "normal" -- lets a UI show full quota/budget headroom for a project,
    not just what's actively breached.
    """
    rows = db.query(models.QuotaAlert).filter_by(project_id=project_id).all()
    return [_serialize(r) for r in rows]


@router.post("/resync")
def trigger_quota_resync(db: Session = Depends(get_db)):
    """Manual trigger for check_quota_and_budget(), same intent as
    POST /api/v1/topology/resync -- run a pass immediately instead of
    waiting for the periodic loop's next tick.
    """
    summary = check_quota_and_budget(db)
    return {"status": "ok", "summary": summary}


# ---------------------------------------------------------------------------
# FinOps (private-cloud cost model) -- see services/finops.py for why costs
# here are a share of a fixed monthly bill rather than a per-unit price list.
# ---------------------------------------------------------------------------

_PHYSICAL_UTIL_TTL_SECONDS = 30
_physical_util_cache: dict = {"at": 0.0, "value": None}


def _physical_utilization() -> dict | None:
    """What the compute nodes are *actually* doing right now (node_exporter
    via Prometheus), to set beside what has been *reserved* for projects --
    reserved-but-idle is the cloud owner's main reclaim opportunity.
    Cached: the Overview tab polls, and collect_metrics() is ~15 Prometheus
    queries. A Prometheus outage yields None (cached too, so a down
    Prometheus doesn't stall every poll on its timeout)."""
    now = time.monotonic()
    if now - _physical_util_cache["at"] < _PHYSICAL_UTIL_TTL_SECONDS:
        return _physical_util_cache["value"]
    value = None
    try:
        from ..services.metrics_collector import collect_metrics

        compute = [m for m in collect_metrics() if m.get("role") == "compute" and m.get("status") == "up"]
        if compute:
            value = {
                "compute_nodes": len(compute),
                "cpu_percent": round(sum(m["cpu_percent"] for m in compute) / len(compute), 1),
                "memory_percent": round(sum(m["memory_percent"] for m in compute) / len(compute), 1),
            }
    except Exception:
        logger.warning("finops overview: physical utilization unavailable", exc_info=True)
    _physical_util_cache.update(at=now, value=value)
    return value


def _pct(numerator: float | None, denominator: float | None) -> float | None:
    if numerator is None or not denominator:
        return None
    return round(numerator / denominator * 100, 1)


def _capacity_row(physical, sellable, allocated, committed, unlimited_projects) -> dict:
    return {
        "physical": physical,
        "sellable": sellable,
        "allocated": allocated,
        "committed": committed,
        "unlimited_projects": unlimited_projects,
        "allocated_pct": _pct(allocated, sellable),
        "committed_pct": _pct(committed, sellable),
    }


@router.get("/overview")
def finops_overview(db: Session = Depends(get_db)):
    """Everything the Overview/Projects tabs need in one call, built from
    the latest hourly metering samples + the per-slot quota rows -- no
    OpenStack round-trip, so it is cheap to poll."""
    cloud = (
        db.query(models.FinopsCloudSample)
        .order_by(models.FinopsCloudSample.sampled_at.desc())
        .first()
    )
    if cloud is None:
        return {"available": False, "currency": finops.CURRENCY}

    samples = db.query(models.FinopsProjectSample).filter_by(sampled_at=cloud.sampled_at).all()
    settings = {s.project_id: s for s in db.query(models.ProjectFinopsSetting).all()}
    alert_rows = db.query(models.QuotaAlert).all()
    quota_by_project: dict[str, dict[str, models.QuotaAlert]] = {}
    budget_by_project: dict[str, models.QuotaAlert] = {}
    for row in alert_rows:
        if row.breach_type == "capacity_cap":
            quota_by_project.setdefault(row.project_id, {})[row.resource] = row
        else:
            budget_by_project[row.project_id] = row

    sellable_vcpus = cloud.physical_vcpus * cloud.cpu_allocation_ratio if cloud.physical_vcpus else None
    sellable_ram = cloud.physical_ram_mb * cloud.ram_allocation_ratio if cloud.physical_ram_mb else None
    sellable_gb = cloud.storage_gb or None

    env_departments = finops.load_project_departments()
    total_fully_loaded = sum(
        s.compute_eur_month + s.storage_eur_month + s.network_eur_month + s.platform_eur_month + s.idle_eur_month
        for s in samples
    )

    projects = []
    for smp in samples:
        direct = smp.compute_eur_month + smp.storage_eur_month + smp.network_eur_month
        fully = direct + smp.platform_eur_month + smp.idle_eur_month
        quotas = quota_by_project.get(smp.project_id, {})
        budget_row = budget_by_project.get(smp.project_id)
        setting = settings.get(smp.project_id)

        def res(key: str, used: float) -> dict:
            q = quotas.get(key)
            return {"used": used, "quota": q.limit if q else None, "quota_ratio": q.ratio if q else None}

        severities = [r.severity for r in quotas.values()] + ([budget_row.severity] if budget_row else [])
        worst = "critical" if "critical" in severities else "warning" if "warning" in severities else "normal"
        budget_eur = (
            setting.monthly_budget_eur if setting and setting.monthly_budget_eur and setting.monthly_budget_eur > 0
            else (budget_row.limit if budget_row else None)
        )
        projects.append(
            {
                "project_id": smp.project_id,
                "project_name": smp.project_name,
                "department": (
                    (setting.department.strip() if setting and setting.department and setting.department.strip() else None)
                    or env_departments.get(smp.project_id)
                    or env_departments.get(smp.project_name)
                    or smp.department
                    or finops.UNASSIGNED_DEPARTMENT
                ),
                "usage": {
                    "instances": res("instances", smp.instances),
                    "vcpus": res("vcpus", smp.vcpus),
                    "ram_mb": res("ram_mb", smp.ram_mb),
                    "gigabytes": res("gigabytes", smp.gigabytes),
                    "volumes": res("volumes", smp.volumes),
                    "floating_ips": res("floating_ips", smp.floating_ips),
                },
                # Share of the *whole cloud's* sellable capacity this project
                # holds -- "who is using the platform", as opposed to quota
                # ratio which is "how close is this project to its own cap".
                "cloud_share": {
                    "vcpus_pct": _pct(smp.vcpus, sellable_vcpus),
                    "ram_pct": _pct(smp.ram_mb, sellable_ram),
                    "storage_pct": _pct(smp.gigabytes, sellable_gb),
                },
                "cost": {
                    "compute_eur": round(smp.compute_eur_month, 2),
                    "storage_eur": round(smp.storage_eur_month, 2),
                    "network_eur": round(smp.network_eur_month, 2),
                    "direct_eur": round(direct, 2),
                    "platform_eur": round(smp.platform_eur_month, 2),
                    "idle_eur": round(smp.idle_eur_month, 2),
                    "fully_loaded_eur": round(fully, 2),
                    "share_pct": round(fully / total_fully_loaded * 100, 1) if total_fully_loaded else 0.0,
                },
                "budget_eur": budget_eur,
                "budget_used_ratio": round(direct / budget_eur, 4) if budget_eur else None,
                "severity": worst,
                "breaches": sum(1 for sev in severities if sev != "normal"),
            }
        )
    projects.sort(key=lambda p: p["cost"]["fully_loaded_eur"], reverse=True)

    details = cloud.details or {}
    priced_pool = (
        (cloud.pool_compute_eur if cloud.rate_vcpu_eur is not None and cloud.rate_ram_gb_eur is not None else 0.0)
        + (cloud.pool_storage_eur if cloud.rate_storage_gb_eur is not None else 0.0)
    )
    return {
        "available": True,
        "currency": finops.CURRENCY,
        "updated_at": _iso_utc(cloud.sampled_at),
        "cloud": {
            "monthly_cost_eur": cloud.monthly_cost_eur,
            "pools": {
                "compute_eur": round(cloud.pool_compute_eur, 2),
                "storage_eur": round(cloud.pool_storage_eur, 2),
                "platform_eur": round(cloud.pool_platform_eur, 2),
            },
            "rates": {
                "vcpu_month_eur": cloud.rate_vcpu_eur,
                "ram_gb_month_eur": cloud.rate_ram_gb_eur,
                "storage_gb_month_eur": cloud.rate_storage_gb_eur,
                "floating_ip_month_eur": cloud.rate_floating_ip_eur,
            },
            "cost": {
                "allocated_eur": round(cloud.allocated_eur_month, 2),
                "idle_eur": round(cloud.idle_eur_month, 2),
                "platform_eur": round(cloud.pool_platform_eur, 2),
                "utilization_pct": _pct(cloud.allocated_eur_month, priced_pool),
            },
            "capacity": {
                "vcpus": _capacity_row(cloud.physical_vcpus, sellable_vcpus, cloud.allocated_vcpus, cloud.committed_vcpus, cloud.unlimited_vcpus),
                "ram_mb": _capacity_row(cloud.physical_ram_mb, sellable_ram, cloud.allocated_ram_mb, cloud.committed_ram_mb, cloud.unlimited_ram),
                "gigabytes": _capacity_row(cloud.storage_gb, sellable_gb, cloud.allocated_gb, cloud.committed_gb, cloud.unlimited_gb),
            },
            "allocation_ratios": {"cpu": cloud.cpu_allocation_ratio, "ram": cloud.ram_allocation_ratio},
            "compute_nodes": cloud.compute_nodes,
            "oversold": cloud.oversold,
            "capacity_source": details.get("capacity_source", {}),
            "unpriced": details.get("unpriced", []),
            "warnings": details.get("warnings", []),
            "physical_utilization": _physical_utilization(),
        },
        "projects": projects,
    }


@router.get("/report")
def finops_report_json(period: str | None = None, db: Session = Depends(get_db)):
    """Showback/chargeback for one month (`period=YYYY-MM`, default the
    current month): cost per project and per department."""
    try:
        return finops_report.build_report(db, period)
    except ValueError as exc:
        raise HTTPException(422, str(exc))


@router.get("/report.pdf")
def finops_report_pdf(period: str | None = None, db: Session = Depends(get_db)):
    try:
        report = finops_report.build_report(db, period)
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    pdf = finops_report.render_pdf(report)
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="cortex-finops-{report["period"]}.pdf"'},
    )


@router.get("/report.csv")
def finops_report_csv(period: str | None = None, db: Session = Depends(get_db)):
    try:
        report = finops_report.build_report(db, period)
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    return Response(
        content=finops_report.render_csv(report),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="cortex-finops-{report["period"]}.csv"'},
    )


class ProjectSettingsIn(BaseModel):
    # Omit a field to leave it unchanged; send null / "" to clear it.
    department: str | None = Field(default=None, max_length=80)
    monthly_budget_eur: float | None = Field(default=None, ge=0, le=10_000_000)


def _serialize_settings(project_id: str, row: models.ProjectFinopsSetting | None) -> dict:
    return {
        "project_id": project_id,
        "department": row.department if row else None,
        "monthly_budget_eur": row.monthly_budget_eur if row else None,
        "updated_at": _iso_utc(row.updated_at) if row else None,
        "updated_by": row.updated_by if row else None,
    }


@router.get("/projects/{project_id}/settings")
def get_project_settings(project_id: str, db: Session = Depends(get_db)):
    row = db.query(models.ProjectFinopsSetting).filter_by(project_id=project_id).first()
    return _serialize_settings(project_id, row)


@router.put("/projects/{project_id}/settings")
def put_project_settings(
    project_id: str,
    body: ProjectSettingsIn,
    db: Session = Depends(get_db),
    user: models.User = Depends(require_admin),
):
    """Set a project's department and/or monthly budget (admin only --
    changing a budget changes who gets paged and what a department is
    charged). 0 or null clears the budget."""
    row = db.query(models.ProjectFinopsSetting).filter_by(project_id=project_id).first()
    if row is None:
        row = models.ProjectFinopsSetting(project_id=project_id)
        db.add(row)
    fields = body.model_fields_set
    if "department" in fields:
        row.department = (body.department or "").strip() or None
    if "monthly_budget_eur" in fields:
        row.monthly_budget_eur = body.monthly_budget_eur or None
    row.updated_at = datetime.utcnow()
    row.updated_by = user.username
    db.commit()
    return _serialize_settings(project_id, row)
