"""Quota/budget breach alerts.

This is deliberately a *separate* alert type from anomaly_detector.py's
AnomalyFlag rows, not a new severity tier bolted onto them:

- anomaly_detector.py asks "is this host's measured resource usage
  (cpu_usage/ram_usage) behaving abnormally for it" -- a statistical
  question, scored per-host against that host's own history.
- this module asks "is this OpenStack *project* up against a hard cap"
  -- a threshold question, scored per-project against two unrelated
  ceilings:

  1. `capacity_cap` -- an actual OpenStack quota (Nova/Cinder `GET
     /limits`, see infra.md's "Quotas par projet" table: VMs, vCPUs,
     RAM, IPs flottantes, Volumes). Hitting this means the project
     physically cannot provision more of that resource until the quota
     itself is raised -- no amount of budget helps.
  2. `budget_cap` -- an estimated monthly spend ceiling configured per
     project (PROJECT_BUDGETS_EUR below). Cortex has no billing system
     (RIF SAS's cloud is self-hosted on flat-rate Hetzner servers, see
     infra.md section 2 -- there's no per-resource invoice to read), so
     this is a rough internal chargeback estimate, not a real bill.
     Hitting this means the project is costing more than intended even
     though it may still have plenty of quota headroom left.

  A project can breach one, the other, both, or neither independently,
  so every alert this module raises is unambiguous about which cap it
  is -- see _capacity_message/_budget_message below. Never a bare
  "threshold exceeded".

Auth/connection: same `openstack.connect(cloud=OS_CLOUD)` pattern as
topology_sync.py's `_connect()` -- this is a second, independent
OpenStack polling loop (quotas/limits, not hypervisors/networks), kept
in its own module rather than folded into topology_sync so a slow or
failing quota pass never blocks the topology graph sync, and vice
versa.
"""
import json
import logging
import os
from datetime import datetime

import openstack
from sqlalchemy.orm import Session

from .. import models

logger = logging.getLogger(__name__)

OS_CLOUD = os.environ.get("OS_CLOUD", "cortex-reader")

# Ratios (used/limit) at which a capacity_cap / budget_cap alert
# escalates. Kept as two independent pairs (rather than one shared pair)
# since a quota breach and a budget breach have different real-world
# urgency -- a full quota blocks work immediately, a budget overrun
# usually doesn't -- and an operator may reasonably want to tune them
# differently.
CAPACITY_WARNING_RATIO = float(os.environ.get("QUOTA_CAPACITY_WARNING_RATIO", "0.8"))
CAPACITY_CRITICAL_RATIO = float(os.environ.get("QUOTA_CAPACITY_CRITICAL_RATIO", "0.95"))
BUDGET_WARNING_RATIO = float(os.environ.get("QUOTA_BUDGET_WARNING_RATIO", "0.8"))
BUDGET_CRITICAL_RATIO = float(os.environ.get("QUOTA_BUDGET_CRITICAL_RATIO", "0.95"))

# Rough internal chargeback rates, in EUR/month per unit, used only to
# turn a project's *current* quota usage into an estimated monthly cost
# for the budget_cap check -- not a real billing feed (see module
# docstring). Defaults are back-of-envelope numbers derived from
# infra.md's Hetzner server costs (~292 EUR/month total across
# controller+compute1+compute2+storage for ~24 vCPU / ~445 GB RAM /
# ~9.4 TB disk of raw capacity) -- deliberately overridable per
# deployment via env, since the "right" number depends on how RIF SAS
# actually wants to allocate that flat monthly cost across projects.
COST_PER_VCPU_MONTH_EUR = float(os.environ.get("QUOTA_COST_PER_VCPU_MONTH_EUR", "2.0"))
COST_PER_GB_RAM_MONTH_EUR = float(os.environ.get("QUOTA_COST_PER_GB_RAM_MONTH_EUR", "0.30"))
COST_PER_GB_STORAGE_MONTH_EUR = float(os.environ.get("QUOTA_COST_PER_GB_STORAGE_MONTH_EUR", "0.05"))

# {project_id_or_name: monthly_budget_eur}. A project absent from this
# dict has no budget_cap check at all (still gets capacity_cap checks) --
# there's no sensible "default budget" to assume for a project nobody
# has configured one for.
_PROJECT_BUDGETS_RAW = os.environ.get("QUOTA_PROJECT_BUDGETS_EUR", "{}")


def _load_project_budgets() -> dict[str, float]:
    try:
        parsed = json.loads(_PROJECT_BUDGETS_RAW)
        return {str(k): float(v) for k, v in parsed.items()}
    except (json.JSONDecodeError, TypeError, ValueError):
        logger.exception("QUOTA_PROJECT_BUDGETS_EUR is not valid JSON, ignoring")
        return {}


def _connect():
    """Thin wrapper so tests can monkeypatch the connection, same as
    topology_sync._connect()."""
    return openstack.connect(cloud=OS_CLOUD)


def _severity(ratio: float, warning_ratio: float, critical_ratio: float) -> str:
    if ratio >= critical_ratio:
        return "critical"
    if ratio >= warning_ratio:
        return "warning"
    return "normal"


def _list_projects(conn) -> list[tuple[str, str]]:
    """[(project_id, project_name), ...]. Falls back to a single
    "unknown" project derived from the connection's current auth scope
    if the identity API can't list projects (e.g. a reader-only token
    without the `list projects` role) -- quota/budget checks then still
    run against whatever project the reader credential itself is scoped
    to, rather than checking nothing at all.
    """
    try:
        return [(p.id, p.name or p.id) for p in conn.identity.projects()]
    except Exception:
        logger.warning(
            "quota_budget_monitor: could not list projects, falling back to "
            "the connection's own scoped project",
            exc_info=True,
        )
        project_id = getattr(getattr(conn, "current_project", None), "id", None)
        project_name = getattr(getattr(conn, "current_project", None), "name", None)
        if project_id:
            return [(project_id, project_name or project_id)]
        return []


def _fetch_project_limits(conn, project_id: str) -> dict[str, tuple[float, float | None]]:
    """{resource: (used, limit)}. `limit` is None for a resource that's
    genuinely unlimited (OpenStack reports -1 for "no quota set") --
    callers must skip the capacity_cap check for those, since "used /
    unlimited" isn't a meaningful ratio.

    Resource keys line up with infra.md's "Quotas par projet" table:
    VMs -> instances, vCPUs -> vcpus, RAM -> ram_mb, IPs flottantes ->
    floating_ips, Volumes -> volumes (+ gigabytes, Cinder's other quota
    dimension, not in that table but just as real a cap).

    Reads attributes directly off the fetched Limits resource's
    `.absolute` object (openstacksdk's `AbsoluteLimits` /
    `AbsoluteLimit`) using its *pythonic* attribute names
    (`instances_used`, `total_cores`, `total_volumes_used`, ...) --
    NOT the raw camelCase JSON keys (`totalInstancesUsed`,
    `maxTotalCores`, ...) those attributes are mapped from. `dict()`-ing
    an openstacksdk resource does not recover the original JSON keys
    (see openstack.resource.Resource -- it dict-ifies to its Python
    property names, several of them duplicated under both a `max_*` and
    an unprefixed alias), so this reads named attributes with `getattr`
    instead of trying to normalize a dict.
    """
    resources: dict[str, tuple[float, float | None]] = {}

    def _limit_or_none(raw) -> float | None:
        if raw is None or raw < 0:
            return None
        return float(raw)

    try:
        compute_absolute = conn.compute.get_limits(project_id=project_id).absolute
        resources["instances"] = (
            float(getattr(compute_absolute, "instances_used", 0) or 0),
            _limit_or_none(getattr(compute_absolute, "instances", None)),
        )
        resources["vcpus"] = (
            float(getattr(compute_absolute, "total_cores_used", 0) or 0),
            _limit_or_none(getattr(compute_absolute, "total_cores", None)),
        )
        resources["ram_mb"] = (
            float(getattr(compute_absolute, "total_ram_used", 0) or 0),
            _limit_or_none(getattr(compute_absolute, "total_ram", None)),
        )
        resources["floating_ips"] = (
            float(getattr(compute_absolute, "floating_ips_used", 0) or 0),
            _limit_or_none(getattr(compute_absolute, "floating_ips", None)),
        )
    except Exception:
        logger.warning(
            "quota_budget_monitor: Nova limits unavailable for project %s", project_id, exc_info=True
        )

    try:
        volume_absolute = conn.block_storage.get_limits(project=project_id).absolute
        resources["volumes"] = (
            float(getattr(volume_absolute, "total_volumes_used", 0) or 0),
            _limit_or_none(getattr(volume_absolute, "max_total_volumes", None)),
        )
        resources["gigabytes"] = (
            float(getattr(volume_absolute, "total_gigabytes_used", 0) or 0),
            _limit_or_none(getattr(volume_absolute, "max_total_volume_gigabytes", None)),
        )
    except Exception:
        logger.warning(
            "quota_budget_monitor: Cinder limits unavailable for project %s", project_id, exc_info=True
        )

    return resources


def _estimate_monthly_cost_eur(limits: dict[str, tuple[float, float | None]]) -> float:
    vcpus_used = limits.get("vcpus", (0.0, None))[0]
    ram_gb_used = limits.get("ram_mb", (0.0, None))[0] / 1024.0
    gigabytes_used = limits.get("gigabytes", (0.0, None))[0]
    return (
        vcpus_used * COST_PER_VCPU_MONTH_EUR
        + ram_gb_used * COST_PER_GB_RAM_MONTH_EUR
        + gigabytes_used * COST_PER_GB_STORAGE_MONTH_EUR
    )


_RESOURCE_LABEL = {
    "instances": "VM instances",
    "vcpus": "vCPUs",
    "ram_mb": "RAM",
    "floating_ips": "floating IPs",
    "volumes": "volumes",
    "gigabytes": "volume storage",
}


def _capacity_message(project_name: str, resource: str, used: float, limit: float, ratio: float) -> str:
    label = _RESOURCE_LABEL.get(resource, resource)
    return (
        f"CAPACITY CAP breach: project '{project_name}' is using "
        f"{used:g}/{limit:g} {label} ({ratio:.0%} of its OpenStack quota). "
        f"This is an infrastructure capacity limit, not a spending limit -- "
        f"raise the quota (openstack quota set) to unblock further "
        f"provisioning, a budget increase won't help."
    )


def _budget_message(project_name: str, used: float, limit: float, ratio: float) -> str:
    return (
        f"BUDGET CAP breach: project '{project_name}' has an estimated "
        f"spend of EUR {used:.2f}/EUR {limit:.2f} this month ({ratio:.0%} of "
        f"its configured budget cap). This is a cost limit, not an "
        f"infrastructure quota -- the project may still have plenty of "
        f"OpenStack quota headroom left; it is simply costing more than "
        f"intended."
    )


def _upsert_alert(
    db: Session,
    *,
    project_id: str,
    project_name: str,
    breach_type: str,
    resource: str,
    used: float,
    limit: float,
    ratio: float,
    severity: str,
    message: str | None,
    now: datetime,
) -> None:
    existing = (
        db.query(models.QuotaAlert)
        .filter_by(project_id=project_id, breach_type=breach_type, resource=resource)
        .first()
    )
    if existing:
        existing.project_name = project_name
        existing.used = used
        existing.limit = limit
        existing.ratio = ratio
        existing.severity = severity
        existing.message = message
        existing.detected_at = now
    else:
        db.add(
            models.QuotaAlert(
                project_id=project_id,
                project_name=project_name,
                breach_type=breach_type,
                resource=resource,
                used=used,
                limit=limit,
                ratio=ratio,
                severity=severity,
                message=message,
                detected_at=now,
            )
        )


def check_quota_and_budget(db: Session, conn=None) -> dict:
    """One pass: for every project, check every OpenStack quota
    (capacity_cap) and, if a budget is configured for it, its estimated
    spend (budget_cap). Upserts one QuotaAlert row per (project,
    breach_type, resource) slot -- including rows that are currently
    "normal" -- same upsert-not-delete convention as AnomalyFlag.

    Returns a small summary dict (projects checked, alerts currently at
    warning/critical) for the caller to log/record, mirroring
    topology_sync.sync_topology()'s summary return.
    """
    conn = conn or _connect()
    now = datetime.utcnow()
    budgets = _load_project_budgets()

    projects_checked = 0
    warning_count = 0
    critical_count = 0

    for project_id, project_name in _list_projects(conn):
        projects_checked += 1
        limits = _fetch_project_limits(conn, project_id)

        for resource, (used, limit) in limits.items():
            if limit is None:
                continue  # unlimited quota -- nothing to breach
            ratio = used / limit if limit > 0 else (1.0 if used > 0 else 0.0)
            severity = _severity(ratio, CAPACITY_WARNING_RATIO, CAPACITY_CRITICAL_RATIO)
            message = (
                _capacity_message(project_name, resource, used, limit, ratio)
                if severity != "normal"
                else None
            )
            _upsert_alert(
                db,
                project_id=project_id,
                project_name=project_name,
                breach_type="capacity_cap",
                resource=resource,
                used=used,
                limit=limit,
                ratio=ratio,
                severity=severity,
                message=message,
                now=now,
            )
            if severity == "warning":
                warning_count += 1
            elif severity == "critical":
                critical_count += 1

        budget_limit = budgets.get(project_id) or budgets.get(project_name)
        if budget_limit:
            estimated_cost = _estimate_monthly_cost_eur(limits)
            ratio = estimated_cost / budget_limit if budget_limit > 0 else 1.0
            severity = _severity(ratio, BUDGET_WARNING_RATIO, BUDGET_CRITICAL_RATIO)
            message = (
                _budget_message(project_name, estimated_cost, budget_limit, ratio)
                if severity != "normal"
                else None
            )
            _upsert_alert(
                db,
                project_id=project_id,
                project_name=project_name,
                breach_type="budget_cap",
                resource="estimated_cost_eur",
                used=estimated_cost,
                limit=budget_limit,
                ratio=ratio,
                severity=severity,
                message=message,
                now=now,
            )
            if severity == "warning":
                warning_count += 1
            elif severity == "critical":
                critical_count += 1

    db.commit()
    summary = {
        "projects_checked": projects_checked,
        "warning_count": warning_count,
        "critical_count": critical_count,
    }
    logger.info("Quota/budget check done: %s", summary)
    return summary
