"""Capacity planning "what-if" simulator (roadmap 4.12): test scenarios --
"+20 VMs", "a new project lands next week", "we add a hypervisor" -- against
the forecast data and see the projected impact on the capacity timeline.

What it does
------------
Takes the per-node forecast trajectories the platform already serves
(forecast_service.get_forecast: cpu/memory on compute nodes, disk on storage
nodes, median + 80% interval out to 90 days) as the **baseline timeline**,
applies a scenario to them, and returns the **scenario timeline** next to it,
plus when each resource first crosses its threshold in each case. A scenario
is a set of independent levers; the point of the feature is that several can
be moved at once and their combined effect read off one chart:

    extra_vms, vm_vcpus / vm_ram_gb / vm_disk_gb   -- what lands
    vm_cpu_util_pct / vm_mem_util_pct / vm_disk_fill_pct -- how hard it works
    start_day, ramp_days                            -- when / how fast it lands
    workload_growth_pct_per_month                   -- organic growth on top
    compute_nodes_added, storage_gb_added, nodes_added_day -- hardware relief
    threshold_pct                                   -- what counts as "full"

The maths (all of it in `simulate`, nothing hidden)
---------------------------------------------------
For one node, at horizon `h` hours, in percent of that node's capacity:

    scenario(h) = (baseline(h) * growth(h) + vm_load * ramp(h)) * capacity_scale(h)

- `growth(h)  = (1 + g/100) ** (h / 720)`  -- compounding monthly growth.
- `vm_load` is the load the new VMs add, **spread evenly** over the nodes
  that carry that resource (Nova's scheduler balances on free RAM/vCPU, so
  even spread is the neutral assumption):
      cpu    = vms * vcpus * cpu_util        / fleet physical vCPUs
      memory = vms * ram   * mem_util        / fleet physical RAM
      disk   = vms * disk  * fill            / Cinder pool size
  i.e. what the VMs actually *use*, not what they merely reserve. The
  reservation side (quota/sellable capacity) is reported separately in
  `reservation`, because "can the scheduler place them" and "will the
  hypervisors stay healthy" are different questions with different answers.
- `ramp(h)` goes 0 -> 1 linearly from `start_day` over `ramp_days` (a new
  project onboarding in waves, not as a step). Never shorter than one hour,
  so an instant step is still a resolvable point on the timeline.
- `capacity_scale(h) = n / (n + k)` once `k` compute nodes (or, for disk,
  `S / (S + added_gb)`) have arrived at `nodes_added_day` -- the same load on
  more hardware. Added nodes are assumed identical to the existing ones.

The timeline is the **busiest node** at each horizon (max over nodes), not
the average: a hypervisor at 95% hurts even if its sibling is at 40%, so the
node that saturates first is the capacity-planning signal. Per-node numbers
are returned alongside.

Honest limits (also returned, in `assumptions`)
-----------------------------------------------
- It is as good as the baseline forecast: where the forecast is the fallback
  tier or past the ML model's trained range, the scenario inherits that.
- Values are *demand as a percentage of capacity* and are not clipped at
  100: 130% means "this load does not fit", which is information, not noise.
- The interval is the baseline's 80% band carried through the same
  transform; the assumed per-VM utilisation is a point estimate with no
  uncertainty of its own.
- Even spread, homogeneous nodes, no live migration or HA headroom.

This module is pure up to `gather_forecasts` / `run_scenario` (the only parts that touch the DB
and the forecast models, imported lazily): `simulate` takes plain dicts, no
randomness, no clock -- the same inputs always give the same output.
"""
from __future__ import annotations

import logging
import math
import threading
import time
from bisect import bisect_left
from dataclasses import dataclass, fields
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

HOURS_PER_MONTH = 720.0
MIN_RAMP_HOURS = 1.0
HISTORY_HOURS = 72
# Demand percentages are shown unclipped (see module docstring) but not
# without limit -- a runaway scenario shouldn't blow up the chart's axis.
DEMAND_CEILING_PCT = 300.0

DEFAULT_THRESHOLD_PCT = 90.0

# metric -> what it measures and which node roles carry it.
RESOURCES: dict[str, dict] = {
    "cpu_percent": {"label": "CPU", "role": "compute"},
    "memory_percent": {"label": "Memory", "role": "compute"},
    "disk_percent": {"label": "Disk", "role": "storage"},
}


# ---------------------------------------------------------------- scenario --
@dataclass(frozen=True)
class Scenario:
    """Every lever the simulator exposes. Defaults describe "no change"
    (apart from the assumptions about a VM, which only matter once
    `extra_vms > 0`), so an all-default Scenario reproduces the baseline."""

    extra_vms: int = 0
    vm_vcpus: int = 2
    vm_ram_gb: float = 4.0
    vm_disk_gb: float = 40.0
    vm_cpu_util_pct: float = 30.0
    vm_mem_util_pct: float = 70.0
    vm_disk_fill_pct: float = 100.0
    start_day: float = 0.0
    ramp_days: float = 0.0
    workload_growth_pct_per_month: float = 0.0
    compute_nodes_added: int = 0
    storage_gb_added: float = 0.0
    nodes_added_day: float = 0.0
    threshold_pct: float = DEFAULT_THRESHOLD_PCT
    horizon_days: int = 30


# The levers that count as "a parameter you changed" -- everything except
# the horizon, which is a view setting rather than part of the scenario.
LEVER_NAMES: tuple[str, ...] = tuple(f.name for f in fields(Scenario) if f.name != "horizon_days")


def changed_parameters(scenario: Scenario) -> list[str]:
    """Names of the levers that differ from their default -- i.e. how many
    things the person actually changed (the UI shows this count)."""
    default = Scenario()
    return [name for name in LEVER_NAMES if getattr(scenario, name) != getattr(default, name)]


# ----------------------------------------------------------------- helpers --
def ramp_fraction(h: float, start_h: float, ramp_h: float) -> float:
    ramp_h = max(ramp_h, MIN_RAMP_HOURS)
    return min(max((h - start_h) / ramp_h, 0.0), 1.0)


def capacity_scale(h: float, arrival_h: float, before: float, after: float) -> float:
    """Linear (over one hour) hand-over from `before` to `after` capacity."""
    frac = min(max((h - arrival_h) / MIN_RAMP_HOURS, 0.0), 1.0)
    return before / (before + (after - before) * frac) if after != before else 1.0


def _interp(xs: list[float], ys: list[float], x: float) -> float:
    """Piecewise-linear interpolation, clamped at both ends."""
    if x <= xs[0]:
        return ys[0]
    if x >= xs[-1]:
        return ys[-1]
    i = bisect_left(xs, x)
    x0, x1, y0, y1 = xs[i - 1], xs[i], ys[i - 1], ys[i]
    return y0 if x1 == x0 else y0 + (y1 - y0) * (x - x0) / (x1 - x0)


def first_crossing(hours: list[float], values: list[float], threshold: float, anchor: datetime | None) -> dict:
    """First-passage time of `values` over `threshold`, linearly interpolated
    between the two bracketing points -- the same definition the 2.5
    threshold warning uses (forecast_service.estimate_threshold_eta), so a
    baseline ETA here agrees with the one on the Forecast page."""
    base = {"will_breach": False, "already_breached": False, "eta_hours": None, "eta_days": None, "crossing_timestamp": None}
    if not values:
        return base
    if values[0] >= threshold:
        return {**base, "will_breach": True, "already_breached": True, "eta_hours": 0.0, "eta_days": 0.0,
                "crossing_timestamp": anchor.isoformat() if anchor else None}
    for i in range(1, len(values)):
        if values[i] >= threshold:
            h0, h1, v0, v1 = hours[i - 1], hours[i], values[i - 1], values[i]
            frac = 0.0 if v1 == v0 else min(max((threshold - v0) / (v1 - v0), 0.0), 1.0)
            eta = h0 + frac * (h1 - h0)
            return {
                **base,
                "will_breach": True,
                "eta_hours": round(eta, 1),
                "eta_days": round(eta / 24.0, 1),
                "crossing_timestamp": (anchor + timedelta(hours=eta)).isoformat() if anchor else None,
            }
    return base


def _parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


# --------------------------------------------------------------- simulation --
def _vm_load_pct(metric: str, s: Scenario, capacity: dict | None) -> tuple[float, str | None]:
    """Percentage points of a node's capacity the full scenario VM set adds
    once ramped in, or (0, reason) when the capacity needed to express it
    as a percentage is unknown."""
    if s.extra_vms <= 0:
        return 0.0, None
    cap = capacity or {}
    if metric == "cpu_percent":
        total = cap.get("physical_vcpus")
        if not total:
            return 0.0, "physical vCPU capacity is unknown, so the added CPU load cannot be expressed as a percentage"
        return s.extra_vms * s.vm_vcpus * (s.vm_cpu_util_pct / 100.0) / total * 100.0, None
    if metric == "memory_percent":
        total = cap.get("physical_ram_mb")
        if not total:
            return 0.0, "physical RAM capacity is unknown, so the added memory load cannot be expressed as a percentage"
        return s.extra_vms * s.vm_ram_gb * 1024.0 * (s.vm_mem_util_pct / 100.0) / total * 100.0, None
    total = cap.get("storage_gb")
    if not total:
        return 0.0, "storage pool size is unknown, so the added disk load cannot be expressed as a percentage"
    return s.extra_vms * s.vm_disk_gb * (s.vm_disk_fill_pct / 100.0) / total * 100.0, None


def _capacity_before_after(metric: str, s: Scenario, capacity: dict | None, node_count: int) -> tuple[float, float, str | None]:
    """(before, after) capacity in any consistent unit -- only their ratio
    is used -- for the hardware-added lever of this metric."""
    cap = capacity or {}
    if metric in ("cpu_percent", "memory_percent"):
        n = cap.get("compute_nodes") or node_count
        return float(n), float(n + s.compute_nodes_added), None
    gb = cap.get("storage_gb")
    if s.storage_gb_added > 0 and not gb:
        return 1.0, 1.0, "storage pool size is unknown, so the added storage cannot be applied"
    return float(gb or 1.0), float((gb or 1.0) + s.storage_gb_added), None


def _align_nodes(nodes: list[dict]) -> tuple[list[float], list[dict]]:
    """Common horizon grid across nodes (their forecasts are served from the
    same horizon list, so this is normally just that list) and, per node,
    its median/lower/upper on it."""
    common: set[float] | None = None
    for n in nodes:
        hs = {float(p["horizon_hours"]) for p in n["forecast"]}
        common = hs if common is None else common & hs
    hours = sorted(common or [])
    aligned = []
    for n in nodes:
        by_h = {float(p["horizon_hours"]): p for p in n["forecast"]}
        now = float(n["actual"][-1]["value"]) if n.get("actual") else float(by_h[hours[0]]["predicted"])
        aligned.append({
            "hostname": n["hostname"],
            "now": now,
            "predicted": [float(by_h[h]["predicted"]) for h in hours],
            "lower": [float(by_h[h]["lower"]) for h in hours],
            "upper": [float(by_h[h]["upper"]) for h in hours],
            "extrapolated": [bool(by_h[h].get("extrapolated", False)) for h in hours],
        })
    return hours, aligned


def _verdict(base: dict, scen: dict) -> str:
    if base["already_breached"]:
        return "already_breached"
    if not base["will_breach"] and not scen["will_breach"]:
        return "ok"
    if not base["will_breach"] and scen["will_breach"]:
        return "new_breach"
    if base["will_breach"] and not scen["will_breach"]:
        return "relieved"
    shift = base["eta_hours"] - scen["eta_hours"]
    if shift > 6.0:
        return "earlier_breach"
    if shift < -6.0:
        return "later_breach"
    return "unchanged"


def _simulate_resource(metric: str, nodes: list[dict], s: Scenario, capacity: dict | None, horizon_hours: float) -> dict:
    meta = RESOURCES[metric]
    out: dict = {"metric": metric, "label": meta["label"], "node_role": meta["role"]}
    if not nodes:
        return {**out, "available": False, "reason": f"no {meta['role']} node has forecast data for this metric"}

    hours, aligned = _align_nodes(nodes)
    if not hours:
        return {**out, "available": False, "reason": "node forecasts do not share a common horizon"}

    notes: list[str] = []
    vm_load, why = _vm_load_pct(metric, s, capacity)
    if why:
        notes.append(why)
    cap_before, cap_after, why = _capacity_before_after(metric, s, capacity, len(nodes))
    if why:
        notes.append(why)

    start_h, ramp_h, arrive_h = s.start_day * 24.0, max(s.ramp_days * 24.0, MIN_RAMP_HOURS), s.nodes_added_day * 24.0

    # Grid = every served horizon + h=0 ("now") + the scenario's own corner
    # points, so a step or ramp edge lands on the chart where it happens
    # rather than being smeared across a coarse 30/90-day checkpoint gap.
    corners = [0.0, start_h, start_h + ramp_h, arrive_h, arrive_h + MIN_RAMP_HOURS]
    grid = sorted({h for h in [*hours, *corners] if 0.0 <= h <= horizon_hours})

    def transform(h: float, value: float) -> float:
        grown = value * (1.0 + s.workload_growth_pct_per_month / 100.0) ** (h / HOURS_PER_MONTH)
        loaded = grown + vm_load * ramp_fraction(h, start_h, ramp_h)
        scaled = loaded * capacity_scale(h, arrive_h, cap_before, cap_after)
        return min(max(scaled, 0.0), DEMAND_CEILING_PCT)

    per_node = []
    series: dict[str, list[list[float]]] = {k: [] for k in ("b", "bl", "bu", "s", "sl", "su")}
    for a in aligned:
        xs = [0.0, *hours]
        med, lo, up = [a["now"], *a["predicted"]], [a["now"], *a["lower"]], [a["now"], *a["upper"]]
        b_med = [_interp(xs, med, h) for h in grid]
        b_lo = [_interp(xs, lo, h) for h in grid]
        b_up = [_interp(xs, up, h) for h in grid]
        s_med = [transform(h, v) for h, v in zip(grid, b_med)]
        s_lo = [transform(h, v) for h, v in zip(grid, b_lo)]
        s_up = [transform(h, v) for h, v in zip(grid, b_up)]
        for key, vals in zip(("b", "bl", "bu", "s", "sl", "su"), (b_med, b_lo, b_up, s_med, s_lo, s_up)):
            series[key].append(vals)
        per_node.append({"hostname": a["hostname"], "b": b_med, "s": s_med, "now": a["now"]})

    def peak(key: str) -> list[float]:
        return [max(col) for col in zip(*series[key])]

    b, bl, bu = peak("b"), peak("bl"), peak("bu")
    sc, sl, su = peak("s"), peak("sl"), peak("su")

    anchors = [t for t in (_parse_ts(n.get("generated_at")) for n in nodes) if t is not None]
    anchor = max(anchors) if anchors else None

    def ts(h: float) -> str | None:
        return (anchor + timedelta(hours=h)).isoformat() if anchor else None

    # Which grid points are past the ML model's trained range -- from the
    # baseline the scenario was built on, any node counts.
    extrap_hours = [h for h in hours if any(a["extrapolated"][hours.index(h)] for a in aligned)]
    first_extrap = min(extrap_hours) if extrap_hours else None

    threshold = s.threshold_pct
    base_breach = first_crossing(grid, b, threshold, anchor)
    scen_breach = first_crossing(grid, sc, threshold, anchor)

    nodes_out = []
    for p in per_node:
        nb, ns = first_crossing(grid, p["b"], threshold, anchor), first_crossing(grid, p["s"], threshold, anchor)
        nodes_out.append({
            "hostname": p["hostname"],
            "current": round(p["now"], 1),
            "baseline_end": round(p["b"][-1], 1),
            "scenario_end": round(p["s"][-1], 1),
            "baseline_peak": round(max(p["b"]), 1),
            "scenario_peak": round(max(p["s"]), 1),
            "baseline_eta_days": nb["eta_days"],
            "scenario_eta_days": ns["eta_days"],
            "verdict": _verdict(nb, ns),
        })
    nodes_out.sort(key=lambda n: -n["scenario_peak"])

    verdict = _verdict(base_breach, scen_breach)
    eta_shift = None
    if base_breach["will_breach"] and scen_breach["will_breach"]:
        eta_shift = round(base_breach["eta_days"] - scen_breach["eta_days"], 1)  # >0 = sooner

    history: dict[str, float] = {}
    for n in nodes:
        for pt in (n.get("actual") or [])[-HISTORY_HOURS:]:
            history[pt["timestamp"]] = max(history.get(pt["timestamp"], 0.0), float(pt["value"]))

    return {
        **out,
        "available": True,
        "node_count": len(nodes),
        "threshold": threshold,
        "vm_load_pct_points": round(vm_load, 2),
        "capacity_scale_after": round(cap_before / cap_after, 3) if cap_after else 1.0,
        "notes": notes,
        "first_extrapolated_hour": first_extrap,
        "history": [{"timestamp": t, "value": round(v, 2)} for t, v in sorted(history.items())][-HISTORY_HOURS:],
        "timeline": [
            {
                "horizon_hours": h,
                "timestamp": ts(h),
                "baseline": round(b[i], 2), "baseline_lower": round(bl[i], 2), "baseline_upper": round(bu[i], 2),
                "scenario": round(sc[i], 2), "scenario_lower": round(sl[i], 2), "scenario_upper": round(su[i], 2),
            }
            for i, h in enumerate(grid)
        ],
        "baseline_breach": base_breach,
        "scenario_breach": scen_breach,
        "eta_shift_days": eta_shift,
        "verdict": verdict,
        "end_baseline": round(b[-1], 1),
        "end_scenario": round(sc[-1], 1),
        "peak_baseline": round(max(b), 1),
        "peak_scenario": round(max(sc), 1),
        "exceeds_capacity": max(sc) > 100.0,
        "nodes": nodes_out,
    }


def _reservation(s: Scenario, capacity: dict | None) -> dict:
    """The placement side of the question: does the scheduler have room, in
    *reserved* (flavor-size) terms, for these VMs? Mirrors the FinOps
    capacity rows (sellable = physical x allocation ratio) so the numbers
    agree with the Quotas & Budget overview."""
    if not capacity:
        return {"available": False, "reason": "no FinOps cloud sample yet -- capacity is unknown"}

    nodes_now = capacity.get("compute_nodes") or 0
    k = s.compute_nodes_added
    per_node_v = (capacity["physical_vcpus"] / nodes_now) if capacity.get("physical_vcpus") and nodes_now else 0.0
    per_node_r = (capacity["physical_ram_mb"] / nodes_now) if capacity.get("physical_ram_mb") and nodes_now else 0.0

    def sellable(phys, ratio, extra):
        return (phys * ratio + extra) if phys else None

    need = {
        "vcpus": s.extra_vms * s.vm_vcpus,
        "ram_mb": s.extra_vms * s.vm_ram_gb * 1024.0,
        "gigabytes": s.extra_vms * s.vm_disk_gb,
    }
    spec = {
        "vcpus": (sellable(capacity.get("physical_vcpus"), capacity["cpu_allocation_ratio"], k * per_node_v * capacity["cpu_allocation_ratio"]),
                  sellable(capacity.get("physical_vcpus"), capacity["cpu_allocation_ratio"], 0.0), capacity["allocated_vcpus"]),
        "ram_mb": (sellable(capacity.get("physical_ram_mb"), capacity["ram_allocation_ratio"], k * per_node_r * capacity["ram_allocation_ratio"]),
                   sellable(capacity.get("physical_ram_mb"), capacity["ram_allocation_ratio"], 0.0), capacity["allocated_ram_mb"]),
        "gigabytes": (sellable(capacity.get("storage_gb"), 1.0, s.storage_gb_added),
                      sellable(capacity.get("storage_gb"), 1.0, 0.0), capacity["allocated_gb"]),
    }

    rows, fits_now, fits_after = {}, [], []
    per_vm = {"vcpus": s.vm_vcpus, "ram_mb": s.vm_ram_gb * 1024.0, "gigabytes": s.vm_disk_gb}
    for key, (sell_after_cap, sell_before, allocated) in spec.items():
        if sell_before is None:
            rows[key] = {"available": False}
            continue
        sell_after = sell_after_cap
        after = allocated + need[key]
        rows[key] = {
            "available": True,
            "sellable_before": round(sell_before, 1),
            "sellable_after": round(sell_after, 1),
            "allocated_before": round(allocated, 1),
            "allocated_after": round(after, 1),
            "pct_before": round(allocated / sell_before * 100.0, 1) if sell_before else None,
            "pct_after": round(after / sell_after * 100.0, 1) if sell_after else None,
            "oversold_after": after > sell_after + 1e-9,
        }
        if per_vm[key] > 0:
            fits_now.append(max(math.floor((sell_before - allocated) / per_vm[key]), 0))
            fits_after.append(max(math.floor((sell_after - allocated) / per_vm[key]), 0))

    rates = capacity.get("rates") or {}
    showback = None
    if s.extra_vms and None not in (rates.get("vcpu"), rates.get("ram_gb"), rates.get("storage_gb")):
        showback = round(
            need["vcpus"] * rates["vcpu"] + need["ram_mb"] / 1024.0 * rates["ram_gb"] + need["gigabytes"] * rates["storage_gb"], 2
        )

    return {
        "available": True,
        "rows": rows,
        "max_additional_vms_before": min(fits_now) if fits_now else None,
        "max_additional_vms_after": min(fits_after) if fits_after else None,
        "oversold_after": any(r.get("oversold_after") for r in rows.values()),
        "showback_eur_month": showback,
    }


_SEVERITY = {"already_breached": 5, "new_breach": 4, "earlier_breach": 3, "unchanged": 2, "later_breach": 1, "relieved": 0, "ok": 0}


def _eta_phrase(breach: dict) -> str:
    """Same wording conventions as the Forecast page's threshold warning
    (lib/thresholds.ts::thresholdEtaLabel): never "in ~0 days"."""
    if breach["already_breached"]:
        return "already"
    days = breach["eta_days"] or 0.0
    if days < 1:
        return "in under a day"
    rounded = round(days)
    return "in ~1 day" if rounded == 1 else f"in ~{rounded} days"


def _headline(resources: list[dict], reservation: dict, changed: list[str]) -> str:
    if not changed:
        return "No scenario parameter changed -- this is the baseline forecast."
    live = [r for r in resources if r.get("available")]
    if not live:
        return "No forecast data is available for any resource, so the scenario cannot be evaluated."
    breaches = [r for r in live if r["scenario_breach"]["will_breach"]]
    if breaches:
        first = min(breaches, key=lambda r: r["scenario_breach"]["eta_hours"])
        when = _eta_phrase(first["scenario_breach"])
        text = f"{first['label']} is projected to cross {first['threshold']:g}% {when} under this scenario"
        base = first["baseline_breach"]
        if base["will_breach"] and not base["already_breached"] and first["eta_shift_days"] and first["eta_shift_days"] > 0:
            text += f" ({first['eta_shift_days']:g} days sooner than the baseline)"
        elif not base["will_breach"]:
            text += " (the baseline stays below it)"
        return text + "."
    worst = max(live, key=lambda r: r["peak_scenario"])
    return f"No resource crosses its threshold; {worst['label']} peaks at {worst['peak_scenario']:g}% of capacity."


def simulate(scenario: Scenario, node_forecasts: dict[str, list[dict]], capacity: dict | None) -> dict:
    """Run one scenario.

    `node_forecasts` -- {metric: [{hostname, generated_at, forecast[], actual[]}, ...]}
    shaped like forecast_service.get_forecast output, one entry per node
    that carries that metric. `capacity` -- the cloud's physical/sellable/
    allocated capacity (see `capacity_from_sample`), or None when unknown.
    """
    horizon_hours = float(scenario.horizon_days) * 24.0
    resources = [
        _simulate_resource(metric, node_forecasts.get(metric) or [], scenario, capacity, horizon_hours)
        for metric in RESOURCES
    ]
    reservation = _reservation(scenario, capacity)
    changed = changed_parameters(scenario)
    live = [r for r in resources if r.get("available")]
    worst = max(live, key=lambda r: _SEVERITY[r["verdict"]]) if live else None
    first_breach = None
    breaching = [r for r in live if r["scenario_breach"]["will_breach"]]
    if breaching:
        r = min(breaching, key=lambda r: r["scenario_breach"]["eta_hours"])
        first_breach = {"metric": r["metric"], "label": r["label"], **r["scenario_breach"]}

    assumptions = [
        "New VM load is spread evenly across the nodes that carry the resource.",
        "Added compute nodes / storage have the same specification as the existing ones.",
        "Per-VM utilisation is a point estimate; the interval only reflects forecast uncertainty.",
        "Values are demand as a percentage of capacity and are not clipped at 100%.",
    ]
    if any(r.get("first_extrapolated_hour") is not None for r in live):
        assumptions.append("Part of the horizon lies beyond the ML model's trained range; it is extrapolated and less certain.")

    return {
        "scenario": {name: getattr(scenario, name) for name in (*LEVER_NAMES, "horizon_days")},
        "changed_parameters": changed,
        "changed_parameter_count": len(changed),
        "horizon_days": scenario.horizon_days,
        "summary": {
            "headline": _headline(resources, reservation, changed),
            "worst_verdict": worst["verdict"] if worst else None,
            "worst_resource": worst["metric"] if worst else None,
            "first_breach": first_breach,
        },
        "resources": resources,
        "reservation": reservation,
        "assumptions": assumptions,
    }


# ------------------------------------------------------ input gathering -----
def capacity_from_sample(sample) -> dict | None:
    """Flatten the latest models.FinopsCloudSample (hourly cloud snapshot:
    physical vs sellable vs reserved capacity + derived unit rates) into the
    plain dict `simulate` takes."""
    if sample is None:
        return None
    return {
        "physical_vcpus": sample.physical_vcpus,
        "physical_ram_mb": sample.physical_ram_mb,
        "storage_gb": sample.storage_gb,
        "compute_nodes": sample.compute_nodes,
        "cpu_allocation_ratio": sample.cpu_allocation_ratio or 1.0,
        "ram_allocation_ratio": sample.ram_allocation_ratio or 1.0,
        "allocated_vcpus": sample.allocated_vcpus or 0.0,
        "allocated_ram_mb": sample.allocated_ram_mb or 0.0,
        "allocated_gb": sample.allocated_gb or 0.0,
        "rates": {
            "vcpu": sample.rate_vcpu_eur,
            "ram_gb": sample.rate_ram_gb_eur,
            "storage_gb": sample.rate_storage_gb_eur,
        },
        "sampled_at": sample.sampled_at.isoformat() if sample.sampled_at else None,
    }


# Forecasts are the expensive part (dataset slice + model predict per node
# and metric) and the same for every scenario at a given horizon, so a
# slider drag re-simulates against a short-lived copy instead of
# re-forecasting on each tick. The forecast models only retrain hourly, so
# a minute of staleness is invisible.
_CACHE_TTL_SECONDS = 60.0
_cache_lock = threading.Lock()
_forecast_cache: dict[int, tuple[float, dict]] = {}


def clear_cache() -> None:
    with _cache_lock:
        _forecast_cache.clear()


def gather_forecasts(db, horizon_days: int) -> dict[str, list[dict]]:
    """{metric: [per-node forecast dicts]} from the same get_forecast the
    Forecast page uses. Nodes without a forecast (too little data, no model)
    are simply absent for that metric."""
    now = time.monotonic()
    with _cache_lock:
        hit = _forecast_cache.get(horizon_days)
        if hit and now - hit[0] < _CACHE_TTL_SECONDS:
            return hit[1]

    from .. import crud  # lazy: keeps this module's pure part import-light
    from .forecast_service import get_forecast

    nodes = crud.list_nodes(db)
    result: dict[str, list[dict]] = {metric: [] for metric in RESOURCES}
    for metric, meta in RESOURCES.items():
        for node in nodes:
            if node.role != meta["role"] or not getattr(node, "is_active", True):
                continue
            fc = get_forecast(node.ip_address, metric, horizon_days=horizon_days)
            if fc is None:
                continue
            result[metric].append({
                "hostname": node.hostname,
                "generated_at": fc["generated_at"],
                "model_type": fc["model_type"],
                "forecast": fc["forecast"],
                "actual": fc["actual"],
            })
    with _cache_lock:
        _forecast_cache[horizon_days] = (now, result)
    return result


def run_scenario(db, scenario: Scenario) -> dict:
    """DB-backed entry point: gather the baseline, simulate, annotate."""
    from .. import models  # lazy, see gather_forecasts

    sample = (
        db.query(models.FinopsCloudSample).order_by(models.FinopsCloudSample.sampled_at.desc()).first()
    )
    capacity = capacity_from_sample(sample)
    forecasts = gather_forecasts(db, scenario.horizon_days)
    result = simulate(scenario, forecasts, capacity)
    result["capacity_sampled_at"] = capacity["sampled_at"] if capacity else None
    result["models"] = sorted({n["model_type"] for nodes in forecasts.values() for n in nodes})
    return result
