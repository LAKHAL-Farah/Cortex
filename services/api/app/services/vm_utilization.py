"""Where right-sizing gets its per-VM numbers (roadmap 4.10).

Two sources behind one shape (``list[VmUtilization]``):

* ``synthetic_fleet()`` -- a fixed, deterministic fleet that deliberately
  contains every case the analysis distinguishes (idle, under-utilised on
  CPU only, bursty, right-sized, saturated, thin history, no history). It is
  what the acceptance test uses and what ``?source=synthetic`` serves, so the
  feature can be demoed and tested without a live cloud. Output built from it
  is always labelled ``source: synthetic`` -- it is never mixed into live data.

* ``live_fleet()`` -- the VM inventory from the topology graph (flavor
  vCPU/RAM, project, host -- already synced by topology_sync, so no extra
  OpenStack polling) joined with per-VM utilisation from Prometheus.

  **Prerequisite:** per-VM utilisation needs a libvirt exporter scraped by
  Prometheus on the compute nodes (e.g. prometheus-libvirt-exporter).
  node_exporter only sees whole hosts, and this repo's infra does not deploy a
  libvirt exporter yet. Without one, ``live_fleet`` raises
  ``NoUtilizationData`` and the API reports "no data" -- it never invents
  numbers and never silently falls back to the synthetic fleet.

  The default PromQL matches prometheus-libvirt-exporter's metric names but
  is fully overridable (``RIGHTSIZING_CPU_EXPR`` / ``RIGHTSIZING_MEM_EXPR`` /
  ``RIGHTSIZING_VM_ID_LABEL``) because exporters differ in naming and in
  which label carries the Nova instance UUID. Each expression must yield a
  percentage of the VM's own allocation, one series per VM.
"""
from __future__ import annotations

import logging
import os
import time
from typing import Any

from .rightsizing import Flavor, UnitRates, VmUtilization

logger = logging.getLogger(__name__)


class NoUtilizationData(RuntimeError):
    """The live source has an inventory but no per-VM utilisation to join."""


# ------------------------------------------------------------ synthetic --
def synthetic_fleet() -> list[VmUtilization]:
    """Deterministic demo/test fleet. Allocation + utilisation are chosen so
    each row exercises one branch of rightsizing.analyze_vm."""

    def vm(vid, name, proj, flavor, vcpus, ram_gb, cpu, mem, hours, host="compute1", status="ACTIVE"):
        return VmUtilization(
            vm_id=vid,
            name=name,
            project_id=f"proj-{proj}",
            project_name=proj,
            host=host,
            status=status,
            flavor_name=flavor,
            vcpus=vcpus,
            ram_mb=int(ram_gb * 1024),
            cpu_avg=cpu[0], cpu_p95=cpu[1], cpu_max=cpu[2],
            mem_avg=mem[0] if mem else None, mem_p95=mem[1] if mem else None, mem_max=mem[2] if mem else None,
            observed_hours=hours,
        )

    return [
        # Clearly over-provisioned batch box -> big, low-risk saving.
        vm("syn-0001", "batch-worker-01", "analytics", "m1.xlarge", 8, 16, (4, 9, 18), (22, 30, 38), 168),
        # Practically idle legacy VM.
        vm("syn-0002", "legacy-reports-vm", "finance", "m1.large", 4, 8, (1.2, 2.5, 6), (10, 14, 16), 168, host="compute2"),
        # Healthy.
        vm("syn-0003", "web-frontend-02", "webshop", "m1.large", 4, 8, (38, 55, 72), (55, 62, 70), 168),
        # CPU idle but RAM hot: only the vCPUs may shrink.
        vm("syn-0004", "db-primary", "webshop", "m1.2xlarge", 8, 32, (20, 28, 45), (78, 84, 88), 168, host="compute2"),
        # Low p95 but a nasty burst: the peak guard limits how far it can go.
        vm("syn-0005", "ci-runner-03", "platform", "m1.xxlarge", 16, 32, (6, 12, 85), (20, 35, 60), 168),
        # Running hot: never shrunk, counted as 'may need more'.
        vm("syn-0006", "api-gateway-01", "webshop", "m1.small", 2, 4, (82, 93, 99), (70, 88, 92), 168),
        # Under-utilised but only 30 h of history -> low confidence, higher risk.
        vm("syn-0007", "dev-sandbox-04", "platform", "m1.large", 4, 16, (3, 7, 15), (15, 20, 28), 30),
        # Too new to judge.
        vm("syn-0008", "new-vm-05", "analytics", "m1.medium", 2, 4, (1, 2, 3), (10, 12, 14), 6),
        # No guest memory stats (no agent): CPU-only judgement.
        vm("syn-0009", "etl-scheduler", "analytics", "m1.xlarge", 8, 16, (7, 14, 30), None, 168, host="compute2"),
    ]


def synthetic_rates() -> UnitRates:
    """Unit rates of the reference cloud used throughout the FinOps tests
    (2 x 8 vCPU / 16 GB hypervisors, 1 000 GB, EUR 292/month, default pools):
    vCPU 1.36875, RAM 1.825 EUR per GB-month."""
    return UnitRates(vcpu=1.36875, ram_gb=1.825, source="reference-cloud")


# ----------------------------------------------------------------- live --
_CPU_EXPR = os.environ.get(
    "RIGHTSIZING_CPU_EXPR",
    "100 * rate(libvirt_domain_info_cpu_time_seconds_total[5m]) / libvirt_domain_info_virtual_cpus",
)
_MEM_EXPR = os.environ.get("RIGHTSIZING_MEM_EXPR", "libvirt_domain_memory_stats_used_percent")
_ID_LABEL = os.environ.get("RIGHTSIZING_VM_ID_LABEL", "instance_id")
_QUERY_TIMEOUT = float(os.environ.get("RIGHTSIZING_QUERY_TIMEOUT_SECONDS", "30"))


def _series_by_vm(result: list[dict]) -> dict[str, float]:
    out: dict[str, float] = {}
    for series in result or []:
        labels = series.get("metric", {})
        key = labels.get(_ID_LABEL) or labels.get("domain")
        if not key:
            continue
        try:
            value = float(series["value"][1])
        except (KeyError, IndexError, TypeError, ValueError):
            continue
        if value != value:  # NaN
            continue
        out[str(key)] = value
    return out


def _agg(expr: str, window_days: int) -> dict[str, dict[str, dict[str, float]]]:
    """avg / p95 / max (+ hours of data) of one per-VM percentage expression
    over the window, as instant subquery aggregations -- 4 Prometheus calls
    instead of pulling a week of raw points per VM."""
    from . import prometheus_client

    win = f"{int(window_days)}d"
    sub = f"({expr})[{win}:5m]"
    queries = {
        "avg": f"avg_over_time({sub})",
        "p95": f"quantile_over_time(0.95, {sub})",
        "max": f"max_over_time({sub})",
        "hours": f"count_over_time(({expr})[{win}:1h])",
    }
    return {name: _series_by_vm(prometheus_client.query(q, timeout=_QUERY_TIMEOUT)) for name, q in queries.items()}  # type: ignore[return-value]


def live_fleet(
    window_days: int,
    inventory: list[dict] | None = None,
    project_names: dict[str, str] | None = None,
) -> list[VmUtilization]:
    """Inventory (graph) x utilisation (Prometheus). VMs with an inventory
    row but no metric series are *omitted* -- judging them on nothing would be
    a guess. Raises NoUtilizationData when nothing joined at all."""
    if inventory is None:
        from .. import graph_db

        inventory = graph_db.fetch_instance_inventory()
    project_names = project_names or {}

    cpu = _agg(_CPU_EXPR, window_days)
    mem = _agg(_MEM_EXPR, window_days) if _MEM_EXPR.strip().lower() not in ("", "none") else None

    fleet: list[VmUtilization] = []
    for inst in inventory:
        vid = inst["id"]
        # Some exporters key by libvirt domain name ("instance-0000002a")
        # rather than the Nova UUID; match on either.
        key = vid if vid in cpu["p95"] else inst.get("name") if inst.get("name") in cpu["p95"] else None
        if key is None:
            continue
        mkey = key if mem and key in mem["p95"] else None
        vcpus, ram_mb = inst.get("flavor_vcpus"), inst.get("flavor_ram_mb")
        if not vcpus or not ram_mb:
            continue
        fleet.append(
            VmUtilization(
                vm_id=vid,
                name=inst.get("name") or vid,
                project_id=inst.get("project_id"),
                project_name=project_names.get(inst.get("project_id") or "", None),
                host=inst.get("host"),
                status=inst.get("status") or "ACTIVE",
                flavor_name=inst.get("flavor_name"),
                vcpus=int(vcpus),
                ram_mb=int(ram_mb),
                cpu_avg=cpu["avg"].get(key), cpu_p95=cpu["p95"].get(key), cpu_max=cpu["max"].get(key),
                mem_avg=mem["avg"].get(mkey) if mkey else None,
                mem_p95=mem["p95"].get(mkey) if mkey else None,
                mem_max=mem["max"].get(mkey) if mkey else None,
                observed_hours=cpu["hours"].get(key, 0.0),
            )
        )
    if not fleet:
        raise NoUtilizationData(
            "No per-VM utilisation found in Prometheus. Right-sizing needs a libvirt exporter scraped on the "
            "compute nodes (node_exporter only sees whole hosts)."
        )
    return fleet


# --------------------------------------------------------------- flavors --
_FLAVOR_TTL_SECONDS = 600
_flavor_cache: dict[str, Any] = {"at": 0.0, "value": None}


def nova_flavors() -> list[Flavor] | None:
    """The cloud's real flavors, so a recommendation names one that exists.
    Best-effort and cached: any failure returns None and the caller falls
    back to the size ladder (and says so in the output)."""
    if os.environ.get("RIGHTSIZING_USE_NOVA_FLAVORS", "true").lower() in ("0", "false", "no"):
        return None
    now = time.monotonic()
    if now - _flavor_cache["at"] < _FLAVOR_TTL_SECONDS:
        return _flavor_cache["value"]
    value: list[Flavor] | None = None
    try:
        import openstack

        conn = openstack.connect(cloud=os.environ.get("OS_CLOUD", "cortex-reader"))
        flavors = [
            Flavor(
                name=f.name,
                vcpus=int(f.vcpus),
                ram_mb=int(f.ram),
                disk_gb=int(f.disk) if getattr(f, "disk", None) is not None else None,
            )
            for f in conn.compute.flavors()
            if not getattr(f, "is_disabled", False)
        ]
        value = flavors or None
    except Exception:
        logger.warning("right-sizing: Nova flavor list unavailable, using the size ladder", exc_info=True)
    _flavor_cache.update(at=now, value=value)
    return value
