"""Tests for the private-cloud FinOps model (services/finops.py), its metering
inside quota_budget_monitor.check_quota_and_budget, the showback/chargeback
report (services/finops_report.py) and the /api/v1/quotas FinOps endpoints.

Reference cloud used throughout (default FINOPS_* config): two 8-vCPU / 16 GB
hypervisors + a 1000 GB Cinder pool, EUR 292/month split 50/30/20 into
compute/storage/platform pools:

    sellable vCPUs = 16 x 4 (cpu ratio)  = 64    -> vCPU rate = 146*0.6/64  = 1.36875
    sellable RAM   = 32 GB x 1           = 32 GB -> RAM  rate = 146*0.4/32  = 1.825
    storage        = 1000 GB                     -> disk rate = 87.6/1000   = 0.0876
    platform pool                          = 58.4 (shared overhead)
"""
import types
from datetime import datetime, timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import models
from app.auth import get_current_user
from app.db import get_db
from app.routers import quotas as quotas_router
from app.services import finops, finops_report
from app.services import quota_budget_monitor as qbm

from tests.test_quota_budget_monitor import (
    NORMAL_COMPUTE,
    NORMAL_VOLUME,
    _compute_absolute,
    _fake_conn,
    _hypervisor,
    _pool,
    _project,
    _volume_absolute,
)


@pytest.fixture(autouse=True)
def _clean_finops_env(monkeypatch):
    for name in list(__import__("os").environ):
        if name.startswith("FINOPS_") or name.startswith("QUOTA_PROJECT_BUDGETS"):
            monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(qbm, "_load_project_budgets", lambda: {})


def _db():
    engine = create_engine("sqlite:///:memory:", poolclass=StaticPool, connect_args={"check_same_thread": False})
    models.Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def _reference_costs(usages):
    cfg = finops.CostConfig()
    cap = finops.CloudCapacity(physical_vcpus=16, physical_ram_mb=32768, storage_gb=1000)
    pools = finops.split_pools(cfg)
    rates = finops.derive_rates(pools, finops.sellable_capacity(cap, cfg), cfg)
    return pools, rates, finops.allocate_costs(usages, pools, rates)


# ------------------------------------------------------------ pure model --
def test_rates_are_pool_divided_by_sellable_units_not_a_price_list():
    pools, rates, _ = _reference_costs([])
    assert rates.vcpu == pytest.approx(146 * 0.6 / 64)
    assert rates.ram_gb == pytest.approx(146 * 0.4 / 32)
    assert rates.storage_gb == pytest.approx(87.6 / 1000)
    # selling every sellable unit recovers exactly the compute + storage pools
    recovered = rates.vcpu * 64 + rates.ram_gb * 32 + rates.storage_gb * 1000
    assert recovered == pytest.approx(pools.compute + pools.storage)


def test_all_projects_together_pay_exactly_the_fixed_bill():
    usages = [
        finops.ProjectUsage("a", "a", vcpus=10, ram_mb=8 * 1024, gigabytes=100),
        finops.ProjectUsage("b", "b", vcpus=2, ram_mb=2 * 1024, gigabytes=400),
    ]
    pools, _, costs = _reference_costs(usages)
    total = sum(p.fully_loaded_eur for p in costs.projects.values())
    assert total == pytest.approx(pools.total)
    assert costs.idle_eur > 0
    assert not costs.oversold


def test_idle_cost_is_what_nobody_reserved():
    usages = [finops.ProjectUsage("a", "a", vcpus=32, ram_mb=16 * 1024, gigabytes=500)]  # half of everything
    pools, _, costs = _reference_costs(usages)
    assert costs.allocated_compute_eur == pytest.approx(pools.compute / 2)
    assert costs.allocated_storage_eur == pytest.approx(pools.storage / 2)
    assert costs.idle_eur == pytest.approx((pools.compute + pools.storage) / 2)


def test_overselling_is_flagged_and_idle_never_goes_negative():
    usages = [finops.ProjectUsage("a", "a", vcpus=200, ram_mb=100 * 1024, gigabytes=5000)]
    _, _, costs = _reference_costs(usages)
    assert costs.oversold
    assert costs.idle_eur == 0.0


def test_unknown_capacity_is_not_priced_at_zero_or_called_idle():
    cfg = finops.CostConfig()
    cap = finops.CloudCapacity(physical_vcpus=16, physical_ram_mb=32768, storage_gb=None)
    pools = finops.split_pools(cfg)
    rates = finops.derive_rates(pools, finops.sellable_capacity(cap, cfg), cfg)
    costs = finops.allocate_costs([finops.ProjectUsage("a", "a", vcpus=4, gigabytes=50)], pools, rates)
    assert rates.storage_gb is None
    assert costs.unpriced == ["storage"]
    assert costs.idle_storage_eur == 0.0  # whole storage pool must NOT be reported as idle


def test_quota_commitment_counts_unlimited_instead_of_treating_as_zero():
    usages = [
        finops.ProjectUsage("a", "a", quota_vcpus=24, quota_ram_mb=51200, quota_gigabytes=None),
        finops.ProjectUsage("b", "b", quota_vcpus=None, quota_ram_mb=51200, quota_gigabytes=None),
    ]
    c = finops.quota_commitment(usages)
    assert c["vcpus"] == 24 and c["unlimited_vcpus"] == 1
    assert c["ram_mb"] == 102400
    assert c["unlimited_gigabytes"] == 2


def test_config_normalises_pool_shares(monkeypatch):
    monkeypatch.setenv("FINOPS_POOL_SHARE_COMPUTE", "2")
    monkeypatch.setenv("FINOPS_POOL_SHARE_STORAGE", "1")
    monkeypatch.setenv("FINOPS_POOL_SHARE_PLATFORM", "1")
    cfg = finops.load_config()
    assert cfg.share_compute + cfg.share_storage + cfg.share_platform == pytest.approx(1.0)
    assert cfg.share_compute == pytest.approx(0.5)


# ------------------------------------------------------ monitor / metering --
def test_check_writes_hourly_project_and_cloud_samples_and_upserts_within_the_hour():
    db = _db()
    compute = _compute_absolute(total_cores_used=4, total_ram_used=8192)
    volume = _volume_absolute(total_gigabytes_used=40)
    conn = _fake_conn(compute, volume, [_project("p1", "mern-prod")])

    qbm.check_quota_and_budget(db, conn=conn)
    qbm.check_quota_and_budget(db, conn=conn)

    samples = db.query(models.FinopsProjectSample).all()
    assert len(samples) == 1
    s = samples[0]
    assert s.compute_eur_month == pytest.approx(4 * 146 * 0.6 / 64 + 8 * 146 * 0.4 / 32)
    assert s.storage_eur_month == pytest.approx(40 * 0.0876)
    cloud = db.query(models.FinopsCloudSample).one()
    assert cloud.physical_vcpus == 16 and cloud.storage_gb == 1000 and cloud.compute_nodes == 2
    assert cloud.allocated_vcpus == 4
    assert cloud.committed_vcpus == 24  # the project's quota is what the cloud has promised
    # reserved + idle + platform == the whole bill
    assert cloud.allocated_eur_month + cloud.idle_eur_month + cloud.pool_platform_eur == pytest.approx(292.0)


def test_unreadable_capacity_degrades_gracefully_with_a_warning_and_no_budget_alert(monkeypatch):
    monkeypatch.setattr(qbm, "_load_project_budgets", lambda: {"p1": 1.0})
    db = _db()
    conn = _fake_conn(NORMAL_COMPUTE, NORMAL_VOLUME, [_project("p1", "x")], hypervisors=None, pools=None)

    qbm.check_quota_and_budget(db, conn=conn)  # must not raise

    cloud = db.query(models.FinopsCloudSample).one()
    assert cloud.rate_vcpu_eur is None and cloud.rate_storage_gb_eur is None
    assert set(cloud.details["unpriced"]) == {"vCPU", "RAM", "storage"}
    assert len(cloud.details["warnings"]) == 2
    # An unpriceable project must not get a budget verdict based on a made-up cost.
    assert db.query(models.QuotaAlert).filter_by(breach_type="budget_cap").count() == 0


def test_env_fallback_capacity_is_used_when_api_is_unreadable(monkeypatch):
    monkeypatch.setenv("FINOPS_FALLBACK_PHYSICAL_VCPUS", "16")
    monkeypatch.setenv("FINOPS_FALLBACK_PHYSICAL_RAM_GB", "32")
    monkeypatch.setenv("FINOPS_FALLBACK_STORAGE_GB", "1000")
    db = _db()
    conn = _fake_conn(NORMAL_COMPUTE, NORMAL_VOLUME, [_project("p1", "x")], hypervisors=None, pools=None)
    qbm.check_quota_and_budget(db, conn=conn)
    cloud = db.query(models.FinopsCloudSample).one()
    assert cloud.rate_vcpu_eur == pytest.approx(146 * 0.6 / 64)
    assert cloud.details["capacity_source"] == {"vcpus": "env", "ram_mb": "env", "gigabytes": "env"}


def test_disabled_hypervisors_do_not_count_as_capacity():
    db = _db()
    conn = _fake_conn(
        NORMAL_COMPUTE, NORMAL_VOLUME, [_project("p1", "x")],
        hypervisors=[_hypervisor(), _hypervisor(status="disabled")],
    )
    qbm.check_quota_and_budget(db, conn=conn)
    assert db.query(models.FinopsCloudSample).one().physical_vcpus == 8


def test_budget_is_checked_against_direct_cost_not_the_idle_share():
    """A tiny project on a mostly-empty cloud gets a big *idle share* in its
    fully-loaded cost -- but that moves with other projects' behaviour, so it
    must not trip the project's own budget."""
    db = _db()
    db.add(models.ProjectFinopsSetting(project_id="p1", monthly_budget_eur=10.0))
    db.commit()
    compute = _compute_absolute(total_cores_used=1, total_ram_used=1024)
    volume = _volume_absolute(total_gigabytes_used=0)
    conn = _fake_conn(compute, volume, [_project("p1", "tiny")])

    qbm.check_quota_and_budget(db, conn=conn)

    row = db.query(models.QuotaAlert).filter_by(project_id="p1", breach_type="budget_cap").one()
    assert row.used == pytest.approx(146 * 0.6 / 64 + 146 * 0.4 / 32)  # direct only (~3.2 EUR)
    assert row.severity == "normal"
    sample = db.query(models.FinopsProjectSample).one()
    assert sample.idle_eur_month + sample.platform_eur_month > 10.0  # fully loaded would have breached


def test_ui_saved_budget_beats_env_budget(monkeypatch):
    monkeypatch.setattr(qbm, "_load_project_budgets", lambda: {"p1": 1000.0})
    db = _db()
    db.add(models.ProjectFinopsSetting(project_id="p1", monthly_budget_eur=1.0))
    db.commit()
    conn = _fake_conn(NORMAL_COMPUTE, NORMAL_VOLUME, [_project("p1", "x")])
    qbm.check_quota_and_budget(db, conn=conn)
    row = db.query(models.QuotaAlert).filter_by(project_id="p1", breach_type="budget_cap").one()
    assert row.limit == 1.0 and row.severity == "critical"
    assert "BUDGET CAP" in row.message and "CAPACITY" not in row.message


def test_department_precedence_ui_then_env_then_keystone_tag_then_unassigned():
    settings = {"p1": types.SimpleNamespace(department="Finance")}
    r = qbm.resolve_department
    assert r("p1", "n", ["department:Ops"], settings, {"p1": "Eng"}) == "Finance"
    assert r("p2", "n2", ["department:Ops"], {}, {"n2": "Eng"}) == "Eng"
    assert r("p3", "n3", ["foo", "dept:Research"], {}, {}) == "Research"
    assert r("p4", "n4", [], {}, {}) == finops.UNASSIGNED_DEPARTMENT


# ----------------------------------------------------------------- report --
def _seed_samples(db, hours, project_id="p1", name="alpha", department="Eng", compute=30.0, storage=6.0, platform=12.0, idle=24.0, cloud=True):
    for h in range(hours):
        ts = datetime(2026, 9, 1) + timedelta(hours=h)
        db.add(models.FinopsProjectSample(
            project_id=project_id, project_name=name, department=department, sampled_at=ts,
            instances=2, vcpus=4, ram_mb=8192, volumes=1, gigabytes=40, floating_ips=1,
            compute_eur_month=compute, storage_eur_month=storage, network_eur_month=0.0,
            platform_eur_month=platform, idle_eur_month=idle,
        ))
        if cloud:
            db.add(models.FinopsCloudSample(sampled_at=ts, monthly_cost_eur=292.0))
    db.commit()


def test_report_sums_hourly_run_rates_over_the_month():
    db = _db()
    _seed_samples(db, hours=72)
    rep = finops_report.build_report(db, "2026-09", now=datetime(2026, 10, 5))  # past period
    p = rep["projects"][0]
    hours_in_sep = 30 * 24
    assert p["hours_metered"] == 72
    assert p["direct_eur"] == pytest.approx(36.0 * 72 / hours_in_sep, abs=0.01)
    assert p["fully_loaded_eur"] == pytest.approx(72.0 * 72 / hours_in_sep, abs=0.01)
    assert p["avg_vcpus"] == 4
    assert rep["is_current_period"] is False
    assert rep["totals"]["projected_month_end_eur"] is None
    assert rep["coverage_pct"] == pytest.approx(72 / hours_in_sep * 100, abs=0.1)


def test_report_groups_by_department_with_current_setting_winning():
    db = _db()
    _seed_samples(db, 24, "p1", "alpha", "Eng")
    _seed_samples(db, 24, "p2", "beta", "Eng", cloud=False)
    db.add(models.ProjectFinopsSetting(project_id="p2", department="Finance"))
    db.commit()
    rep = finops_report.build_report(db, "2026-09", now=datetime(2026, 10, 5))
    by_dept = {d["department"]: d for d in rep["departments"]}
    assert set(by_dept) == {"Eng", "Finance"}
    assert by_dept["Eng"]["projects"] == 1
    assert sum(d["share_pct"] for d in rep["departments"]) == pytest.approx(100.0, abs=0.2)


def test_current_month_report_projects_month_end_from_run_rate():
    db = _db()
    now = datetime(2026, 9, 10, 12, 30)
    for h in range(24 * 9 + 13):
        ts = datetime(2026, 9, 1) + timedelta(hours=h)
        db.add(models.FinopsProjectSample(
            project_id="p1", project_name="alpha", sampled_at=ts, compute_eur_month=60.0,
            platform_eur_month=0.0, idle_eur_month=0.0,
        ))
        db.add(models.FinopsCloudSample(sampled_at=ts, monthly_cost_eur=292.0))
    db.commit()
    rep = finops_report.build_report(db, "2026-09", now=now)
    p = rep["projects"][0]
    assert rep["is_current_period"]
    # Constant 60 EUR/month run-rate: accrued + remaining must be ~the full 60.
    assert p["projected_month_end_eur"] == pytest.approx(60.0, abs=0.6)


def test_report_flags_partial_coverage_and_budget_use():
    db = _db()
    _seed_samples(db, 10)
    db.add(models.ProjectFinopsSetting(project_id="p1", monthly_budget_eur=40.0))
    db.commit()
    rep = finops_report.build_report(db, "2026-09", now=datetime(2026, 10, 5))
    assert rep["coverage_pct"] < 5
    assert rep["projects"][0]["budget_used_ratio"] == pytest.approx(rep["projects"][0]["direct_eur"] / 40.0, abs=1e-3)


def test_report_rejects_bad_period():
    with pytest.raises(ValueError):
        finops_report.build_report(_db(), "september")


def test_pdf_and_csv_exports_contain_the_project_breakdown():
    db = _db()
    _seed_samples(db, 24, "p1", "alpha", "Eng")
    rep = finops_report.build_report(db, "2026-09", now=datetime(2026, 10, 5))
    pdf = finops_report.render_pdf(rep)
    assert pdf.startswith(b"%PDF-") and len(pdf) > 1500
    rows = finops_report.render_csv(rep).strip().splitlines()
    assert rows[0].startswith("period,project_id,project,department")
    assert "alpha" in rows[1] and "Eng" in rows[1]


def test_pdf_renders_with_no_data():
    rep = finops_report.build_report(_db(), "2026-09", now=datetime(2026, 10, 5))
    assert finops_report.render_pdf(rep).startswith(b"%PDF-")


# ----------------------------------------------------------------- router --
# A one-router app (instead of app.main.app) keeps these tests independent of the
# heavy ML/LLM imports main.py pulls in; auth + DB are overridden the same way.
app = FastAPI()
app.include_router(quotas_router.router)


@pytest.fixture
def api():
    db = _db()
    user = types.SimpleNamespace(role="admin", username="root", is_active=True)
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_current_user] = lambda: user
    yield TestClient(app), db, user
    app.dependency_overrides.clear()


def test_overview_before_any_check_says_unavailable(api):
    client, _, _ = api
    assert client.get("/api/v1/quotas/overview").json()["available"] is False


def test_overview_after_a_check_shows_cloud_projects_and_quota_vs_cost(api, monkeypatch):
    client, db, _ = api
    monkeypatch.setattr("app.routers.quotas._physical_utilization", lambda: None)
    conn = _fake_conn(
        _compute_absolute(total_cores_used=6, total_cores=24, total_ram_used=12288),
        _volume_absolute(total_gigabytes_used=100),
        [_project("p1", "alpha", ["department:Eng"]), _project("p2", "beta")],
    )
    qbm.check_quota_and_budget(db, conn=conn)

    body = client.get("/api/v1/quotas/overview").json()
    assert body["available"] and len(body["projects"]) == 2
    cloud = body["cloud"]
    assert cloud["capacity"]["vcpus"]["sellable"] == 64
    assert cloud["capacity"]["vcpus"]["allocated"] == 12  # 2 projects x 6
    assert cloud["capacity"]["vcpus"]["committed"] == 48  # 2 x 24 quota
    assert cloud["capacity"]["vcpus"]["committed_pct"] == 75.0
    total = sum(p["cost"]["fully_loaded_eur"] for p in body["projects"])
    assert total == pytest.approx(292.0, abs=0.05)
    alpha = next(p for p in body["projects"] if p["project_id"] == "p1")
    assert alpha["department"] == "Eng"
    assert alpha["usage"]["vcpus"]["quota"] == 24 and alpha["cloud_share"]["vcpus_pct"] == 9.4


def test_settings_put_is_admin_only_and_feeds_the_next_check(api):
    client, db, user = api
    r = client.put("/api/v1/quotas/projects/p1/settings", json={"department": " Finance ", "monthly_budget_eur": 25})
    assert r.status_code == 200 and r.json()["department"] == "Finance" and r.json()["updated_by"] == "root"
    assert client.get("/api/v1/quotas/projects/p1/settings").json()["monthly_budget_eur"] == 25

    # partial update leaves the other field alone; 0 clears the budget
    client.put("/api/v1/quotas/projects/p1/settings", json={"monthly_budget_eur": 0})
    got = client.get("/api/v1/quotas/projects/p1/settings").json()
    assert got["monthly_budget_eur"] is None and got["department"] == "Finance"

    user.role = "viewer"
    assert client.put("/api/v1/quotas/projects/p1/settings", json={"department": "X"}).status_code == 403
    assert client.put("/api/v1/quotas/projects/p1/settings", json={"monthly_budget_eur": -5}).status_code in (403, 422)


def test_report_endpoints(api):
    client, db, _ = api
    _seed_samples(db, 24)
    assert client.get("/api/v1/quotas/report", params={"period": "2026-09"}).json()["period"] == "2026-09"
    pdf = client.get("/api/v1/quotas/report.pdf", params={"period": "2026-09"})
    assert pdf.status_code == 200 and pdf.headers["content-type"] == "application/pdf"
    assert "cortex-finops-2026-09.pdf" in pdf.headers["content-disposition"] and pdf.content.startswith(b"%PDF-")
    csv_resp = client.get("/api/v1/quotas/report.csv", params={"period": "2026-09"})
    assert csv_resp.status_code == 200 and "alpha" in csv_resp.text
    assert client.get("/api/v1/quotas/report", params={"period": "nope"}).status_code == 422
