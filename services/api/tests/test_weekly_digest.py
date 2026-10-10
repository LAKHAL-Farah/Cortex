"""Roadmap 4.5 -- weekly digest email (capacity trend, security posture, what
the agents caught). Acceptance line this file proves: "digest auto-sent on
schedule, real data from the week".

Sections:
1. Scheduling -- slot math, and `send_due_digest`'s restart-safe /
   double-send-safe behaviour (the "auto-sent on schedule" half).
2. Aggregation -- capacity, security and agent sections built from real rows
   (the "real data from the week" half), incl. graceful degradation.
3. AI summary -- parsing, and that every failure mode falls back instead of
   breaking the digest.
4. Rendering -- the HTML/text are complete, escaped, and survive empty data.

SQLite in-memory + faked Prometheus/forecast, same approach as
test_quota_budget_monitor.py; nothing here needs Postgres or the network.
"""
import time
import types
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import models
from app.services import alert_email, weekly_digest as wd
from app.services import weekly_digest_email as email_view

NOW = datetime(2026, 10, 12, 9, 30)  # a Monday, 09:30 UTC


def _db():
    engine = create_engine("sqlite:///:memory:")
    models.Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def _ts(dt: datetime) -> float:
    return dt.replace(tzinfo=timezone.utc).timestamp()


def _points(start: datetime, hours: int, value_fn):
    return [[_ts(start + timedelta(hours=h)), str(value_fn(h))] for h in range(hours)]


# ===========================================================================
# 1. Scheduling
# ===========================================================================

def test_latest_slot_is_this_weeks_slot_once_it_has_passed():
    assert wd.latest_slot(NOW, weekday=0, hour=8) == datetime(2026, 10, 12, 8, 0)


def test_latest_slot_is_last_weeks_slot_before_it_has_passed():
    assert wd.latest_slot(NOW, weekday=0, hour=10) == datetime(2026, 10, 5, 10, 0)
    assert wd.latest_slot(NOW, weekday=4, hour=8) == datetime(2026, 10, 9, 8, 0)


def test_next_slot_is_always_in_the_future():
    assert wd.next_slot(NOW, 0, 8) == datetime(2026, 10, 19, 8, 0)
    assert wd.next_slot(NOW, 0, 10) == datetime(2026, 10, 12, 10, 0)


@pytest.fixture
def mailer(monkeypatch):
    sent = []
    monkeypatch.setattr(alert_email, "smtp_configured", lambda: True)
    monkeypatch.setattr(alert_email, "send_email", lambda to, subject, text, html=None: sent.append((to, subject, text, html)))
    monkeypatch.setattr(wd, "build_digest_email", lambda db, now=None, use_ai=True: ("subj", "text", "<html></html>", {"posture": {"level": "ok"}}))
    return sent


def test_first_run_seeds_the_schedule_instead_of_sending_immediately(mailer):
    db = _db()
    assert wd.send_due_digest(db, NOW) == "seeded"
    assert mailer == []
    assert alert_email.get_settings(db).digest_last_sent_at == datetime(2026, 10, 12, 8, 0)


def test_sends_once_when_the_slot_passes_and_never_twice(mailer):
    db = _db()
    settings = alert_email.get_settings(db)
    settings.digest_last_sent_at = datetime(2026, 10, 5, 8, 0)  # last week's send
    db.commit()

    assert wd.send_due_digest(db, NOW) == "sent"
    assert len(mailer) == 1 and mailer[0][0] == settings.recipient_email
    # Same tick again (or a second replica ticking a moment later): no resend.
    assert wd.send_due_digest(db, NOW + timedelta(minutes=5)) == "not_due"
    assert len(mailer) == 1


def test_not_due_before_the_scheduled_hour(mailer):
    db = _db()
    settings = alert_email.get_settings(db)
    settings.digest_last_sent_at = datetime(2026, 10, 5, 8, 0)
    db.commit()
    assert wd.send_due_digest(db, datetime(2026, 10, 12, 7, 59)) == "not_due"
    assert mailer == []


def test_stale_slots_are_skipped_rather_than_sent_days_late(mailer):
    db = _db()
    settings = alert_email.get_settings(db)
    settings.digest_last_sent_at = datetime(2026, 10, 5, 8, 0)
    db.commit()
    assert wd.send_due_digest(db, datetime(2026, 10, 14, 9, 0)) == "not_due"  # 2 days after the slot
    assert mailer == []


def test_failed_send_is_retried_on_the_next_tick(monkeypatch, mailer):
    db = _db()
    settings = alert_email.get_settings(db)
    previous = datetime(2026, 10, 5, 8, 0)
    settings.digest_last_sent_at = previous
    db.commit()

    def boom(*a, **k):
        raise RuntimeError("smtp down")

    monkeypatch.setattr(alert_email, "send_email", boom)
    assert wd.send_due_digest(db, NOW) == "failed"
    assert alert_email.get_settings(db).digest_last_sent_at == previous  # claim rolled back

    monkeypatch.setattr(alert_email, "send_email", lambda *a, **k: mailer.append(a))
    assert wd.send_due_digest(db, NOW + timedelta(minutes=5)) == "sent"


def test_disabled_and_unconfigured_smtp_do_nothing(monkeypatch, mailer):
    db = _db()
    settings = alert_email.get_settings(db)
    settings.digest_last_sent_at = datetime(2026, 10, 5, 8, 0)
    settings.digest_enabled = False
    db.commit()
    assert wd.send_due_digest(db, NOW) == "disabled"

    settings.digest_enabled = True
    db.commit()
    monkeypatch.setattr(alert_email, "smtp_configured", lambda: False)
    assert wd.send_due_digest(db, NOW) == "smtp_not_configured"
    assert mailer == []


def test_manual_send_does_not_move_the_schedule(mailer):
    db = _db()
    settings = alert_email.get_settings(db)
    settings.digest_last_sent_at = datetime(2026, 10, 5, 8, 0)
    db.commit()
    result = wd.send_digest_now(db)
    assert result["recipient"] == settings.recipient_email and len(mailer) == 1
    assert alert_email.get_settings(db).digest_last_sent_at == datetime(2026, 10, 5, 8, 0)


# ===========================================================================
# 2. Aggregation
# ===========================================================================

NODES = [
    {"hostname": "compute-01", "role": "compute", "instance": "10.0.0.11:9100"},
    {"hostname": "ctrl-01", "role": "controller", "instance": "10.0.0.10:9100"},
]


def _series():
    start = NOW - timedelta(days=14)
    # compute-01: ~40% the week before, ~60% this week. ctrl-01 flat 20%.
    def hot(h):
        return 40 if h < 168 else 60

    return {
        "cpu": {
            "10.0.0.11:9100": _points(start, 336, hot),
            "10.0.0.10:9100": _points(start, 336, lambda h: 20),
            "10.9.9.9:9100": _points(start, 336, lambda h: 99),  # unregistered target: ignored
        },
        "memory": {"10.0.0.11:9100": _points(start, 336, lambda h: 70)},
        "disk": {},
    }


def test_capacity_trend_compares_this_week_to_the_previous_one():
    cap = wd.build_capacity_section(NODES, _series(), NOW, [], [])
    assert cap["available"]
    cpu = cap["fleet"]["cpu"]
    # fleet avg this week = mean(60, 20) = 40; last week mean(40, 20) = 30
    assert cpu["avg"] == 40.0 and cpu["prev_avg"] == 30.0 and cpu["delta"] == 10.0
    assert cpu["peak"] == 60.0 and cpu["peak_host"] == "compute-01"
    assert len(cpu["daily"]) == 7 and all(d is not None for d in cpu["daily"])
    # unregistered 99% target must not leak into the fleet numbers
    assert cap["nodes"][0]["hostname"] == "compute-01"  # busiest first
    assert cap["nodes"][0]["cpu_delta"] == 20.0


def test_capacity_degrades_instead_of_reporting_zeroes_when_prometheus_is_down():
    cap = wd.build_capacity_section(NODES, None, NOW, [], [])
    assert cap["available"] is False and cap["fleet"] == {} and cap["nodes"] == []


def test_nan_samples_from_scrape_gaps_are_ignored():
    start = NOW - timedelta(days=14)
    pts = _points(start, 336, lambda h: 50)
    pts[300][1] = "NaN"
    cap = wd.build_capacity_section(NODES, {"cpu": {"10.0.0.11:9100": pts}}, NOW, [], [])
    assert cap["fleet"]["cpu"]["avg"] == 50.0


def _cache_row(host, **signals):
    base = {"sec_group_signal": {}, "cve_signal": {}, "exposed_port_signal": {}, "ebpf_signal": {}, "auth_signal": {}}
    base.update(signals)
    has = any(v for sig in base.values() for v in sig.values() if isinstance(v, (list, bool)) and v)
    return types.SimpleNamespace(hostname=host, raw_data=base, has_signal=has, degraded=False)


def test_security_section_counts_signals_and_ranks_cves_by_severity():
    rows = [
        _cache_row(
            "compute-01",
            cve_signal={"matches": [
                {"cve_id": "CVE-1", "severity": "medium", "package": "libx", "installed_version": "1", "fixed_version": "2"},
                {"cve_id": "CVE-2", "severity": "critical", "package": "openssl", "installed_version": "3.0.1", "fixed_version": "3.0.9"},
            ]},
            sec_group_signal={"risky_rules": [{"reason": "0.0.0.0/0 on 22"}], "drift": []},
        ),
        _cache_row("ctrl-01"),
    ]
    runs = [types.SimpleNamespace(status="ok"), types.SimpleNamespace(status="ok"), types.SimpleNamespace(status="degraded")]
    sec = wd.build_security_section(rows, runs)
    assert sec["totals"]["cves"] == 2 and sec["totals"]["risky_rules"] == 1
    assert sec["cve_by_severity"]["critical"] == 1
    assert sec["top_cves"][0]["cve_id"] == "CVE-2"  # critical first
    assert sec["nodes_with_signal"] == 1 and sec["hosts_with_signal"] == ["compute-01"]
    assert sec["scan_ok_pct"] == pytest.approx(66.7, abs=0.1)


def test_anomaly_stats_resolution_and_notable_ordering():
    t0 = NOW - timedelta(days=2)
    ev = lambda host, sev, z, res=None, rtype=None: types.SimpleNamespace(
        hostname=host, metric_name="cpu_usage", severity=sev, z_score=z, current_value=90.0,
        started_at=t0, resolved_at=(t0 + timedelta(minutes=res)) if res is not None else None, resolution_type=rtype,
    )
    stats = wd.build_anomaly_stats(
        [ev("a", "medium", 3.1, 30), ev("b", "critical", 6.0), ev("a", "high", 4.0, 90, "manual")], prev_count=1, open_now=1
    )
    assert stats["total"] == 3 and stats["prev_total"] == 1
    assert stats["by_severity"] == {"critical": 1, "high": 1, "medium": 1}
    assert stats["resolved"] == 2 and stats["manually_resolved"] == 1 and stats["auto_resolved"] == 1
    assert stats["avg_resolution_minutes"] == 60.0
    assert stats["notable"][0]["severity"] == "critical" and stats["notable"][0]["resolved"] is False
    assert stats["top_hosts"][0] == ("a", 2)


def test_collect_digest_data_reads_the_weeks_real_rows(monkeypatch):
    db = _db()
    db.add_all([
        models.Node(hostname="compute-01", ip_address="10.0.0.11", role="compute", exporter_port=9100),
        models.Node(hostname="ctrl-01", ip_address="10.0.0.10", role="controller", exporter_port=9100),
    ])
    # one episode this week (resolved), one last week, one older than that
    db.add_all([
        models.AnomalyEvent(hostname="compute-01", metric_name="cpu_usage", severity="critical", z_score=5.0, current_value=97.0,
                            started_at=NOW - timedelta(days=2), resolved_at=NOW - timedelta(days=2, hours=-1)),
        models.AnomalyEvent(hostname="ctrl-01", metric_name="ram_usage", severity="high", z_score=4.0, current_value=91.0,
                            started_at=NOW - timedelta(days=9), resolved_at=NOW - timedelta(days=8)),
        models.AnomalyEvent(hostname="ctrl-01", metric_name="ram_usage", severity="high", z_score=4.0, current_value=91.0,
                            started_at=NOW - timedelta(days=30), resolved_at=NOW - timedelta(days=29)),
    ])
    db.add(models.SecurityFindingCache(
        hostname="compute-01", role="compute", has_signal=True, degraded=False, answer="",
        raw_data={"cve_signal": {"matches": [{"cve_id": "CVE-9", "severity": "high", "package": "p", "installed_version": "1", "fixed_version": "2"}]}},
    ))
    db.add(models.SecurityScanRun(status="ok", started_at=NOW - timedelta(hours=3), finished_at=NOW - timedelta(hours=3)))
    db.add(models.QuotaAlert(project_id="p1", project_name="stagiaires", breach_type="capacity_cap", resource="vcpus",
                             used=9, limit=10, ratio=0.9, severity="warning"))
    pid = uuid.uuid4()
    db.add(models.RemediationProposal(id=pid, proposal_id="fix-1", status="executed",
                                      proposal={"symptom_title": "Nova compute service down", "host": "compute-01"}))
    for i, ev in enumerate(("proposed", "approved", "executed"), start=1):
        db.add(models.RemediationAuditEntry(
            id=i, proposal_row_id=pid, proposal_id="fix-1", event=ev, to_status=ev, actor_username="yosra", actor_role="admin",
            prev_hash="0" * 64, entry_hash=f"{i}" * 64, created_at=NOW.replace(tzinfo=timezone.utc) - timedelta(days=1),
        ))
    db.commit()

    monkeypatch.setattr(wd, "_fetch_capacity_series", lambda nodes, now: _series())
    monkeypatch.setattr(wd, "_fetch_forecast_warnings", lambda nodes: [
        {"hostname": "compute-01", "metric": "disk", "current": 84.0, "threshold": 90.0, "eta_days": 3.2, "already_breached": False}])

    data = wd.collect_digest_data(db, NOW)

    assert data["node_count"] == 2
    assert data["window"]["label"] == "5 Oct – 12 Oct 2026"
    assert data["capacity"]["fleet"]["cpu"]["avg"] == 40.0
    assert data["capacity"]["quota_alerts"][0]["project"] == "stagiaires"
    assert data["security"]["totals"]["cves"] == 1 and data["security"]["scan_ok_pct"] == 100.0
    an = data["agents"]["anomalies"]
    assert an["total"] == 1 and an["prev_total"] == 1 and an["resolved"] == 1 and an["still_open"] == 0
    rem = data["agents"]["remediation"]
    assert (rem["proposed"], rem["approved"], rem["executed"]) == (1, 1, 1)
    assert rem["recent"][0]["title"] == "Nova compute service down" and rem["recent"][0]["actor"] == "yosra"
    assert data["posture"]["level"] == "attention"  # forecast breach + quota warning + open CVE signal


def test_posture_escalates_to_action_on_critical_findings():
    base = {
        "agents": {"anomalies": {"still_open": 0, "by_severity": {}}},
        "security": {"cve_by_severity": {"critical": 1}, "total_signals": 1},
        "capacity": {"forecast_warnings": [], "quota_alerts": []},
    }
    assert wd.compute_posture(base)["level"] == "action"
    base["security"] = {"cve_by_severity": {}, "total_signals": 0}
    assert wd.compute_posture(base)["level"] == "ok"


# ===========================================================================
# 3. AI summary
# ===========================================================================

def _quiet_data():
    cap = wd.build_capacity_section(NODES, _series(), NOW, [], [])
    data = {
        "generated_at": NOW.isoformat() + "Z",
        "window": {"start": (NOW - timedelta(days=7)).isoformat() + "Z", "end": NOW.isoformat() + "Z", "label": "5 Oct – 12 Oct 2026"},
        "node_count": 2,
        "capacity": cap,
        "security": wd.build_security_section([_cache_row("ctrl-01")], []),
        "agents": {
            "anomalies": wd.build_anomaly_stats([], 0, 0),
            "remediation": {"proposed": 0, "approved": 0, "rejected": 0, "executed": 0, "execution_failed": 0, "recent": []},
            "activity": {"questions": 4, "prev_questions": 2, "by_agent": [{"agent": "monitoring", "count": 4}], "critic_flagged_pct": 0.0, "degraded_pct": 0.0},
        },
    }
    data["posture"] = wd.compute_posture(data)
    return data


GOOD_JSON = '{"headline": "Quiet week.", "highlights": ["CPU up 10 pts on compute-01."], "actions": ["Review compute-01."]}'


def test_parse_summary_tolerates_fences_and_reasoning_tags():
    wrapped = "<think>let me reason</think>\n```json\n" + GOOD_JSON + "\n```"
    parsed = wd._parse_summary(wrapped)
    assert parsed["headline"] == "Quiet week." and parsed["source"] == "ai" and parsed["actions"] == ["Review compute-01."]


@pytest.mark.parametrize("bad", ["", "no json here", '{"headline": ""}', '{"headline": "x", "highlights": []}', "{broken"])
def test_parse_summary_rejects_unusable_output(bad):
    assert wd._parse_summary(bad) is None


def test_summary_without_an_api_key_is_the_deterministic_fallback(monkeypatch):
    monkeypatch.delenv("NVIDIA_API_KEY", raising=False)
    summary = wd.generate_summary(_quiet_data())
    assert summary["source"] == "fallback" and summary["headline"] and summary["highlights"]


def test_summary_uses_the_llm_when_it_answers_and_falls_back_when_it_does_not(monkeypatch):
    monkeypatch.setenv("NVIDIA_API_KEY", "test")
    monkeypatch.setattr(wd, "_call_llm", lambda payload: GOOD_JSON)
    assert wd.generate_summary(_quiet_data())["source"] == "ai"

    monkeypatch.setattr(wd, "_call_llm", lambda payload: "I cannot help with that")
    assert wd.generate_summary(_quiet_data())["source"] == "fallback"

    def boom(payload):
        raise RuntimeError("[500] Internal Server Error")

    monkeypatch.setattr(wd, "_call_llm", boom)
    assert wd.generate_summary(_quiet_data())["source"] == "fallback"


def test_a_hung_llm_cannot_block_the_digest(monkeypatch):
    monkeypatch.setenv("NVIDIA_API_KEY", "test")
    monkeypatch.setattr(wd, "DIGEST_LLM_TIMEOUT_SECONDS", 0.2)
    monkeypatch.setattr(wd, "_call_llm", lambda payload: time.sleep(3) or GOOD_JSON)
    started = time.monotonic()
    assert wd.generate_summary(_quiet_data())["source"] == "fallback"
    assert time.monotonic() - started < 2


def test_llm_prompt_payload_contains_only_aggregates_not_raw_rows():
    payload = wd._compact_for_llm(_quiet_data())
    assert payload["capacity"]["fleet_avg_percent_with_change_vs_last_week_in_points"]["cpu"]["avg"] == 40.0
    assert "daily" not in str(payload["capacity"]["fleet_avg_percent_with_change_vs_last_week_in_points"])


# ===========================================================================
# 4. Rendering
# ===========================================================================

def test_html_contains_every_section_and_the_real_numbers():
    data = _quiet_data()
    summary = {"headline": "Quiet week overall.", "highlights": ["CPU is up 10 pts."], "actions": ["Review compute-01."], "source": "ai"}
    html = email_view.render_html(data, summary)
    for needle in ("Quiet week overall.", "Capacity trend", "Security posture", "What the agents caught",
                   "compute-01", "40%", "Recommended next steps", "NVIDIA NIM", "All clear"):
        assert needle in html
    assert "<!DOCTYPE html>" in html and 'name="color-scheme"' in html
    assert "<img" not in html  # nothing remote to block or host


def test_dynamic_strings_are_html_escaped():
    data = _quiet_data()
    data["capacity"]["nodes"][0]["hostname"] = '<script>alert("x")</script>'
    summary = {"headline": "<b>boom</b>", "highlights": ["a & b"], "actions": [], "source": "ai"}
    html = email_view.render_html(data, summary)
    assert "<script>" not in html and "<b>boom</b>" not in html
    assert "&lt;script&gt;" in html and "a &amp; b" in html


def test_renders_when_every_source_is_empty_or_down():
    data = _quiet_data()
    data["capacity"] = wd.build_capacity_section(NODES, None, NOW, [], [])
    data["security"] = wd.build_security_section([], [])
    html = email_view.render_html(data, wd.fallback_summary(data))
    assert "unavailable" in html and "No security scan results" in html
    text = email_view.render_text(data, wd.fallback_summary(data))
    assert "Capacity metrics were unavailable" in text


def test_text_alternative_and_subject_are_useful_on_their_own():
    data = _quiet_data()
    summary = wd.fallback_summary(data)
    text = email_view.render_text(data, summary)
    assert "CORTEX WEEKLY DIGEST" in text and "01  CAPACITY TREND" in text and "03  WHAT THE AGENTS CAUGHT" in text
    assert email_view.subject_line(data).startswith("[Cortex] Weekly digest")


def test_cta_only_renders_with_a_valid_public_url(monkeypatch):
    data, summary = _quiet_data(), {"headline": "h", "highlights": ["x"], "actions": [], "source": "fallback"}
    monkeypatch.delenv("CORTEX_PUBLIC_URL", raising=False)
    assert "Open Cortex dashboard" not in email_view.render_html(data, summary)
    monkeypatch.setenv("CORTEX_PUBLIC_URL", "https://cortex.example.com/")
    assert 'href="https://cortex.example.com/dashboard"' in email_view.render_html(data, summary)
    monkeypatch.setenv("CORTEX_PUBLIC_URL", "javascript:alert(1)")
    assert "javascript:" not in email_view.render_html(data, summary)


# ===========================================================================
# 5. HTTP surface (settings router, mounted on a bare app -- no Postgres)
# ===========================================================================

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool

from app.auth import get_current_user
from app.db import get_db
from app.routers import settings as settings_router


def _client(role="admin"):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    models.Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    app = FastAPI()
    app.include_router(settings_router.router)

    def _get_db():
        db = Session()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = _get_db
    app.dependency_overrides[get_current_user] = lambda: models.User(username="u", role=role)
    return TestClient(app), Session


def test_get_returns_schedule_defaults_and_next_send():
    client, _ = _client()
    body = client.get("/api/v1/settings/weekly-digest").json()
    assert body["enabled"] is True and body["weekday"] == 0 and body["hour_utc"] == 8
    assert body["next_send_at"].endswith("Z")


@pytest.mark.parametrize("payload", [
    {"enabled": True, "weekday": 7, "hour_utc": 8},
    {"enabled": True, "weekday": 0, "hour_utc": 24},
    {"enabled": True, "weekday": -1, "hour_utc": 8},
])
def test_put_rejects_out_of_range_schedules(payload):
    client, _ = _client()
    assert client.put("/api/v1/settings/weekly-digest", json=payload).status_code == 422


def test_put_updates_schedule_and_reseeds_so_it_cannot_fire_immediately():
    client, Session = _client()
    resp = client.put("/api/v1/settings/weekly-digest", json={"enabled": True, "weekday": 4, "hour_utc": 17})
    assert resp.status_code == 200 and resp.json()["weekday"] == 4 and resp.json()["hour_utc"] == 17
    db = Session()
    row = alert_email.get_settings(db)
    assert row.digest_last_sent_at == wd.latest_slot(datetime.utcnow(), 4, 17)


def test_viewers_can_read_but_not_change_or_trigger():
    client, _ = _client(role="viewer")
    assert client.get("/api/v1/settings/weekly-digest").status_code == 200
    assert client.put("/api/v1/settings/weekly-digest", json={"enabled": False, "weekday": 0, "hour_utc": 8}).status_code == 403
    assert client.post("/api/v1/settings/weekly-digest/send").status_code == 403


def test_send_now_reports_smtp_problems_as_503(monkeypatch):
    client, _ = _client()
    monkeypatch.setattr(wd, "build_digest_email", lambda db, now=None, use_ai=True: ("s", "t", "<p/>", {"posture": {"level": "ok"}}))
    monkeypatch.delenv("SMTP_HOST", raising=False)
    resp = client.post("/api/v1/settings/weekly-digest/send")
    assert resp.status_code == 503 and "SMTP is not configured" in resp.json()["detail"]


def test_send_now_succeeds_and_preview_returns_html(monkeypatch):
    client, _ = _client()
    sent = []
    monkeypatch.setattr(wd, "build_digest_email", lambda db, now=None, use_ai=True: ("s", "t", "<p>digest</p>", {"posture": {"level": "ok"}}))
    monkeypatch.setattr(alert_email, "send_email", lambda *a, **k: sent.append(a))
    resp = client.post("/api/v1/settings/weekly-digest/send")
    assert resp.status_code == 200 and resp.json()["message"] == "weekly digest sent" and len(sent) == 1

    preview = client.get("/api/v1/settings/weekly-digest/preview?use_ai=false")
    assert preview.status_code == 200 and preview.headers["content-type"].startswith("text/html") and "digest" in preview.text
