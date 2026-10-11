"""Realistic seed data for the report tests (and for eyeballing the PDFs).

Not a test module (no `test_` prefix) -- shared by test_reports.py.

Cost period is September 2026 (closed), "now" is Monday 12 Oct 2026. Three
projects in two departments metered every hour of September so coverage is
100 %, at the FinOps reference-cloud run-rates.
"""
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import models
from app.services import weekly_digest as wd

NOW = datetime(2026, 10, 12, 9, 30)
PERIOD = "2026-09"


def make_db():
    engine = create_engine("sqlite:///:memory:", poolclass=StaticPool, connect_args={"check_same_thread": False})
    models.Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def _points(start, hours, fn):
    return [[(start + timedelta(hours=h)).replace(tzinfo=timezone.utc).timestamp(), str(fn(h))] for h in range(hours)]


def fake_prometheus_series():
    start = NOW - timedelta(days=14)
    return {
        "cpu": {
            "10.0.0.11:9100": _points(start, 336, lambda h: 40 if h < 168 else 62),
            "10.0.0.12:9100": _points(start, 336, lambda h: 18),
            "10.0.0.10:9100": _points(start, 336, lambda h: 25),
        },
        "memory": {
            "10.0.0.11:9100": _points(start, 336, lambda h: 71),
            "10.0.0.12:9100": _points(start, 336, lambda h: 35),
            "10.0.0.10:9100": _points(start, 336, lambda h: 58),
        },
        "disk": {"10.0.0.10:9100": _points(start, 336, lambda h: 47 + h // 40)},
    }


def seed(db, with_finance=True):
    db.add_all([
        models.Node(hostname="controller-01", ip_address="10.0.0.10", role="controller", exporter_port=9100),
        models.Node(hostname="compute-01", ip_address="10.0.0.11", role="compute", exporter_port=9100),
        models.Node(hostname="compute-02", ip_address="10.0.0.12", role="compute", exporter_port=9100),
    ])
    db.add_all([
        models.AnomalyEvent(hostname="compute-01", metric_name="cpu_usage", severity="critical", z_score=5.2, current_value=97.0,
                            started_at=NOW - timedelta(days=2), resolved_at=NOW - timedelta(days=2, hours=-1)),
        models.AnomalyEvent(hostname="controller-01", metric_name="ram_usage", severity="high", z_score=4.1, current_value=91.0,
                            started_at=NOW - timedelta(days=1)),
    ])
    db.add_all([
        models.SecurityFindingCache(hostname="compute-01", role="compute", has_signal=True, degraded=False, answer="", raw_data={
            "cve_signal": {"matches": [
                {"cve_id": "CVE-2026-1111", "severity": "critical", "package": "openssl", "installed_version": "3.0.2", "fixed_version": "3.0.15"},
                {"cve_id": "CVE-2026-2222", "severity": "high", "package": "libvirt", "installed_version": "8.0", "fixed_version": "8.4"}]},
            "sec_group_signal": {"risky_rules": [{"rule": "0.0.0.0/0:22"}], "drift": []}}),
        models.SecurityFindingCache(hostname="controller-01", role="controller", has_signal=False, degraded=False, answer="", raw_data={}),
    ])
    db.add(models.SecurityScanRun(status="ok", started_at=NOW - timedelta(hours=3), finished_at=NOW - timedelta(hours=3)))
    db.add(models.QuotaAlert(project_id="p-web", project_name="webshop", breach_type="budget_cap", resource="estimated_cost_eur",
                             used=92, limit=100, ratio=0.92, severity="warning"))
    pid = uuid.uuid4()
    db.add(models.RemediationProposal(id=pid, proposal_id="fix-1", status="executed",
                                      proposal={"symptom_title": "Restart nova_compute on compute-01", "host": "compute-01"}))
    for i, ev in enumerate(("proposed", "approved", "executed"), start=1):
        db.add(models.RemediationAuditEntry(id=i, proposal_row_id=pid, proposal_id="fix-1", event=ev, to_status=ev,
                                            actor_username="yosra", actor_role="admin", prev_hash="0" * 64,
                                            entry_hash=f"{i}" * 64, created_at=NOW.replace(tzinfo=timezone.utc) - timedelta(days=1)))
    if with_finance:
        projects = [("p-web", "webshop", "E-commerce", 8, 16384, 200), ("p-ana", "analytics", "Data", 16, 32768, 400),
                    ("p-fin", "finance", "Data", 4, 8192, 100)]
        db.add_all([models.ProjectFinopsSetting(project_id="p-web", department="E-commerce", monthly_budget_eur=100.0),
                    models.ProjectFinopsSetting(project_id="p-ana", department="Data", monthly_budget_eur=250.0)])
        start = datetime(2026, 9, 1)
        for h in range(30 * 24):
            ts = start + timedelta(hours=h)
            db.add(models.FinopsCloudSample(sampled_at=ts, physical_vcpus=16, physical_ram_mb=32768, storage_gb=1000, compute_nodes=2,
                                            cpu_allocation_ratio=4.0, ram_allocation_ratio=1.0, monthly_cost_eur=292.0,
                                            pool_compute_eur=146.0, pool_storage_eur=87.6, pool_platform_eur=58.4,
                                            rate_vcpu_eur=1.36875, rate_ram_gb_eur=1.825, rate_storage_gb_eur=0.0876))
            for pid_, name, dept, vcpus, ram, gb in projects:
                compute = vcpus * 1.36875 + ram / 1024 * 1.825
                storage = gb * 0.0876
                db.add(models.FinopsProjectSample(project_id=pid_, project_name=name, department=dept, sampled_at=ts, vcpus=vcpus,
                                                  ram_mb=ram, gigabytes=gb, compute_eur_month=compute, storage_eur_month=storage,
                                                  platform_eur_month=58.4 * 0.33, idle_eur_month=12.0))
    db.commit()


def patch_prometheus(monkeypatch):
    monkeypatch.setattr(wd, "_fetch_capacity_series", lambda nodes, now: fake_prometheus_series())
    monkeypatch.setattr(wd, "_fetch_forecast_warnings", lambda nodes: [
        {"hostname": "controller-01", "metric": "disk", "current": 59.0, "threshold": 85.0, "eta_days": 11.4, "already_breached": False}])
