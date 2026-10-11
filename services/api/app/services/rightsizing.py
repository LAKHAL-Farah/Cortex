"""Roadmap 4.10 -- right-sizing recommendations for under-utilised VMs.

*Recommendation only.* Nothing in this module (or its router) resizes, stops
or otherwise touches a VM. It reads utilisation, decides which VMs are
carrying more vCPU/RAM than they use, and says what a smaller allocation
would look like, what it would free up, and how risky the change is. A human
(or, later, the approval-gated remediation flow) acts on it.

Pure by design -- no DB, no OpenStack, no Prometheus. ``vm_utilization.py``
supplies ``VmUtilization`` rows (live or synthetic) and ``finops`` supplies
unit rates; this module only does the arithmetic, so every rule below is
unit-testable with plain dataclasses.

How a VM is judged
------------------
All utilisation figures are percentages *of the VM's own allocation* (CPU %
of its vCPUs, memory % of its RAM), summarised over an observation window as
avg / p95 / max.

* **Classification** (on p95, not avg -- an average hides the bursts that a
  resize would clip):
    - ``saturated``      p95 >= ``saturated_p95`` on CPU or memory. Never
                         shrunk; counted so the report can say "N VMs may
                         need *more*".
    - ``idle``           CPU p95 < ``idle_cpu_p95`` and memory (if known) is
                         under-used too.
    - ``underutilized``  CPU p95 < ``under_cpu_p95`` and/or memory p95 <
                         ``under_mem_p95``. Only the over-provisioned
                         dimension is reduced: a DB with idle CPUs but a hot
                         buffer pool keeps its RAM.
    - ``right_sized``    everything else.
    - ``insufficient_data`` fewer than ``min_observed_hours`` of samples --
                         not enough to judge, so no recommendation.
* **Target size** -- the smallest allocation that would run the *observed*
  load at ``cpu_target`` / ``mem_target`` utilisation at p95 **and** keep the
  observed peak under ``peak_ceiling`` of the new size, snapped up to a real
  flavor (or to a size ladder when no flavor catalogue is available).
* **Risk** (low / medium / high) -- projected peak utilisation on the new
  size, how thin the evidence is, and how big a step it is. See
  ``assess_risk``.
* **Efficiency score** (0-100) -- how much of what the VM reserves (cost-
  weighted CPU vs RAM) its p95 load actually needs at target utilisation.
  100 = nothing to reclaim.
* **Savings** -- the change in the VM's reserved-capacity cost at the FinOps
  unit rates (see ``finops.py``). On a fixed-bill private cloud this is the
  *reclaimable* run-rate: it lowers that project's showback/chargeback and
  frees capacity others could use, but the monthly bill itself only drops
  when the freed capacity is actually removed or re-sold.
"""
from __future__ import annotations

import logging
import math
import os
from dataclasses import dataclass, field
from typing import Iterable

logger = logging.getLogger(__name__)

CLASS_IDLE = "idle"
CLASS_UNDER = "underutilized"
CLASS_RIGHT = "right_sized"
CLASS_SATURATED = "saturated"
CLASS_INSUFFICIENT = "insufficient_data"

RISK_LOW, RISK_MEDIUM, RISK_HIGH = "low", "medium", "high"
_RISK_RANK = {RISK_LOW: 0, RISK_MEDIUM: 1, RISK_HIGH: 2}

# Size ladders used when no flavor catalogue is supplied. Powers of two plus
# the common in-between steps; RAM in MB.
_VCPU_LADDER = (1, 2, 3, 4, 6, 8, 12, 16, 24, 32, 48, 64)
_RAM_LADDER_MB = tuple(int(g * 1024) for g in (0.5, 1, 2, 3, 4, 6, 8, 12, 16, 24, 32, 48, 64, 96, 128))


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    try:
        return float(raw)
    except ValueError:
        logger.warning("%s=%r is not a number, using %s", name, raw, default)
        return default


# ---------------------------------------------------------------- policy --
@dataclass(frozen=True)
class SizingPolicy:
    idle_cpu_p95: float = 5.0
    under_cpu_p95: float = 35.0
    under_mem_p95: float = 50.0
    saturated_p95: float = 85.0
    # Post-resize p95 utilisation we aim for / observed peak we refuse to exceed.
    cpu_target: float = 0.60
    mem_target: float = 0.75
    peak_ceiling: float = 0.85
    # Evidence thresholds (hours of samples inside the window).
    min_observed_hours: float = 24.0
    medium_confidence_hours: float = 48.0
    high_confidence_hours: float = 120.0
    # Ignore recommendations that would reclaim less than this per month.
    min_savings_eur: float = 0.50


def load_policy() -> SizingPolicy:
    d = SizingPolicy()
    return SizingPolicy(
        idle_cpu_p95=_env_float("RIGHTSIZING_IDLE_CPU_P95", d.idle_cpu_p95),
        under_cpu_p95=_env_float("RIGHTSIZING_UNDER_CPU_P95", d.under_cpu_p95),
        under_mem_p95=_env_float("RIGHTSIZING_UNDER_MEM_P95", d.under_mem_p95),
        saturated_p95=_env_float("RIGHTSIZING_SATURATED_P95", d.saturated_p95),
        cpu_target=min(max(_env_float("RIGHTSIZING_CPU_TARGET", d.cpu_target), 0.1), 0.95),
        mem_target=min(max(_env_float("RIGHTSIZING_MEM_TARGET", d.mem_target), 0.1), 0.95),
        peak_ceiling=min(max(_env_float("RIGHTSIZING_PEAK_CEILING", d.peak_ceiling), 0.3), 1.0),
        min_observed_hours=_env_float("RIGHTSIZING_MIN_OBSERVED_HOURS", d.min_observed_hours),
        medium_confidence_hours=_env_float("RIGHTSIZING_MEDIUM_CONFIDENCE_HOURS", d.medium_confidence_hours),
        high_confidence_hours=_env_float("RIGHTSIZING_HIGH_CONFIDENCE_HOURS", d.high_confidence_hours),
        min_savings_eur=max(_env_float("RIGHTSIZING_MIN_SAVINGS_EUR", d.min_savings_eur), 0.0),
    )


# ----------------------------------------------------------------- inputs --
@dataclass
class VmUtilization:
    """One VM's allocation plus its utilisation over the observation window.
    Utilisation is % of the VM's own allocation; ``mem_*`` is None when the
    source cannot see inside the guest (no balloon/agent stats)."""

    vm_id: str
    name: str
    project_id: str | None = None
    project_name: str | None = None
    host: str | None = None
    status: str = "ACTIVE"
    flavor_name: str | None = None
    vcpus: int = 0
    ram_mb: int = 0
    disk_gb: int | None = None  # root disk, when known (Nova cannot shrink it)
    cpu_avg: float | None = None
    cpu_p95: float | None = None
    cpu_max: float | None = None
    mem_avg: float | None = None
    mem_p95: float | None = None
    mem_max: float | None = None
    observed_hours: float = 0.0


@dataclass(frozen=True)
class Flavor:
    name: str
    vcpus: int
    ram_mb: int
    disk_gb: int | None = None
    from_catalog: bool = True


@dataclass(frozen=True)
class UnitRates:
    """EUR / month. None = capacity unknown, so that dimension can't be priced
    honestly (see finops.Rates)."""

    vcpu: float | None
    ram_gb: float | None
    source: str = "unknown"

    @property
    def priced(self) -> bool:
        return self.vcpu is not None and self.ram_gb is not None

    def monthly_cost(self, vcpus: float, ram_mb: float) -> float | None:
        if not self.priced:
            return None
        return vcpus * self.vcpu + (ram_mb / 1024.0) * self.ram_gb  # type: ignore[operator]


# ------------------------------------------------------------ catalogue --
def ladder_flavors() -> list[Flavor]:
    """Every vCPU x RAM combination on the ladders. Used when Nova's real
    flavor list isn't available; the output says so (``from_catalog``)."""
    return [
        Flavor(
            name=f"custom {c} vCPU / {r / 1024:g} GB",
            vcpus=c,
            ram_mb=r,
            from_catalog=False,
        )
        for c in _VCPU_LADDER
        for r in _RAM_LADDER_MB
    ]


def pick_flavor(
    catalog: Iterable[Flavor],
    need_vcpus: int,
    need_ram_mb: int,
    rates: UnitRates,
    min_disk_gb: int | None = None,
) -> Flavor | None:
    """Cheapest flavor with at least the needed vCPU/RAM (and root disk, when
    both sides know it). Ties break towards fewer vCPUs, then less RAM, so the
    result is deterministic. Without rates, 'cheapest' degrades to 'smallest'."""

    def cost(f: Flavor) -> float:
        priced = rates.monthly_cost(f.vcpus, f.ram_mb)
        return priced if priced is not None else f.vcpus * 1000.0 + f.ram_mb / 1024.0

    fits = [
        f
        for f in catalog
        if f.vcpus >= need_vcpus
        and f.ram_mb >= need_ram_mb
        and (min_disk_gb is None or f.disk_gb is None or f.disk_gb >= min_disk_gb)
    ]
    if not fits:
        return None
    return min(fits, key=lambda f: (cost(f), f.vcpus, f.ram_mb, f.name))


# --------------------------------------------------------------- analysis --
def _clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


def classify(vm: VmUtilization, policy: SizingPolicy) -> str:
    if vm.cpu_p95 is None or vm.observed_hours < policy.min_observed_hours or vm.vcpus <= 0:
        return CLASS_INSUFFICIENT
    cpu, mem = vm.cpu_p95, vm.mem_p95
    if cpu >= policy.saturated_p95 or (mem is not None and mem >= policy.saturated_p95):
        return CLASS_SATURATED
    mem_under = mem is not None and mem < policy.under_mem_p95
    cpu_under = cpu < policy.under_cpu_p95
    if cpu < policy.idle_cpu_p95 and (mem is None or mem_under):
        return CLASS_IDLE
    if cpu_under or mem_under:
        return CLASS_UNDER
    return CLASS_RIGHT


def confidence(vm: VmUtilization, policy: SizingPolicy) -> str:
    if vm.observed_hours >= policy.high_confidence_hours and vm.mem_p95 is not None:
        return "high"
    if vm.observed_hours >= policy.medium_confidence_hours:
        return "medium"
    return "low"


def efficiency_score(vm: VmUtilization, rates: UnitRates, policy: SizingPolicy) -> float | None:
    """0-100: share of the reserved allocation that p95 load justifies at the
    target utilisation, cost-weighted across CPU and RAM. None when there is
    no usable utilisation. A VM can't score above 100 however hot it runs."""
    if vm.cpu_p95 is None or vm.vcpus <= 0:
        return None
    cpu_fit = _clamp(vm.cpu_p95 / 100.0 / policy.cpu_target)
    if vm.mem_p95 is None or vm.ram_mb <= 0:
        return round(cpu_fit * 100.0, 1)
    mem_fit = _clamp(vm.mem_p95 / 100.0 / policy.mem_target)
    cpu_cost = vm.vcpus * rates.vcpu if rates.vcpu is not None else None
    ram_cost = (vm.ram_mb / 1024.0) * rates.ram_gb if rates.ram_gb is not None else None
    if cpu_cost is None or ram_cost is None or cpu_cost + ram_cost <= 0:
        w_cpu = 0.5  # no prices: weigh the two dimensions equally
    else:
        w_cpu = cpu_cost / (cpu_cost + ram_cost)
    return round((w_cpu * cpu_fit + (1.0 - w_cpu) * mem_fit) * 100.0, 1)


def _needed_vcpus(vm: VmUtilization, policy: SizingPolicy) -> int:
    used_p95 = (vm.cpu_p95 or 0.0) / 100.0 * vm.vcpus
    used_max = (vm.cpu_max if vm.cpu_max is not None else vm.cpu_p95 or 0.0) / 100.0 * vm.vcpus
    need = max(used_p95 / policy.cpu_target, used_max / policy.peak_ceiling)
    return max(1, math.ceil(need - 1e-9))


def _needed_ram_mb(vm: VmUtilization, policy: SizingPolicy) -> int:
    used_p95 = (vm.mem_p95 or 0.0) / 100.0 * vm.ram_mb
    used_max = (vm.mem_max if vm.mem_max is not None else vm.mem_p95 or 0.0) / 100.0 * vm.ram_mb
    need = max(used_p95 / policy.mem_target, used_max / policy.peak_ceiling)
    return max(256, math.ceil(need - 1e-9))


def assess_risk(
    vm: VmUtilization, new_vcpus: int, new_ram_mb: int, conf: str, policy: SizingPolicy
) -> tuple[str, list[str]]:
    """Points, not a black box. Scored factors: projected peak on the smaller
    size (>70% = 1, >85% = 2), an unknown guest memory profile when RAM is
    cut (1), and a short observation window (1). 0 -> low, 1-2 -> medium,
    >=3 -> high. Every factor -- scored or purely informational -- adds a
    reason the UI shows verbatim."""
    points = 0
    reasons: list[str] = []

    def peak_points(label: str, peak_pct: float | None, old: float, new: float) -> None:
        nonlocal points
        if peak_pct is None or new >= old:
            return
        projected = peak_pct * old / new
        if projected > 85:
            points += 2
            reasons.append(f"{label} peak would reach ~{projected:.0f}% of the smaller size")
        elif projected > 70:
            points += 1
            reasons.append(f"{label} peak would reach ~{projected:.0f}% of the smaller size")

    peak_points("CPU", vm.cpu_max if vm.cpu_max is not None else vm.cpu_p95, vm.vcpus, new_vcpus)
    peak_points("Memory", vm.mem_max if vm.mem_max is not None else vm.mem_p95, vm.ram_mb, new_ram_mb)

    if new_ram_mb < vm.ram_mb:
        if vm.mem_p95 is None:
            points += 1
            reasons.append("guest memory usage is unknown, so the RAM cut is a guess")
        elif new_ram_mb <= vm.ram_mb * 0.5:
            # Informational: the projected-peak check above already prices
            # this in; the note is for the human reading the card.
            reasons.append("RAM is cut by half or more -- a guest that runs out of memory is OOM-killed, not throttled")
    if vm.vcpus >= 4 * max(new_vcpus, 1):
        reasons.append(f"vCPU count drops {vm.vcpus} -> {new_vcpus} in a single step")
    if conf == "low":
        points += 1
        reasons.append("only a short observation window -- weekly/monthly peaks may be missing")
    elif conf == "medium" and vm.observed_hours < policy.high_confidence_hours:
        reasons.append("under five days of history -- check for weekly/monthly jobs before resizing")

    level = RISK_LOW if points == 0 else RISK_MEDIUM if points <= 2 else RISK_HIGH
    if level == RISK_LOW:
        reasons.insert(0, "observed peaks stay comfortably inside the smaller size")
    return level, reasons


def analyze_vm(
    vm: VmUtilization,
    rates: UnitRates,
    catalog: list[Flavor],
    policy: SizingPolicy,
    currency: str = "EUR",
) -> tuple[dict, dict | None]:
    """Returns (row, recommendation|None). `row` is the compact per-VM verdict
    every analysed VM gets; `recommendation` exists only for idle/under-
    utilised VMs where a cheaper size actually fits."""
    cls = classify(vm, policy)
    # No score from too little evidence -- 6 hours of samples can't say how
    # efficient a VM is, and it would skew the fleet average.
    eff = None if cls == CLASS_INSUFFICIENT else efficiency_score(vm, rates, policy)
    conf = confidence(vm, policy)
    row = {
        "vm_id": vm.vm_id,
        "name": vm.name,
        "project_id": vm.project_id,
        "project_name": vm.project_name,
        "classification": cls,
        "efficiency_score": eff,
        "confidence": conf,
        "vcpus": vm.vcpus,
        "ram_mb": vm.ram_mb,
        "cpu_p95": vm.cpu_p95,
        "mem_p95": vm.mem_p95,
    }
    if cls not in (CLASS_IDLE, CLASS_UNDER):
        return row, None

    # Only shrink the dimension that is actually over-provisioned.
    cpu_over = vm.cpu_p95 is not None and vm.cpu_p95 < policy.under_cpu_p95
    mem_over = vm.mem_p95 is not None and vm.mem_p95 < policy.under_mem_p95
    need_vcpus = min(_needed_vcpus(vm, policy), vm.vcpus) if cpu_over else vm.vcpus
    need_ram = min(_needed_ram_mb(vm, policy), vm.ram_mb) if mem_over else vm.ram_mb

    target = pick_flavor(catalog, need_vcpus, need_ram, rates, min_disk_gb=vm.disk_gb)
    if target is None:
        return row, None
    # A catalogue flavor can be bigger than the VM on one axis (e.g. more RAM
    # for fewer vCPUs). Right-sizing never grows a dimension, and a flavor
    # identical to the current allocation is not a recommendation.
    if target.vcpus > vm.vcpus or target.ram_mb > vm.ram_mb:
        return row, None
    if target.vcpus == vm.vcpus and target.ram_mb == vm.ram_mb:
        return row, None

    cur_cost = rates.monthly_cost(vm.vcpus, vm.ram_mb)
    new_cost = rates.monthly_cost(target.vcpus, target.ram_mb)
    savings = round(cur_cost - new_cost, 2) if cur_cost is not None and new_cost is not None else None
    if savings is not None and savings < policy.min_savings_eur:
        return row, None
    savings_pct = round(savings / cur_cost * 100.0, 1) if savings is not None and cur_cost else None

    risk, reasons = assess_risk(vm, target.vcpus, target.ram_mb, conf, policy)

    changes = []
    if target.vcpus < vm.vcpus:
        changes.append(f"vCPU {vm.vcpus} -> {target.vcpus}")
    if target.ram_mb < vm.ram_mb:
        changes.append(f"RAM {vm.ram_mb / 1024:g} -> {target.ram_mb / 1024:g} GB")
    mem_txt = f", memory p95 {vm.mem_p95:.0f}%" if vm.mem_p95 is not None else ""
    rationale = (
        f"CPU p95 {vm.cpu_p95:.0f}% (peak {vm.cpu_max if vm.cpu_max is not None else vm.cpu_p95:.0f}%){mem_txt} "
        f"over {vm.observed_hours / 24:.1f} days. Suggested: {', '.join(changes)}."
    )
    notes = []
    if vm.disk_gb is None:
        notes.append("Root disk size unknown -- the target flavor's disk must be >= the current one (Nova cannot shrink a root disk).")
    if not target.from_catalog:
        notes.append("No Nova flavor catalogue was available -- pick the closest existing flavor that is at least this size.")
    if vm.status != "ACTIVE":
        notes.append(f"VM is {vm.status}; utilisation reflects the period it was running.")

    rec = {
        **row,
        "host": vm.host,
        "status": vm.status,
        "current": {
            "flavor": vm.flavor_name,
            "vcpus": vm.vcpus,
            "ram_mb": vm.ram_mb,
            "monthly_cost_eur": None if cur_cost is None else round(cur_cost, 2),
        },
        "recommended": {
            "flavor": target.name,
            "vcpus": target.vcpus,
            "ram_mb": target.ram_mb,
            "monthly_cost_eur": None if new_cost is None else round(new_cost, 2),
            "from_catalog": target.from_catalog,
        },
        "utilization": {
            "cpu_avg": vm.cpu_avg,
            "cpu_p95": vm.cpu_p95,
            "cpu_max": vm.cpu_max,
            "mem_avg": vm.mem_avg,
            "mem_p95": vm.mem_p95,
            "mem_max": vm.mem_max,
            "observed_hours": round(vm.observed_hours, 1),
        },
        "estimated_monthly_savings_eur": savings,
        "estimated_annual_savings_eur": None if savings is None else round(savings * 12, 2),
        "savings_pct": savings_pct,
        "currency": currency,
        "risk": {"level": risk, "reasons": reasons},
        "rationale": rationale,
        "notes": notes,
        "action": "Recommendation only -- Cortex never resizes a VM on its own. Apply via a change window "
        "(openstack server resize) after the owner confirms.",
    }
    return row, rec


@dataclass
class RightSizingResult:
    recommendations: list[dict] = field(default_factory=list)
    others: list[dict] = field(default_factory=list)
    summary: dict = field(default_factory=dict)


def analyze_fleet(
    vms: list[VmUtilization],
    rates: UnitRates,
    catalog: list[Flavor] | None = None,
    policy: SizingPolicy | None = None,
    currency: str = "EUR",
) -> RightSizingResult:
    policy = policy or SizingPolicy()
    catalog = catalog or ladder_flavors()
    out = RightSizingResult()
    counts = {CLASS_IDLE: 0, CLASS_UNDER: 0, CLASS_RIGHT: 0, CLASS_SATURATED: 0, CLASS_INSUFFICIENT: 0}
    weighted_eff = weight_total = 0.0
    by_project: dict[str, dict] = {}

    for vm in vms:
        row, rec = analyze_vm(vm, rates, catalog, policy, currency)
        counts[row["classification"]] += 1
        cost = rates.monthly_cost(vm.vcpus, vm.ram_mb)
        if row["efficiency_score"] is not None:
            w = cost if cost else 1.0
            weighted_eff += row["efficiency_score"] * w
            weight_total += w
        proj = by_project.setdefault(
            vm.project_id or "unknown",
            {"project_id": vm.project_id, "project_name": vm.project_name or vm.project_id or "unknown",
             "vms": 0, "flagged": 0, "monthly_savings_eur": 0.0},
        )
        proj["vms"] += 1
        if rec is not None:
            proj["flagged"] += 1
            proj["monthly_savings_eur"] += rec["estimated_monthly_savings_eur"] or 0.0
            out.recommendations.append(rec)
        else:
            out.others.append(row)

    out.recommendations.sort(
        key=lambda r: (
            -(r["estimated_monthly_savings_eur"] if r["estimated_monthly_savings_eur"] is not None else -1.0),
            _RISK_RANK[r["risk"]["level"]],
            r["name"] or "",
        )
    )
    out.others.sort(key=lambda r: (r["efficiency_score"] is None, r["efficiency_score"] or 0.0, r["name"] or ""))

    total_savings = sum(r["estimated_monthly_savings_eur"] or 0.0 for r in out.recommendations)
    by_risk = {RISK_LOW: 0, RISK_MEDIUM: 0, RISK_HIGH: 0}
    savings_by_risk = {RISK_LOW: 0.0, RISK_MEDIUM: 0.0, RISK_HIGH: 0.0}
    for r in out.recommendations:
        by_risk[r["risk"]["level"]] += 1
        savings_by_risk[r["risk"]["level"]] += r["estimated_monthly_savings_eur"] or 0.0
    projects = sorted(by_project.values(), key=lambda p: -p["monthly_savings_eur"])
    for p in projects:
        p["monthly_savings_eur"] = round(p["monthly_savings_eur"], 2)

    out.summary = {
        "currency": currency,
        "vms_analyzed": len(vms),
        "flagged": len(out.recommendations),
        "by_classification": counts,
        "by_risk": by_risk,
        "estimated_monthly_savings_eur": round(total_savings, 2) if rates.priced else None,
        "estimated_annual_savings_eur": round(total_savings * 12, 2) if rates.priced else None,
        "low_risk_monthly_savings_eur": round(savings_by_risk[RISK_LOW], 2) if rates.priced else None,
        "fleet_efficiency_score": round(weighted_eff / weight_total, 1) if weight_total else None,
        "priced": rates.priced,
        "rate_source": rates.source,
        "by_project": projects,
    }
    return out
