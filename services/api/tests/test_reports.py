"""Roadmap 4.11 -- professional PDF reports (executive / financial / technical).

Acceptance line this file proves: ">=2 of the 3 report variants generate
correctly formatted PDFs on demand" -- here all three, checked structurally
(valid PDF, page count, footer numbering) and by content (the figures that
come from the seeded data actually appear, so a blank-but-valid PDF fails).

SQLite in-memory + faked Prometheus/forecast, same approach as
test_weekly_digest.py; nothing needs Postgres, the network or an LLM.
"""
import io

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pypdf import PdfReader

from app.db import get_db
from app.routers import reports as reports_router
from app.services import report_pdf, reports, rightsizing_service
from tests import report_fixtures as rf


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    for name in list(__import__("os").environ):
        if name.startswith(("RIGHTSIZING_", "FINOPS_", "QUOTA_PROJECT_BUDGETS")):
            monkeypatch.delenv(name, raising=False)
    monkeypatch.delenv("NVIDIA_API_KEY", raising=False)
    rightsizing_service.clear_cache()
    rf.patch_prometheus(monkeypatch)


@pytest.fixture()
def db():
    d = rf.make_db()
    rf.seed(d)
    return d


def _text(pdf: bytes) -> str:
    return "\n".join(p.extract_text() for p in PdfReader(io.BytesIO(pdf)).pages)


def _render(db, variant, **kw):
    kw.setdefault("rightsizing_source", "synthetic")
    return reports.render_report(db, variant, period=rf.PERIOD, now=rf.NOW, **kw)


# ===========================================================================
# Acceptance: every variant is a well-formed PDF
# ===========================================================================

@pytest.mark.parametrize("variant", ["executive", "financial", "technical"])
def test_each_variant_renders_a_valid_numbered_pdf(db, variant):
    pdf, filename = _render(db, variant)
    assert pdf.startswith(b"%PDF-") and pdf.rstrip().endswith(b"%%EOF")
    assert filename == f"cortex-{variant}-report-2026-09.pdf"
    reader = PdfReader(io.BytesIO(pdf))
    n = len(reader.pages)
    assert 1 <= n <= 4
    assert f"Page {n} of {n}" in reader.pages[-1].extract_text()
    assert reader.metadata.title.startswith(f"Cortex {variant}")


def test_executive_content_comes_from_the_real_sources(db):
    text = _text(_render(db, "executive")[0])
    assert "Executive summary" in text and "September 2026" in text
    assert "Action required" in text            # posture: open alert + critical CVE
    assert "€292" in text                       # metered fixed bill
    assert "Critical CVEs" in text and "Resolved by agents" in text
    assert "batch-worker-01" in text and "€22.81" in text   # top right-sizing saving
    assert "DEMO DATA" in text                  # synthetic fleet is labelled
    assert "Recommendations only" in text


def test_financial_content_matches_the_finops_report(db):
    from app.services import finops_report

    fin = finops_report.build_report(db, rf.PERIOD, rf.NOW)
    text = _text(_render(db, "financial")[0])
    assert f"€{fin['totals']['fully_loaded_eur']:,.2f}" in text
    for dept in fin["departments"]:
        assert dept["department"] in text
    for proj in fin["projects"]:
        assert proj["project_name"] in text
    assert "Closed" in text                     # a past period isn't a projection
    assert "How these numbers are computed" in text
    assert "Fleet CPU" not in text              # operations aren't part of this variant


def test_technical_content_covers_capacity_security_agents_and_sizing(db):
    text = _text(_render(db, "technical")[0])
    for needle in ("compute-01", "controller-01", "CVE-2026-1111", "openssl", "webshop",
                   "Restart nova_compute on compute-01", "yosra", "Projected threshold breaches",
                   "legacy-reports-vm", "Risk notes"):
        assert needle in text, needle
    assert "€" in text and "Operational window" in text


def test_financial_report_does_not_touch_prometheus(db, monkeypatch):
    def boom(*a, **k):
        raise AssertionError("financial report must not query Prometheus")

    from app.services import weekly_digest as wd

    monkeypatch.setattr(wd, "_fetch_capacity_series", boom)
    monkeypatch.setattr(wd, "_fetch_forecast_warnings", boom)
    assert _render(db, "financial")[0].startswith(b"%PDF-")


# ===========================================================================
# Degradation: a missing source never prevents the report
# ===========================================================================

def test_empty_database_still_renders_all_variants():
    empty = rf.make_db()
    for variant in reports.VARIANTS:
        pdf, _ = _render(empty, variant)
        assert PdfReader(io.BytesIO(pdf)).pages, variant
    assert "No metered usage" in _text(_render(empty, "financial")[0])


def test_failing_cost_source_is_reported_not_fatal(db, monkeypatch):
    from app.services import finops_report

    monkeypatch.setattr(finops_report, "build_report", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("db down")))
    pdf, _ = _render(db, "executive")
    text = _text(pdf)
    assert "Cost data could not be read" in text
    assert "Fleet CPU" in text or "Action required" in text   # the rest is intact


def test_failing_operations_source_is_reported_not_fatal(db, monkeypatch):
    from app.services import weekly_digest as wd

    monkeypatch.setattr(wd, "collect_digest_data", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("prometheus down")))
    text = _text(_render(db, "technical")[0])
    assert "Operational data" in text and "omitted" in text
    assert "legacy-reports-vm" in text          # right-sizing section survived


def test_live_without_vm_metrics_explains_why_instead_of_inventing_vms(db, monkeypatch):
    from app.services import vm_utilization

    monkeypatch.setattr(vm_utilization, "live_fleet", lambda *a, **k: (_ for _ in ()).throw(vm_utilization.NoUtilizationData("no libvirt exporter")))
    text = _text(_render(db, "financial", rightsizing_source="auto")[0])
    assert "no libvirt exporter" in text
    assert "batch-worker-01" not in text and "DEMO DATA" not in text


def test_ai_summary_is_opt_in_and_falls_back(db, monkeypatch):
    from app.services import weekly_digest as wd

    calls = []
    monkeypatch.setattr(wd, "generate_summary", lambda d: calls.append(1) or {"headline": "LLM headline", "highlights": [], "actions": []})
    assert "LLM headline" not in _text(_render(db, "executive")[0]) and not calls
    assert "LLM headline" in _text(_render(db, "executive", use_ai=True)[0]) and calls


# ===========================================================================
# Validation + rendering safety
# ===========================================================================

def test_bad_inputs_are_value_errors(db):
    with pytest.raises(ValueError):
        reports.collect_report_data(db, "bogus")
    with pytest.raises(ValueError):
        reports.collect_report_data(db, "financial", period="2026-13")
    with pytest.raises(ValueError):
        reports.collect_report_data(db, "financial", rightsizing_source="nope")


def test_hostile_names_cannot_break_the_layout(db):
    from app import models

    db.add(models.QuotaAlert(project_id="x", project_name="<b>evil</b> & 日本語 → ✓", breach_type="capacity_cap",
                             resource="vcpus", used=9, limit=10, ratio=0.9, severity="critical"))
    db.commit()
    pdf, _ = _render(db, "technical")
    text = _text(pdf)
    # Printed literally: if it had been parsed as markup the tags would vanish.
    assert "<b>evil</b> & ??? ? ?" in text
    assert report_pdf._t("日本語 → ✓") == "??? ? ?"  # unrenderable glyphs become '?', not boxes


# ===========================================================================
# Endpoint
# ===========================================================================

def _client(db):
    app = FastAPI()
    app.include_router(reports_router.router)
    app.dependency_overrides[get_db] = lambda: db
    return TestClient(app)


def test_endpoint_lists_the_three_variants(db):
    body = _client(db).get("/api/v1/reports").json()
    assert [v["id"] for v in body["variants"]] == ["executive", "financial", "technical"]


@pytest.mark.parametrize("variant", ["executive", "financial", "technical"])
def test_endpoint_serves_a_pdf_attachment(db, variant):
    r = _client(db).get(f"/api/v1/reports/{variant}.pdf?period=2026-09&rightsizing_source=synthetic")
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/pdf"
    assert f"cortex-{variant}-report-2026-09.pdf" in r.headers["content-disposition"]
    assert r.headers["cache-control"] == "no-store"
    assert r.content.startswith(b"%PDF-")


def test_endpoint_errors(db):
    c = _client(db)
    assert c.get("/api/v1/reports/bogus.pdf").status_code == 404
    assert c.get("/api/v1/reports/financial.pdf?period=nope").status_code == 422
    assert c.get("/api/v1/reports/financial.pdf?rightsizing_source=nope").status_code == 422


def test_endpoint_hides_internal_errors(db, monkeypatch):
    def boom(data):
        raise RuntimeError("secret detail")

    monkeypatch.setitem(report_pdf.RENDERERS, "financial", boom)
    r = _client(db).get("/api/v1/reports/financial.pdf")
    assert r.status_code == 500 and "secret detail" not in r.text
