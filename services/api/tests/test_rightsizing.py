"""Roadmap 4.10 -- right-sizing recommendations.

Acceptance line this file proves: ">=1 synthetic under-utilised VM produces a
suggestion with an estimated savings figure and a risk label".

Hand-computed against the reference cloud rates (vCPU 1.36875, RAM 1.825
EUR/GB-month) and the default policy (CPU target 60 %, memory target 75 %,
peak ceiling 85 %), e.g. for `batch-worker-01` (8 vCPU / 16 GB, CPU p95 9 %
peak 18 %, memory p95 30 % peak 38 %):

    vCPUs needed = max(8*0.09/0.60, 8*0.18/0.85) = max(1.2, 1.69) -> 2
    RAM needed   = max(16*0.30/0.75, 16*0.38/0.85) = 7.15 GB       -> 8 GB step
    current  8*1.36875 + 16*1.825 = 40.15 /month
    smaller  2*1.36875 +  8*1.825 = 17.3375
    savings  22.8125 -> 22.81
"""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import models
from app.db import get_db
from app.routers import rightsizing as rightsizing_router
from app.services import rightsizing as rs
from app.services import rightsizing_service, vm_utilization


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    for name in list(__import__("os").environ):
        if name.startswith("RIGHTSIZING_"):
            monkeypatch.delenv(name, raising=False)
    rightsizing_service.clear_cache()


def _db():
    engine = create_engine("sqlite:///:memory:", poolclass=StaticPool, connect_args={"check_same_thread": False})
    models.Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def _fleet_result():
    return rs.analyze_fleet(vm_utilization.synthetic_fleet(), vm_utilization.synthetic_rates())


def _rec(result, name):
    return next(r for r in result.recommendations if r["name"] == name)


# ===========================================================================
# Acceptance
# ===========================================================================

def test_synthetic_underutilized_vm_gets_savings_estimate_and_risk_label():
    rec = _rec(_fleet_result(), "batch-worker-01")
    assert rec["classification"] == "underutilized"
    assert rec["current"]["vcpus"] == 8 and rec["current"]["ram_mb"] == 16384
    assert rec["recommended"]["vcpus"] == 2 and rec["recommended"]["ram_mb"] == 8192
    assert rec["current"]["monthly_cost_eur"] == pytest.approx(40.15)
    assert rec["recommended"]["monthly_cost_eur"] == pytest.approx(17.34, abs=0.01)
    assert rec["estimated_monthly_savings_eur"] == pytest.approx(22.81, abs=0.01)
    assert rec["estimated_annual_savings_eur"] == pytest.approx(22.81 * 12, abs=0.2)
    assert rec["risk"]["level"] in {"low", "medium", "high"}
    assert rec["risk"]["reasons"]
    assert 0 <= rec["efficiency_score"] <= 100


# ===========================================================================
# Classification + the other branches of the fleet
# ===========================================================================

def test_every_branch_of_the_fleet_is_classified_as_designed():
    result = _fleet_result()
    verdict = {r["name"]: r["classification"] for r in result.recommendations + result.others}
    assert verdict == {
        "batch-worker-01": "underutilized",
        "legacy-reports-vm": "idle",
        "web-frontend-02": "right_sized",
        "db-primary": "underutilized",
        "ci-runner-03": "underutilized",
        "api-gateway-01": "saturated",
        "dev-sandbox-04": "underutilized",
        "new-vm-05": "insufficient_data",
        "etl-scheduler": "underutilized",
    }
    # Healthy, saturated and too-new VMs never get a recommendation.
    assert {r["name"] for r in result.others} == {"web-frontend-02", "api-gateway-01", "new-vm-05"}
    # ...and a VM with 6 h of data gets no efficiency score at all.
    assert next(r for r in result.others if r["name"] == "new-vm-05")["efficiency_score"] is None


def test_summary_totals_match_the_individual_recommendations():
    result = _fleet_result()
    s = result.summary
    assert s["vms_analyzed"] == 9 and s["flagged"] == 6
    assert s["by_classification"]["saturated"] == 1 and s["by_classification"]["insufficient_data"] == 1
    assert s["estimated_monthly_savings_eur"] == pytest.approx(
        sum(r["estimated_monthly_savings_eur"] for r in result.recommendations), abs=0.01
    )
    assert s["estimated_monthly_savings_eur"] == pytest.approx(84.41, abs=0.01)
    assert sum(s["by_risk"].values()) == 6
    assert s["priced"] is True and 0 < s["fleet_efficiency_score"] < 100
    assert sum(p["monthly_savings_eur"] for p in s["by_project"]) == pytest.approx(s["estimated_monthly_savings_eur"], abs=0.02)


def test_recommendations_are_sorted_by_savings_descending():
    savings = [r["estimated_monthly_savings_eur"] for r in _fleet_result().recommendations]
    assert savings == sorted(savings, reverse=True)


def test_idle_vm_with_wide_headroom_is_low_risk():
    rec = _rec(_fleet_result(), "legacy-reports-vm")
    assert rec["classification"] == "idle"
    assert (rec["recommended"]["vcpus"], rec["recommended"]["ram_mb"]) == (1, 2048)
    assert rec["risk"]["level"] == "low"
    assert rec["estimated_monthly_savings_eur"] == pytest.approx(15.06, abs=0.01)


def test_only_the_overprovisioned_dimension_is_shrunk():
    """db-primary: idle CPUs but a hot buffer pool -> vCPUs shrink, RAM stays."""
    rec = _rec(_fleet_result(), "db-primary")
    assert rec["recommended"]["ram_mb"] == rec["current"]["ram_mb"] == 32 * 1024
    assert rec["recommended"]["vcpus"] == 6 < rec["current"]["vcpus"]
    assert rec["estimated_monthly_savings_eur"] == pytest.approx(2.74, abs=0.01)


def test_a_burst_limits_how_far_a_low_p95_vm_can_shrink():
    """ci-runner-03: p95 12 % but a 85 % peak on 16 vCPUs needs all 16 under
    the 85 % peak ceiling, so only the RAM comes down -- and the projected
    memory peak is what makes it medium risk."""
    rec = _rec(_fleet_result(), "ci-runner-03")
    assert rec["recommended"]["vcpus"] == 16
    assert rec["recommended"]["ram_mb"] == 24 * 1024
    assert rec["risk"]["level"] == "medium"


def test_thin_history_raises_risk_and_lowers_confidence():
    rec = _rec(_fleet_result(), "dev-sandbox-04")
    assert rec["confidence"] == "low"
    assert rec["risk"]["level"] == "medium"
    assert any("short observation window" in r for r in rec["risk"]["reasons"])


def test_unknown_guest_memory_means_cpu_only_recommendation():
    rec = _rec(_fleet_result(), "etl-scheduler")
    assert rec["recommended"]["ram_mb"] == rec["current"]["ram_mb"]
    assert rec["recommended"]["vcpus"] == 3
    assert rec["confidence"] != "high"  # no memory profile -> never 'high'


def test_a_saturated_vm_is_never_shrunk():
    result = _fleet_result()
    assert "api-gateway-01" not in {r["name"] for r in result.recommendations}


# ===========================================================================
# Unit rules
# ===========================================================================

def _vm(**kw):
    base = dict(vm_id="v", name="v", vcpus=4, ram_mb=8192, cpu_avg=3, cpu_p95=6, cpu_max=10, mem_avg=20, mem_p95=25, mem_max=30, observed_hours=168)
    base.update(kw)
    return rs.VmUtilization(**base)


def test_classify_boundaries():
    p = rs.SizingPolicy()
    assert rs.classify(_vm(cpu_p95=4.9), p) == "idle"
    assert rs.classify(_vm(cpu_p95=5.0), p) == "underutilized"
    assert rs.classify(_vm(cpu_p95=60, mem_p95=70), p) == "right_sized"
    assert rs.classify(_vm(cpu_p95=85), p) == "saturated"
    assert rs.classify(_vm(cpu_p95=10, mem_p95=90), p) == "saturated"  # hot memory beats idle CPU
    assert rs.classify(_vm(observed_hours=23.9), p) == "insufficient_data"
    assert rs.classify(_vm(cpu_p95=None), p) == "insufficient_data"
    assert rs.classify(_vm(vcpus=0), p) == "insufficient_data"
    # High memory with low CPU: memory is not under-used, CPU is.
    assert rs.classify(_vm(cpu_p95=20, mem_p95=80), p) == "underutilized"


def test_efficiency_score_bounds():
    rates, p = vm_utilization.synthetic_rates(), rs.SizingPolicy()
    assert rs.efficiency_score(_vm(cpu_p95=0, mem_p95=0), rates, p) == 0
    assert rs.efficiency_score(_vm(cpu_p95=99, mem_p95=99), rates, p) == 100  # capped, not >100
    assert rs.efficiency_score(_vm(cpu_p95=None), rates, p) is None
    assert rs.efficiency_score(_vm(mem_p95=None, cpu_p95=30), rates, p) == 50.0  # 30 % / 60 % target
    mid = rs.efficiency_score(_vm(cpu_p95=30, mem_p95=37.5), rates, p)
    assert mid == 50.0  # both dimensions at half their target


def test_unpriced_rates_still_recommend_but_report_no_savings():
    rates = rs.UnitRates(vcpu=None, ram_gb=None, source="unavailable")
    result = rs.analyze_fleet(vm_utilization.synthetic_fleet(), rates)
    assert result.recommendations, "sizing doesn't need prices"
    assert all(r["estimated_monthly_savings_eur"] is None for r in result.recommendations)
    assert all(r["current"]["monthly_cost_eur"] is None for r in result.recommendations)
    assert result.summary["priced"] is False
    assert result.summary["estimated_monthly_savings_eur"] is None


def test_savings_below_the_floor_are_not_worth_a_recommendation():
    p = rs.SizingPolicy(min_savings_eur=1000.0)
    result = rs.analyze_fleet(vm_utilization.synthetic_fleet(), vm_utilization.synthetic_rates(), policy=p)
    assert result.recommendations == []


def test_real_flavor_catalog_is_used_and_respects_root_disk():
    catalog = [
        rs.Flavor("m1.tiny", 1, 1024, disk_gb=1),
        rs.Flavor("m1.small", 2, 2048, disk_gb=20),
        rs.Flavor("m1.medium", 2, 4096, disk_gb=40),
        rs.Flavor("m1.large", 4, 8192, disk_gb=80),
    ]
    rates = vm_utilization.synthetic_rates()
    vm = _vm(cpu_p95=4, cpu_max=8, mem_p95=10, mem_max=14, disk_gb=40, flavor_name="m1.large")
    rec = rs.analyze_vm(vm, rates, catalog, rs.SizingPolicy())[1]
    # tiny/small would fit CPU+RAM but have a smaller root disk than the
    # current 40 GB -- Nova cannot shrink a disk, so only m1.medium qualifies.
    assert rec["recommended"]["flavor"] == "m1.medium"
    assert rec["recommended"]["from_catalog"] is True
    assert not any("catalogue" in n for n in rec["notes"])


def test_ladder_recommendations_warn_that_no_real_flavor_was_matched():
    rec = _rec(_fleet_result(), "batch-worker-01")
    assert rec["recommended"]["from_catalog"] is False
    assert any("flavor catalogue" in n for n in rec["notes"])
    assert any("root disk" in n.lower() for n in rec["notes"])


def test_the_recommendation_never_grows_a_dimension():
    catalog = [rs.Flavor("ram-heavy", 1, 32768), rs.Flavor("m1.large", 4, 8192)]
    rec = rs.analyze_vm(_vm(cpu_p95=2, cpu_max=4), vm_utilization.synthetic_rates(), catalog, rs.SizingPolicy())[1]
    assert rec is None  # the only smaller-CPU flavor has *more* RAM than the VM


# ===========================================================================
# Service + endpoint
# ===========================================================================

def test_service_synthetic_uses_reference_rates_on_a_fresh_database():
    out = rightsizing_service.build_recommendations(_db(), source="synthetic")
    assert out["available"] and out["source"] == "synthetic" and out["recommendation_only"] is True
    assert out["summary"]["rate_source"] == "reference-cloud"
    assert out["recommendations"][0]["estimated_monthly_savings_eur"] > 0


def test_service_prefers_the_metered_finops_rates_when_they_exist():
    from datetime import datetime

    db = _db()
    db.add(models.FinopsCloudSample(sampled_at=datetime(2026, 10, 1, 10), rate_vcpu_eur=2.0, rate_ram_gb_eur=1.0))
    db.commit()
    out = rightsizing_service.build_recommendations(db, source="synthetic")
    assert out["summary"]["rate_source"] == "finops-metered"
    batch = next(r for r in out["recommendations"] if r["name"] == "batch-worker-01")
    assert batch["current"]["monthly_cost_eur"] == pytest.approx(8 * 2.0 + 16 * 1.0)


def test_live_without_a_utilisation_source_says_so_instead_of_inventing_data(monkeypatch):
    def boom(window_days, **kw):
        raise vm_utilization.NoUtilizationData("no libvirt exporter")

    monkeypatch.setattr(vm_utilization, "live_fleet", boom)
    out = rightsizing_service.build_recommendations(_db(), source="auto")
    assert out["available"] is False and "libvirt" in out["reason"]
    assert out["source"] == "live", "auto must never silently become synthetic"
    assert out["recommendations"] == [] and out["summary"] is None


def test_live_fleet_joins_inventory_with_prometheus(monkeypatch):
    def fake_agg(expr, window_days):
        cpu = "rate(" in expr
        v = {"avg": 5.0, "p95": 9.0, "max": 20.0, "hours": 168.0} if cpu else {"avg": 20.0, "p95": 30.0, "max": 40.0, "hours": 168.0}
        return {k: {"vm-1": x, "inst-0000002b": x} for k, x in v.items()}

    monkeypatch.setattr(vm_utilization, "_agg", fake_agg)
    inventory = [
        {"id": "vm-1", "name": "app-1", "status": "ACTIVE", "project_id": "p1", "flavor_name": "m1.xl", "flavor_vcpus": 8, "flavor_ram_mb": 16384, "host": "compute1"},
        {"id": "vm-2", "name": "no-metrics", "status": "ACTIVE", "project_id": "p1", "flavor_name": "m1.l", "flavor_vcpus": 4, "flavor_ram_mb": 8192, "host": "compute1"},
        {"id": "vm-3", "name": "inst-0000002b", "status": "ACTIVE", "project_id": "p2", "flavor_name": "m1.l", "flavor_vcpus": 4, "flavor_ram_mb": 8192, "host": None},
        {"id": "vm-4", "name": "no-flavor", "status": "ACTIVE", "project_id": "p2", "flavor_vcpus": None, "flavor_ram_mb": None},
    ]
    fleet = vm_utilization.live_fleet(7, inventory=inventory, project_names={"p1": "Alpha"})
    # vm-2 has no series (omitted, not guessed); vm-3 matches by domain name;
    # vm-4 has no allocation to judge against.
    assert [v.vm_id for v in fleet] == ["vm-1", "vm-3"]
    assert fleet[0].project_name == "Alpha" and fleet[0].cpu_p95 == 9.0 and fleet[0].mem_p95 == 30.0
    assert fleet[0].observed_hours == 168.0


def test_live_fleet_with_no_matching_series_raises(monkeypatch):
    monkeypatch.setattr(vm_utilization, "_agg", lambda expr, w: {k: {} for k in ("avg", "p95", "max", "hours")})
    with pytest.raises(vm_utilization.NoUtilizationData):
        vm_utilization.live_fleet(7, inventory=[{"id": "x", "name": "x", "flavor_vcpus": 2, "flavor_ram_mb": 2048}])


def test_project_filter_recomputes_the_summary():
    out = rightsizing_service.build_recommendations(_db(), source="synthetic", project_id="proj-webshop")
    names = {r["name"] for r in out["recommendations"]} | {r["name"] for r in out["others"]}
    assert names == {"web-frontend-02", "db-primary", "api-gateway-01"}
    assert out["summary"]["vms_analyzed"] == 3 and out["summary"]["flagged"] == 1
    assert out["summary"]["estimated_monthly_savings_eur"] == pytest.approx(2.74, abs=0.01)
    assert out["summary"]["by_classification"]["saturated"] == 1


def test_invalid_source_is_rejected():
    with pytest.raises(ValueError):
        rightsizing_service.build_recommendations(_db(), source="bogus")


def _client():
    db = _db()
    app = FastAPI()
    app.include_router(rightsizing_router.router)
    app.dependency_overrides[get_db] = lambda: db
    return TestClient(app)


def test_endpoint_returns_recommendations():
    r = _client().get("/api/v1/rightsizing?source=synthetic")
    assert r.status_code == 200
    body = r.json()
    top = body["recommendations"][0]
    assert {"estimated_monthly_savings_eur", "risk", "efficiency_score", "current", "recommended"} <= set(top)
    assert top["risk"]["level"] in {"low", "medium", "high"}


def test_endpoint_validates_parameters():
    c = _client()
    assert c.get("/api/v1/rightsizing?source=bogus").status_code == 422
    assert c.get("/api/v1/rightsizing?window_days=0").status_code == 422
    assert c.get("/api/v1/rightsizing?window_days=31").status_code == 422


def test_there_is_no_way_to_apply_a_recommendation_through_the_api():
    """Recommendation only: the router exposes GET and nothing else."""
    methods = {m for route in rightsizing_router.router.routes for m in route.methods}
    assert methods == {"GET"}
