"""FinOps cost model for a *private* OpenStack cloud.

Why this isn't an AWS-style price list
--------------------------------------
Public clouds sell resources at a market price per unit-hour. A private
cloud like RIF SAS's (self-hosted on flat-rate Hetzner dedicated servers,
see docs/knowledge/README.md: "predictable fixed cost") has no per-unit
invoice at all: the bill is the same whether the cloud is 5% or 95% full.
A per-vCPU "price" invented from thin air therefore says nothing true about
cost. What a cloud owner actually needs to know is:

1. **How much of what we pay for is actually handed out?** The monthly bill
   is a fixed *cost pool*. Every project's share is a slice of that pool
   and whatever nobody has reserved is *idle capacity* -- money spent on
   hardware that produces nothing.
2. **Who is consuming the pool?** Showback (informational) / chargeback
   (internal recharge) by project and department.
3. **Are we promising more than we own?** The sum of project quotas can
   exceed physical capacity (overcommit), so the cloud can run dry even
   while every project is under its own quota.

So rates are *derived*, never invented:

    unit rate = (pool cost allocated to that resource) / (sellable units)

    sellable vCPUs = physical vCPUs x cpu_allocation_ratio
    sellable RAM   = physical RAM   x ram_allocation_ratio
    sellable disk  = Cinder backend pool capacity

Three pools split the monthly bill (all env-tunable, see `load_config`):

- `compute`  -> priced per vCPU and per GB RAM (cpu/ram split below)
- `storage`  -> priced per GB of Cinder volume
- `platform` -> controller / network / monitoring nodes. Nobody "uses"
  these directly, so it is *shared overhead*, spread across projects in
  proportion to what each one consumes.

Resources are charged by what a project has **reserved** (Nova/Cinder
"used" quota = flavor sizes of existing VMs and volume sizes), not by
CPU-seconds burned: a stopped or idle VM still occupies hypervisor
capacity nobody else can have, which is exactly what is being paid for.
(This is also how OpenStack's own rating service, CloudKitty, charges
flavor-based resources.)

Per project the model yields:

- `direct`        = reserved units x derived rate (+ optional pass-through)
- `platform_share`= platform pool x (this project's direct / total direct)
- `idle_share`    = unreserved compute+storage pool, same pro-rata split
- `fully_loaded`  = direct + platform_share + idle_share  (full recovery:
                    all projects together pay exactly the monthly bill)

Budget-cap alerts compare *direct* cost to the project's budget -- that is
the part a project's own decisions control. Idle share moves when *other*
projects start or stop things, so alerting on it would page the wrong team.

This module is pure (no DB, no OpenStack): quota_budget_monitor.py feeds
it inputs, finops_report.py turns its outputs into reports.
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

CURRENCY = "EUR"
UNASSIGNED_DEPARTMENT = "Unassigned"


# --------------------------------------------------------------- config --
def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    try:
        return float(raw)
    except ValueError:
        logger.warning("%s=%r is not a number, using %s", name, raw, default)
        return default


def _env_optional_float(name: str) -> float | None:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return None
    try:
        return float(raw)
    except ValueError:
        logger.warning("%s=%r is not a number, ignoring", name, raw)
        return None


@dataclass(frozen=True)
class CostConfig:
    # Total fixed monthly bill (servers, bandwidth, ...). Default is the
    # ~292 EUR/month Hetzner total quoted in quota_budget_monitor's original
    # notes for controller+compute1+compute2+storage -- override it with
    # the real invoice figure.
    monthly_cost_eur: float = 292.0
    # How the bill splits across the three pools. Normalised to sum to 1.
    share_compute: float = 0.50
    share_storage: float = 0.30
    share_platform: float = 0.20
    # Inside the compute pool: how much is "paid for" by vCPUs vs RAM.
    cpu_share_of_compute: float = 0.60
    # Allocation (overcommit) ratios the cloud is *planned* to be sold at.
    # Nova's own defaults are 16:1 CPU / 1.5:1 RAM; 4:1 / 1:1 is a
    # conservative planning figure for a training cloud.
    cpu_allocation_ratio: float = 4.0
    ram_allocation_ratio: float = 1.0
    # Used only when Nova/Cinder capacity cannot be read from the API.
    fallback_physical_vcpus: float | None = None
    fallback_physical_ram_gb: float | None = None
    fallback_storage_gb: float | None = None
    # Optional pass-through for a paid public IPv4 (Hetzner bills extra
    # addresses). 0 when addresses are already inside the monthly pool.
    floating_ip_month_eur: float = 0.0


def load_config() -> CostConfig:
    shares = [
        max(_env_float("FINOPS_POOL_SHARE_COMPUTE", 0.50), 0.0),
        max(_env_float("FINOPS_POOL_SHARE_STORAGE", 0.30), 0.0),
        max(_env_float("FINOPS_POOL_SHARE_PLATFORM", 0.20), 0.0),
    ]
    total = sum(shares)
    if total <= 0:
        shares, total = [0.5, 0.3, 0.2], 1.0
    return CostConfig(
        monthly_cost_eur=max(_env_float("FINOPS_MONTHLY_COST_EUR", 292.0), 0.0),
        share_compute=shares[0] / total,
        share_storage=shares[1] / total,
        share_platform=shares[2] / total,
        cpu_share_of_compute=min(max(_env_float("FINOPS_CPU_SHARE_OF_COMPUTE", 0.60), 0.0), 1.0),
        cpu_allocation_ratio=max(_env_float("FINOPS_CPU_ALLOCATION_RATIO", 4.0), 0.1),
        ram_allocation_ratio=max(_env_float("FINOPS_RAM_ALLOCATION_RATIO", 1.0), 0.1),
        fallback_physical_vcpus=_env_optional_float("FINOPS_FALLBACK_PHYSICAL_VCPUS"),
        fallback_physical_ram_gb=_env_optional_float("FINOPS_FALLBACK_PHYSICAL_RAM_GB"),
        fallback_storage_gb=_env_optional_float("FINOPS_FALLBACK_STORAGE_GB"),
        floating_ip_month_eur=max(_env_float("FINOPS_FLOATING_IP_MONTH_EUR", 0.0), 0.0),
    )


def load_project_departments() -> dict[str, str]:
    """{project_id_or_name: department} from FINOPS_PROJECT_DEPARTMENTS
    (JSON). The per-project DB setting (editable in the UI) wins over this,
    and this wins over Keystone project tags."""
    raw = os.environ.get("FINOPS_PROJECT_DEPARTMENTS", "{}")
    try:
        parsed = json.loads(raw)
        return {str(k): str(v) for k, v in parsed.items() if str(v).strip()}
    except (json.JSONDecodeError, TypeError, AttributeError):
        logger.exception("FINOPS_PROJECT_DEPARTMENTS is not a valid JSON object, ignoring")
        return {}


# ------------------------------------------------------------- capacity --
@dataclass
class CloudCapacity:
    """Physical capacity of the cloud, as far as it could be discovered."""

    physical_vcpus: float | None = None
    physical_ram_mb: float | None = None
    storage_gb: float | None = None
    compute_nodes: int = 0
    source: dict[str, str] = field(default_factory=dict)  # resource -> "nova"|"cinder"|"env"
    warnings: list[str] = field(default_factory=list)


def sellable_capacity(cap: CloudCapacity, cfg: CostConfig) -> dict[str, float | None]:
    """Capacity after applying the planned allocation ratios -- the number
    of units the monthly pool is spread across."""
    return {
        "vcpus": cap.physical_vcpus * cfg.cpu_allocation_ratio if cap.physical_vcpus else None,
        "ram_mb": cap.physical_ram_mb * cfg.ram_allocation_ratio if cap.physical_ram_mb else None,
        "gigabytes": cap.storage_gb if cap.storage_gb else None,
    }


@dataclass(frozen=True)
class Pools:
    compute: float
    storage: float
    platform: float

    @property
    def total(self) -> float:
        return self.compute + self.storage + self.platform


def split_pools(cfg: CostConfig) -> Pools:
    return Pools(
        compute=cfg.monthly_cost_eur * cfg.share_compute,
        storage=cfg.monthly_cost_eur * cfg.share_storage,
        platform=cfg.monthly_cost_eur * cfg.share_platform,
    )


@dataclass(frozen=True)
class Rates:
    """EUR per unit per month, derived from pool / sellable capacity.
    None means the capacity behind that rate is unknown, so it cannot be
    priced honestly (rather than silently pricing it at 0)."""

    vcpu: float | None
    ram_gb: float | None
    storage_gb: float | None
    floating_ip: float


def derive_rates(pools: Pools, sellable: dict[str, float | None], cfg: CostConfig) -> Rates:
    vcpus = sellable.get("vcpus")
    ram_mb = sellable.get("ram_mb")
    gb = sellable.get("gigabytes")
    cpu_pool = pools.compute * cfg.cpu_share_of_compute
    ram_pool = pools.compute * (1.0 - cfg.cpu_share_of_compute)
    return Rates(
        vcpu=cpu_pool / vcpus if vcpus else None,
        ram_gb=ram_pool / (ram_mb / 1024.0) if ram_mb else None,
        storage_gb=pools.storage / gb if gb else None,
        floating_ip=cfg.floating_ip_month_eur,
    )


# ----------------------------------------------------------- allocation --
@dataclass
class ProjectUsage:
    """What one project currently has reserved, plus its quota ceilings.
    `quota_*` is None when the quota is unlimited."""

    project_id: str
    project_name: str
    instances: float = 0.0
    vcpus: float = 0.0
    ram_mb: float = 0.0
    volumes: float = 0.0
    gigabytes: float = 0.0
    floating_ips: float = 0.0
    quota_vcpus: float | None = None
    quota_ram_mb: float | None = None
    quota_gigabytes: float | None = None


@dataclass
class ProjectCost:
    project_id: str
    project_name: str
    compute_eur: float = 0.0  # vCPU + RAM, per month run-rate
    storage_eur: float = 0.0
    network_eur: float = 0.0  # optional floating-IP pass-through
    platform_eur: float = 0.0
    idle_eur: float = 0.0

    @property
    def direct_eur(self) -> float:
        return self.compute_eur + self.storage_eur + self.network_eur

    @property
    def fully_loaded_eur(self) -> float:
        return self.direct_eur + self.platform_eur + self.idle_eur


@dataclass
class CloudCosts:
    pools: Pools
    rates: Rates
    projects: dict[str, ProjectCost]
    # Monthly run-rate EUR of reserved-and-paid-for capacity vs capacity
    # nobody has reserved. idle_compute/idle_storage are what a project
    # `idle_share` is carved from.
    allocated_compute_eur: float
    allocated_storage_eur: float
    idle_compute_eur: float
    idle_storage_eur: float
    oversold: bool  # reservations exceed planned sellable capacity
    unpriced: list[str]  # resources whose capacity was unknown

    @property
    def idle_eur(self) -> float:
        return self.idle_compute_eur + self.idle_storage_eur

    @property
    def allocated_eur(self) -> float:
        return self.allocated_compute_eur + self.allocated_storage_eur


def allocate_costs(usages: list[ProjectUsage], pools: Pools, rates: Rates) -> CloudCosts:
    unpriced = [
        label
        for label, rate in (("vCPU", rates.vcpu), ("RAM", rates.ram_gb), ("storage", rates.storage_gb))
        if rate is None
    ]
    projects: dict[str, ProjectCost] = {}
    for u in usages:
        cost = ProjectCost(project_id=u.project_id, project_name=u.project_name)
        cost.compute_eur = u.vcpus * (rates.vcpu or 0.0) + (u.ram_mb / 1024.0) * (rates.ram_gb or 0.0)
        cost.storage_eur = u.gigabytes * (rates.storage_gb or 0.0)
        cost.network_eur = u.floating_ips * rates.floating_ip
        projects[u.project_id] = cost

    allocated_compute = sum(p.compute_eur for p in projects.values())
    allocated_storage = sum(p.storage_eur for p in projects.values())

    # Only resources we could actually price take part in the idle maths --
    # an unknown-capacity resource would otherwise show its whole pool as
    # idle, which is a claim we have no evidence for.
    priced_compute_pool = pools.compute if rates.vcpu is not None and rates.ram_gb is not None else 0.0
    priced_storage_pool = pools.storage if rates.storage_gb is not None else 0.0
    idle_compute = max(priced_compute_pool - allocated_compute, 0.0)
    idle_storage = max(priced_storage_pool - allocated_storage, 0.0)
    oversold = (priced_compute_pool > 0 and allocated_compute > priced_compute_pool + 1e-9) or (
        priced_storage_pool > 0 and allocated_storage > priced_storage_pool + 1e-9
    )

    # Platform overhead and idle capacity are spread pro rata to each
    # project's *priced* direct cost (compute + storage, pass-through
    # excluded -- it is a separate pass-through, not a slice of the pool).
    weight_total = sum(p.compute_eur + p.storage_eur for p in projects.values())
    if weight_total > 0:
        for p in projects.values():
            w = (p.compute_eur + p.storage_eur) / weight_total
            p.platform_eur = pools.platform * w
            p.idle_eur = (idle_compute + idle_storage) * w

    return CloudCosts(
        pools=pools,
        rates=rates,
        projects=projects,
        allocated_compute_eur=allocated_compute,
        allocated_storage_eur=allocated_storage,
        idle_compute_eur=idle_compute,
        idle_storage_eur=idle_storage,
        oversold=oversold,
        unpriced=unpriced,
    )


def quota_commitment(usages: list[ProjectUsage]) -> dict[str, float | int | None]:
    """Sum of project quota ceilings: what the cloud has *promised*, as
    opposed to what is currently reserved. A project with an unlimited
    quota makes the promise unbounded for that resource -- counted in
    `unlimited_*` instead of silently treated as 0."""
    out: dict[str, float | int | None] = {}
    for key, attr in (("vcpus", "quota_vcpus"), ("ram_mb", "quota_ram_mb"), ("gigabytes", "quota_gigabytes")):
        values = [getattr(u, attr) for u in usages]
        out[key] = sum(v for v in values if v is not None)
        out[f"unlimited_{key}"] = sum(1 for v in values if v is None)
    return out


def hours_in_month(year: int, month: int) -> int:
    import calendar

    return calendar.monthrange(year, month)[1] * 24
