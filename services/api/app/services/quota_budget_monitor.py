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
  2. `budget_cap` -- a monthly budget configured per project (editable on
     the Quotas & Budget page, or bootstrapped with PROJECT_BUDGETS_EUR
     below). Cortex has no billing system to read -- RIF SAS's cloud is
     self-hosted on flat-rate Hetzner servers (docs/knowledge/README.md:
     "predictable fixed cost"), so there is no per-resource invoice. The
     cost compared to the budget is the project's *share of that fixed
     bill*, derived in services/finops.py (reserved vCPU/RAM/storage x the
     rate the monthly pool works out to per sellable unit) -- not an
     AWS-style price list. Hitting this means the project is reserving
     more of the cloud's cost pool than intended even though it may still
     have plenty of quota headroom left.

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
from . import finops

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

# {project_id_or_name: monthly_budget_eur} bootstrap defaults. A budget saved
# for the project in the UI (models.ProjectFinopsSetting) takes precedence.
# A project with neither has no budget_cap check at all (still gets
# capacity_cap checks) -- there's no sensible "default budget" to assume
# for a project nobody has configured one for.
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


def _list_projects(conn) -> list[tuple[str, str, list[str]]]:
    """[(project_id, project_name, keystone_tags), ...]. Falls back to a
    single "unknown" project derived from the connection's current auth
    scope if the identity API can't list projects (e.g. a reader-only token
    without the `list projects` role) -- quota/budget checks then still
    run against whatever project the reader credential itself is scoped
    to, rather than checking nothing at all.

    Keystone project tags are returned too: `department:<name>` (or
    `dept:<name>`) is the zero-config way to say which department a
    project is charged back to -- see _resolve_department.
    """
    try:
        return [
            (p.id, p.name or p.id, [str(t) for t in (getattr(p, "tags", None) or [])])
            for p in conn.identity.projects()
        ]
    except Exception:
        logger.warning(
            "quota_budget_monitor: could not list projects, falling back to "
            "the connection's own scoped project",
            exc_info=True,
        )
        project_id = getattr(getattr(conn, "current_project", None), "id", None)
        project_name = getattr(getattr(conn, "current_project", None), "name", None)
        if project_id:
            return [(project_id, project_name or project_id, [])]
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

    Reading *another* project's numbers needs the right call per service --
    getting this wrong silently returns the credential's own project for
    every project, which would make every per-project cost wrong:

    - Nova: `GET /limits?tenant_id=<project>` (admin credential). The
      openstacksdk `fetch()` passes kwargs through verbatim, so the
      parameter must be spelled `tenant_id`; a `project_id=` kwarg is not
      a Nova parameter and is ignored (or rejected on newer microversions).
      Reads attributes off the Limits resource's `.absolute` object using
      openstacksdk's *pythonic* names (`instances_used`, `total_cores`,
      ...), not the raw camelCase JSON keys they map from.
    - Cinder: `GET /os-quota-sets/<project>?usage=True` -- Cinder's
      `/limits` is bound to the token's own project and cannot be pointed
      at another one. openstacksdk normalises the response so that
      `quota_set.<resource>` is the limit and `quota_set.usage[<resource>]`
      is `in_use`.
    """
    resources: dict[str, tuple[float, float | None]] = {}

    def _limit_or_none(raw) -> float | None:
        if raw is None or raw < 0:
            return None
        return float(raw)

    try:
        compute_absolute = conn.compute.get_limits(tenant_id=project_id).absolute
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
        quota_set = conn.block_storage.get_quota_set(project_id, usage=True)
        in_use = getattr(quota_set, "usage", None) or {}
        resources["volumes"] = (
            float(in_use.get("volumes", 0) or 0),
            _limit_or_none(getattr(quota_set, "volumes", None)),
        )
        resources["gigabytes"] = (
            float(in_use.get("gigabytes", 0) or 0),
            _limit_or_none(getattr(quota_set, "gigabytes", None)),
        )
    except Exception:
        logger.warning(
            "quota_budget_monitor: Cinder quota usage unavailable for project %s", project_id, exc_info=True
        )

    return resources


def _to_float(raw) -> float | None:
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    return value if value >= 0 else None


def _fetch_cloud_capacity(conn, cfg: finops.CostConfig) -> finops.CloudCapacity:
    """Physical capacity the monthly cost pool is spread over.

    - vCPUs / RAM: sum over enabled Nova hypervisors (`vcpus`, and
      openstacksdk's `memory_size`, which maps Nova's `memory_mb`).
    - storage: sum of Cinder backend pools' `total_capacity_gb` (needs an
      admin-scoped credential; pools reporting "infinite"/"unknown" are
      skipped).
    - anything that can't be read falls back to FINOPS_FALLBACK_* env vars;
      what is still unknown stays None and is reported as a warning, so the
      UI says "capacity unknown" instead of pricing it at a made-up number.
    """
    cap = finops.CloudCapacity()

    try:
        vcpus = ram_mb = 0.0
        nodes = 0
        for hv in conn.compute.hypervisors(details=True):
            status = getattr(hv, "status", None)
            if status and str(status).lower() != "enabled":
                continue
            hv_vcpus = _to_float(getattr(hv, "vcpus", None))
            hv_ram = _to_float(getattr(hv, "memory_size", None))
            if hv_vcpus is None and hv_ram is None:
                continue
            vcpus += hv_vcpus or 0.0
            ram_mb += hv_ram or 0.0
            nodes += 1
        if nodes:
            cap.physical_vcpus, cap.physical_ram_mb, cap.compute_nodes = vcpus, ram_mb, nodes
            cap.source["vcpus"] = cap.source["ram_mb"] = "nova"
    except Exception:
        logger.warning("quota_budget_monitor: Nova hypervisor capacity unavailable", exc_info=True)

    try:
        total_gb = 0.0
        pools = 0
        for pool in conn.block_storage.backend_pools():
            capabilities = getattr(pool, "capabilities", None) or {}
            pool_gb = _to_float(capabilities.get("total_capacity_gb"))
            if pool_gb is None:
                continue
            total_gb += pool_gb
            pools += 1
        if pools:
            cap.storage_gb = total_gb
            cap.source["gigabytes"] = "cinder"
    except Exception:
        logger.warning("quota_budget_monitor: Cinder backend pool capacity unavailable", exc_info=True)

    if cap.physical_vcpus is None and cfg.fallback_physical_vcpus:
        cap.physical_vcpus = cfg.fallback_physical_vcpus
        cap.source["vcpus"] = "env"
    if cap.physical_ram_mb is None and cfg.fallback_physical_ram_gb:
        cap.physical_ram_mb = cfg.fallback_physical_ram_gb * 1024.0
        cap.source["ram_mb"] = "env"
    if cap.storage_gb is None and cfg.fallback_storage_gb:
        cap.storage_gb = cfg.fallback_storage_gb
        cap.source["gigabytes"] = "env"

    if cap.physical_vcpus is None or cap.physical_ram_mb is None:
        cap.warnings.append(
            "Compute capacity unknown (Nova hypervisors unreadable): vCPU/RAM cost cannot be derived. "
            "Set FINOPS_FALLBACK_PHYSICAL_VCPUS / FINOPS_FALLBACK_PHYSICAL_RAM_GB or grant the Cortex "
            "reader credential the admin role for hypervisor stats."
        )
    if cap.storage_gb is None:
        cap.warnings.append(
            "Storage capacity unknown (Cinder backend pools unreadable): storage cost cannot be derived. "
            "Set FINOPS_FALLBACK_STORAGE_GB or grant the reader credential admin rights on Cinder."
        )
    return cap


def _usage_from_limits(project_id: str, project_name: str, limits: dict) -> finops.ProjectUsage:
    def used(key: str) -> float:
        return limits.get(key, (0.0, None))[0]

    def quota(key: str) -> float | None:
        return limits.get(key, (0.0, None))[1]

    return finops.ProjectUsage(
        project_id=project_id,
        project_name=project_name,
        instances=used("instances"),
        vcpus=used("vcpus"),
        ram_mb=used("ram_mb"),
        volumes=used("volumes"),
        gigabytes=used("gigabytes"),
        floating_ips=used("floating_ips"),
        quota_vcpus=quota("vcpus"),
        quota_ram_mb=quota("ram_mb"),
        quota_gigabytes=quota("gigabytes"),
    )


def _resolve_budget(project_id: str, project_name: str, settings: dict, env_budgets: dict) -> float | None:
    """UI-saved budget > env bootstrap (by id, then name). None / <= 0 =
    no budget configured, so no budget_cap check."""
    row = settings.get(project_id)
    if row is not None and row.monthly_budget_eur:
        return float(row.monthly_budget_eur) if row.monthly_budget_eur > 0 else None
    value = env_budgets.get(project_id) or env_budgets.get(project_name)
    return float(value) if value and value > 0 else None


def resolve_department(
    project_id: str, project_name: str, tags: list[str], settings: dict, env_departments: dict
) -> str:
    """Department a project is charged back to. Precedence: department
    saved in the UI > FINOPS_PROJECT_DEPARTMENTS env > Keystone project tag
    `department:<name>` / `dept:<name>` > "Unassigned"."""
    row = settings.get(project_id)
    if row is not None and row.department and row.department.strip():
        return row.department.strip()
    mapped = env_departments.get(project_id) or env_departments.get(project_name)
    if mapped:
        return mapped
    for tag in tags:
        key, _, value = tag.partition(":")
        if key.strip().lower() in ("department", "dept") and value.strip():
            return value.strip()
    return UNASSIGNED_DEPARTMENT


UNASSIGNED_DEPARTMENT = finops.UNASSIGNED_DEPARTMENT


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
        f"BUDGET CAP breach: project '{project_name}' is reserving resources "
        f"that cost EUR {used:.2f}/month ({ratio:.0%} of its EUR {limit:.2f} "
        f"monthly budget cap). This is a spending limit, not an "
        f"infrastructure quota -- the project may still have plenty of "
        f"OpenStack quota headroom left; it is simply taking a bigger slice "
        f"of the cloud's fixed monthly cost than budgeted. Cost = reserved "
        f"vCPU/RAM/storage x the cloud's derived unit rates."
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


def _hour_bucket(ts: datetime) -> datetime:
    return ts.replace(minute=0, second=0, microsecond=0)


def _upsert_project_sample(
    db: Session, usage: finops.ProjectUsage, cost: finops.ProjectCost, department: str, bucket: datetime
) -> None:
    row = (
        db.query(models.FinopsProjectSample)
        .filter_by(project_id=usage.project_id, sampled_at=bucket)
        .first()
    )
    if row is None:
        row = models.FinopsProjectSample(project_id=usage.project_id, sampled_at=bucket)
        db.add(row)
    row.project_name = usage.project_name
    row.department = department
    row.instances = usage.instances
    row.vcpus = usage.vcpus
    row.ram_mb = usage.ram_mb
    row.volumes = usage.volumes
    row.gigabytes = usage.gigabytes
    row.floating_ips = usage.floating_ips
    row.compute_eur_month = cost.compute_eur
    row.storage_eur_month = cost.storage_eur
    row.network_eur_month = cost.network_eur
    row.platform_eur_month = cost.platform_eur
    row.idle_eur_month = cost.idle_eur


def _upsert_cloud_sample(
    db: Session,
    *,
    bucket: datetime,
    cfg: finops.CostConfig,
    capacity: finops.CloudCapacity,
    usages: list[finops.ProjectUsage],
    costs: finops.CloudCosts,
) -> None:
    row = db.query(models.FinopsCloudSample).filter_by(sampled_at=bucket).first()
    if row is None:
        row = models.FinopsCloudSample(sampled_at=bucket)
        db.add(row)
    commitment = finops.quota_commitment(usages)
    row.physical_vcpus = capacity.physical_vcpus
    row.physical_ram_mb = capacity.physical_ram_mb
    row.storage_gb = capacity.storage_gb
    row.compute_nodes = capacity.compute_nodes
    row.cpu_allocation_ratio = cfg.cpu_allocation_ratio
    row.ram_allocation_ratio = cfg.ram_allocation_ratio
    row.allocated_vcpus = sum(u.vcpus for u in usages)
    row.allocated_ram_mb = sum(u.ram_mb for u in usages)
    row.allocated_gb = sum(u.gigabytes for u in usages)
    row.committed_vcpus = commitment["vcpus"] or 0.0
    row.committed_ram_mb = commitment["ram_mb"] or 0.0
    row.committed_gb = commitment["gigabytes"] or 0.0
    row.unlimited_vcpus = int(commitment["unlimited_vcpus"] or 0)
    row.unlimited_ram = int(commitment["unlimited_ram_mb"] or 0)
    row.unlimited_gb = int(commitment["unlimited_gigabytes"] or 0)
    row.monthly_cost_eur = cfg.monthly_cost_eur
    row.pool_compute_eur = costs.pools.compute
    row.pool_storage_eur = costs.pools.storage
    row.pool_platform_eur = costs.pools.platform
    row.rate_vcpu_eur = costs.rates.vcpu
    row.rate_ram_gb_eur = costs.rates.ram_gb
    row.rate_storage_gb_eur = costs.rates.storage_gb
    row.rate_floating_ip_eur = costs.rates.floating_ip
    row.allocated_eur_month = costs.allocated_eur
    row.idle_eur_month = costs.idle_eur
    row.oversold = costs.oversold
    row.details = {
        "capacity_source": capacity.source,
        "unpriced": costs.unpriced,
        "warnings": capacity.warnings,
    }


def check_quota_and_budget(db: Session, conn=None) -> dict:
    """One pass, three jobs:

    1. for every project, check every OpenStack quota (capacity_cap);
    2. derive the cloud's unit rates from its fixed monthly cost and
       physical capacity (services/finops.py), price every project's
       reserved resources with them and -- if a budget is configured --
       check the result against it (budget_cap);
    3. meter: upsert this hour's per-project and cloud-level FinOps samples,
       which the showback/chargeback reports are summed from.

    Upserts one QuotaAlert row per (project, breach_type, resource) slot --
    including rows that are currently "normal" -- same upsert-not-delete
    convention as AnomalyFlag.

    Returns a small summary dict (projects checked, alerts currently at
    warning/critical) for the caller to log/record, mirroring
    topology_sync.sync_topology()'s summary return.
    """
    conn = conn or _connect()
    now = datetime.utcnow()
    bucket = _hour_bucket(now)
    cfg = finops.load_config()
    env_budgets = _load_project_budgets()
    env_departments = finops.load_project_departments()
    settings = {s.project_id: s for s in db.query(models.ProjectFinopsSetting).all()}

    projects_checked = 0
    warning_count = 0
    critical_count = 0

    usages: list[finops.ProjectUsage] = []
    departments: dict[str, str] = {}

    for project_id, project_name, tags in _list_projects(conn):
        projects_checked += 1
        limits = _fetch_project_limits(conn, project_id)
        usages.append(_usage_from_limits(project_id, project_name, limits))
        departments[project_id] = resolve_department(project_id, project_name, tags, settings, env_departments)

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

    capacity = _fetch_cloud_capacity(conn, cfg)
    pools = finops.split_pools(cfg)
    rates = finops.derive_rates(pools, finops.sellable_capacity(capacity, cfg), cfg)
    costs = finops.allocate_costs(usages, pools, rates)
    if costs.unpriced:
        logger.warning(
            "quota_budget_monitor: capacity unknown for %s -- those resources are not priced "
            "and budget_cap checks may under-report",
            ", ".join(costs.unpriced),
        )

    for usage in usages:
        project_cost = costs.projects[usage.project_id]
        budget_limit = _resolve_budget(usage.project_id, usage.project_name, settings, env_budgets)
        # Budget is compared to *direct* cost -- the part a project's own
        # reservations control. Its platform/idle share moves with what
        # other projects do, so alerting on it would page the wrong team.
        if budget_limit and not costs.unpriced:
            direct = project_cost.direct_eur
            ratio = direct / budget_limit
            severity = _severity(ratio, BUDGET_WARNING_RATIO, BUDGET_CRITICAL_RATIO)
            message = (
                _budget_message(usage.project_name, direct, budget_limit, ratio)
                if severity != "normal"
                else None
            )
            _upsert_alert(
                db,
                project_id=usage.project_id,
                project_name=usage.project_name,
                breach_type="budget_cap",
                resource="estimated_cost_eur",
                used=direct,
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

        _upsert_project_sample(db, usage, project_cost, departments[usage.project_id], bucket)

    _upsert_cloud_sample(db, bucket=bucket, cfg=cfg, capacity=capacity, usages=usages, costs=costs)

    db.commit()
    summary = {
        "projects_checked": projects_checked,
        "warning_count": warning_count,
        "critical_count": critical_count,
    }
    logger.info("Quota/budget check done: %s", summary)
    return summary
