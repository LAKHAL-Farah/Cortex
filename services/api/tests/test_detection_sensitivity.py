"""
services/api/tests/test_detection_sensitivity.py

Admin-controlled anomaly-detection sensitivity (settings -> anomaly detection),
plus the faster detection-to-inbox path that the roadmap's 4.7 "critical alert
reaches email within 1 minute" target needs.

Sections:
1. The presets, validation, and "balanced == what Cortex always did".
2. The detector honours the config (thresholds, SSH floor, persistence).
3. The "what would this have done?" preview.
4. The HTTP surface and its roles.
5. Detection -> e-mail in one pass.

1, 2, 3 and 5 use an in-memory SQLite database; 4 uses the shared Postgres.
"""
import math
import uuid
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import crud, models
from app.auth import create_access_token, get_current_user, hash_password
from app.db import SessionLocal
from app.main import app
from app.services import alert_routing
from app.services import anomaly_detector as det
from app.services import detection_settings as ds

NOW = datetime(2026, 10, 11, 12, 0, 0)


@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:")
    models.Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, autoflush=False)()
    yield session
    session.close()


def _scripted_z(monkeypatch, z):
    """Make the detector see a fixed z-score, but classify it with whatever
    thresholds the caller passes (that is the part under test)."""
    monkeypatch.setattr(
        det, "score_current_value",
        lambda db, h, m, v, wd, hr, thresholds=None: (z, det.severity_from_zscore(z, thresholds), "robust_zscore", 40),
    )


def _tick(db, config, host="compute-01", metric="cpu_usage", value=90.0, at=NOW):
    det._score_and_record(db, host, metric, value, at.weekday(), at.hour, at, config=config)
    db.commit()


def _events(db, host="compute-01"):
    return db.query(models.AnomalyEvent).filter_by(hostname=host).all()


def _flag(db, host="compute-01", metric="cpu_usage"):
    return db.query(models.AnomalyFlag).filter_by(hostname=host, metric_name=metric).first()


# --------------------------------------------------------------------
# 1. Presets and validation
# --------------------------------------------------------------------

def test_balanced_is_exactly_what_the_detector_always_did():
    balanced = ds.PRESETS["balanced"]
    assert balanced.z == det.THRESHOLDS
    assert balanced.ssh == det.SSH_FAILED_ABSOLUTE_THRESHOLDS
    assert balanced.persist_minutes == 0 and balanced.confirm_ticks(30) == 1
    assert ds.DEFAULT_CONFIG is balanced


def test_presets_are_ordered_from_quiet_to_chatty():
    low, balanced, high = (ds.PRESETS[n] for n in ("low", "balanced", "high"))
    for tier in ("medium", "high", "critical"):
        assert low.z[tier] > balanced.z[tier] > high.z[tier]
        assert low.ssh[tier] > balanced.ssh[tier] > high.ssh[tier]
    assert low.persist_minutes > 0 == balanced.persist_minutes == high.persist_minutes


@pytest.mark.parametrize("z,low,balanced,high", [
    (1.6, "normal", "normal", "medium"),
    (2.5, "normal", "medium", "high"),
    (3.2, "medium", "high", "high"),
    (4.2, "high", "critical", "critical"),
    (5.1, "critical", "critical", "critical"),
    (-5.1, "critical", "critical", "critical"),   # negative deviations count too
])
def test_the_same_reading_is_classified_by_the_chosen_sensitivity(z, low, balanced, high):
    got = [det.severity_from_zscore(z, ds.PRESETS[n].z) for n in ("low", "balanced", "high")]
    assert got == [low, balanced, high]
    assert det.severity_from_zscore(z) == balanced        # no argument = balanced


@pytest.mark.parametrize("count,low,balanced,high", [
    (4, "normal", "normal", "medium"),
    (12, "medium", "medium", "high"),
    (35, "high", "critical", "critical"),
])
def test_the_ssh_floor_follows_the_sensitivity(count, low, balanced, high):
    got = [det._severity_for_ssh_failed_count(count, ds.PRESETS[n].ssh) for n in ("low", "balanced", "high")]
    assert got == [low, balanced, high]


def test_persistence_is_expressed_in_minutes_but_counted_in_ticks():
    cfg = ds.PRESETS["low"]
    assert cfg.persist_minutes == 2
    assert cfg.confirm_ticks(30) == 4 and cfg.confirm_ticks(60) == 2 and cfg.confirm_ticks(120) == 1


@pytest.mark.parametrize("z,ssh,persist,message", [
    ({"medium": 3, "high": 2, "critical": 4}, {"medium": 5, "high": 15, "critical": 30}, 0, "medium < high < critical"),
    ({"medium": 2, "high": 2, "critical": 4}, {"medium": 5, "high": 15, "critical": 30}, 0, "medium < high < critical"),
    ({"medium": 0.1, "high": 3, "critical": 4}, {"medium": 5, "high": 15, "critical": 30}, 0, "between"),
    ({"medium": 2, "high": 3, "critical": 400}, {"medium": 5, "high": 15, "critical": 30}, 0, "between"),
    ({"medium": 2, "high": 3, "critical": float("nan")}, {"medium": 5, "high": 15, "critical": 30}, 0, "finite"),
    ({"medium": 2, "high": 3, "critical": 4}, {"medium": 15, "high": 5, "critical": 30}, 0, "Failed-login"),
    ({"medium": 2, "high": 3, "critical": 4}, {"medium": 0, "high": 5, "critical": 30}, 0, "Failed-login"),
    ({"medium": 2, "high": 3, "critical": 4}, {"medium": 5, "high": 15, "critical": 30}, -1, "persistence"),
    ({"medium": 2, "high": 3, "critical": 4}, {"medium": 5, "high": 15, "critical": 30}, 9999, "persistence"),
    ({"medium": 2, "high": 3}, {"medium": 5, "high": 15, "critical": 30}, 0, "must be a number"),
    ({"medium": "abc", "high": 3, "critical": 4}, {"medium": 5, "high": 15, "critical": 30}, 0, "must be a number"),
])
def test_custom_values_that_make_no_sense_are_rejected_with_the_reason(z, ssh, persist, message):
    with pytest.raises(ds.InvalidSettings, match=message):
        ds.validate_custom(z, ssh, persist)


def test_a_sensible_custom_config_is_accepted():
    cfg = ds.validate_custom({"medium": 2.5, "high": 3.5, "critical": 6}, {"medium": 8, "high": 20, "critical": 50}, 5)
    assert cfg.preset == "custom" and cfg.z["critical"] == 6.0 and cfg.persist_minutes == 5


def test_effective_config_is_balanced_when_nothing_was_ever_saved(db):
    assert ds.effective_config(db) is ds.DEFAULT_CONFIG


def test_effective_config_follows_the_saved_preset_and_custom_values(db):
    ds.save(db, preset="low", custom=None, username="admin", now=NOW)
    assert ds.effective_config(db) is ds.PRESETS["low"]

    custom = {"z": {"medium": 2.5, "high": 3.5, "critical": 4.5}, "ssh": {"medium": 6, "high": 16, "critical": 31},
              "persist_minutes": 3}
    ds.save(db, preset="custom", custom=custom, username="admin", now=NOW)
    cfg = ds.effective_config(db)
    assert (cfg.preset, cfg.z["medium"], cfg.ssh["critical"], cfg.persist_minutes) == ("custom", 2.5, 31, 3)

    # Switching away keeps what was typed, so "custom" again restores it.
    ds.save(db, preset="balanced", custom=None, username="admin", now=NOW)
    assert ds.effective_config(db) is ds.PRESETS["balanced"]
    row = ds.get_row(db)
    assert (row.z_medium, row.ssh_critical, row.persist_minutes) == (2.5, 31, 3)
    assert row.updated_by == "admin"


def test_unusable_stored_values_fall_back_to_balanced_instead_of_stopping_detection(db):
    row = ds.get_row(db)
    row.preset, row.z_medium, row.z_high = "custom", 5.0, 1.0      # not increasing: corrupt
    db.commit()

    assert ds.effective_config(db) is ds.DEFAULT_CONFIG


def test_the_ewma_smoothing_keeps_its_time_constant_when_the_interval_changes():
    # 0.02 was tuned for 60 s ticks; at the default 30 s it must halve.
    assert det.DETECTION_INTERVAL_SECONDS == 30
    assert math.isclose(det.EWMA_ALPHA, 0.01)


# --------------------------------------------------------------------
# 2. The detector honours the config
# --------------------------------------------------------------------

def test_a_reading_that_alerts_at_balanced_is_quiet_at_low(db, monkeypatch):
    _scripted_z(monkeypatch, 2.5)

    _tick(db, ds.PRESETS["low"], host="quiet-host")
    _tick(db, ds.PRESETS["balanced"], host="normal-host")
    _tick(db, ds.PRESETS["high"], host="chatty-host")

    assert _events(db, "quiet-host") == []
    assert [e.severity for e in _events(db, "normal-host")] == ["medium"]
    assert [e.severity for e in _events(db, "chatty-host")] == ["high"]


def test_without_a_config_the_detector_behaves_exactly_as_before(db, monkeypatch):
    _scripted_z(monkeypatch, 2.5)
    det._score_and_record(db, "legacy", "cpu_usage", 90.0, NOW.weekday(), NOW.hour, NOW)
    db.commit()

    assert [e.severity for e in _events(db, "legacy")] == ["medium"]


def test_detect_anomalies_reads_the_saved_sensitivity(db, monkeypatch):
    now = datetime.utcnow()
    monkeypatch.setattr(det, "METRICS", {"cpu_usage": "dummy"})
    monkeypatch.setattr(det, "fetch_instant",
                        lambda q: [{"metric": {"instance": "h:9100", "node": "h"}, "value": [0, "90"]}])
    monkeypatch.setattr(det.auth_log_metrics, "get_failed_login_stats",
                        lambda h: type("S", (), {"count": 0, "source_ips": []})())
    monkeypatch.setattr(det.auth_log_metrics, "get_successful_login_stats",
                        lambda h: type("S", (), {"count": 0, "source_ips": []})())
    _scripted_z(monkeypatch, 2.5)

    det.detect_anomalies(db)
    assert [e.severity for e in _events(db, "h")] == ["medium"]            # nothing saved -> balanced

    for event in _events(db, "h"):
        event.resolved_at = now
    db.commit()
    ds.save(db, preset="low", custom=None, username="admin")
    det.detect_anomalies(db)
    assert len(_events(db, "h")) == 1                                      # low: no new alert for the same reading


def test_ssh_failed_logins_use_the_configured_floor(db, monkeypatch):
    monkeypatch.setattr(det, "METRICS", {})
    for host_count in (12,):
        monkeypatch.setattr(det.auth_log_metrics, "get_failed_login_stats",
                            lambda h, c=host_count: type("S", (), {"count": c, "source_ips": ["10.0.0.9"]})())
    monkeypatch.setattr(det.auth_log_metrics, "get_successful_login_stats",
                        lambda h: type("S", (), {"count": 0, "source_ips": []})())
    monkeypatch.setattr(det.crud, "list_nodes", lambda db: [type("N", (), {"hostname": "bastion"})()])
    _scripted_z(monkeypatch, 0.0)

    ds.save(db, preset="high", custom=None, username="admin")
    det.detect_anomalies(db)

    event = _events(db, "bastion")[0]
    assert event.severity == "high" and event.details["triggered_by"] == "absolute_threshold"


# ---- persistence ----

@pytest.fixture
def persist_two_minutes(monkeypatch):
    monkeypatch.setattr(det, "DETECTION_INTERVAL_SECONDS", 30)
    return ds.PRESETS["low"].__class__("custom", ds.DEFAULT_CONFIG.z, ds.DEFAULT_CONFIG.ssh, 2)   # 4 ticks of 30 s


def test_a_one_tick_blip_never_becomes_an_alert(db, monkeypatch, persist_two_minutes):
    _scripted_z(monkeypatch, 5.0)
    _tick(db, persist_two_minutes)
    _scripted_z(monkeypatch, 0.0)
    _tick(db, persist_two_minutes)

    assert _events(db) == []
    assert _flag(db).severity == "normal" and _flag(db).consecutive_anomalous_ticks == 0


def test_an_alert_opens_once_the_reading_has_stayed_abnormal_long_enough(db, monkeypatch, persist_two_minutes):
    _scripted_z(monkeypatch, 5.0)

    for tick in (1, 2, 3):
        _tick(db, persist_two_minutes)
        assert _events(db) == [], f"tick {tick} should still be waiting"
        assert _flag(db).severity == "normal"                 # not shown on the dashboard either
    _tick(db, persist_two_minutes)

    assert [e.severity for e in _events(db)] == ["critical"]
    assert _flag(db).severity == "critical" and _flag(db).consecutive_anomalous_ticks == 4


def test_a_dip_in_the_middle_restarts_the_wait(db, monkeypatch, persist_two_minutes):
    for z in (5.0, 5.0, 5.0, 0.0, 5.0, 5.0, 5.0):
        _scripted_z(monkeypatch, z)
        _tick(db, persist_two_minutes)

    assert _events(db) == []


def test_once_open_an_alert_is_not_delayed_again_and_still_resolves(db, monkeypatch, persist_two_minutes):
    _scripted_z(monkeypatch, 5.0)
    for _ in range(4):
        _tick(db, persist_two_minutes)
    _scripted_z(monkeypatch, 2.5)                              # still abnormal (high enough to stay open)
    _tick(db, persist_two_minutes)
    assert len(_events(db)) == 1 and _events(db)[0].resolved_at is None

    _scripted_z(monkeypatch, 0.0)
    _tick(db, persist_two_minutes)
    assert _events(db)[0].resolved_at is not None and _events(db)[0].resolution_type == "automatic"


def test_an_operator_closed_alert_stays_closed_while_the_signal_stays_high(db, monkeypatch, persist_two_minutes):
    _scripted_z(monkeypatch, 5.0)
    for _ in range(4):
        _tick(db, persist_two_minutes)
    event = _events(db)[0]
    event.resolved_at, event.resolution_type = NOW, "manual"
    _flag(db).manually_resolved_at = NOW
    db.commit()

    for _ in range(3):
        _tick(db, persist_two_minutes)

    assert len(_events(db)) == 1                               # no re-open


# --------------------------------------------------------------------
# 3. Preview
# --------------------------------------------------------------------

def _past_event(db, *, z, severity, minutes, metric="cpu_usage", value=90.0, method="robust_zscore", host=None,
                days_ago=1):
    started = NOW - timedelta(days=days_ago)
    db.add(models.AnomalyEvent(
        hostname=host or f"h-{uuid.uuid4().hex[:6]}", metric_name=metric, current_value=value, z_score=z,
        severity=severity, method=method, started_at=started, resolved_at=started + timedelta(minutes=minutes),
    ))
    db.commit()


def test_the_preview_replays_recent_alerts_against_a_candidate(db):
    _past_event(db, z=2.4, severity="medium", minutes=1)          # a mild blip
    _past_event(db, z=3.4, severity="high", minutes=30)
    _past_event(db, z=4.6, severity="critical", minutes=20)
    _past_event(db, z=0.0, severity="critical", minutes=5, method="service_state_check", metric="service_state")
    _past_event(db, z=9.0, severity="critical", minutes=5, days_ago=30)     # outside the 7-day window

    balanced = ds.preview(db, ds.PRESETS["balanced"], interval_seconds=30, now=NOW)
    low = ds.preview(db, ds.PRESETS["low"], interval_seconds=30, now=NOW)
    high = ds.preview(db, ds.PRESETS["high"], interval_seconds=30, now=NOW)

    assert balanced["alerts_raised"] == 3 and balanced["unaffected_service_alerts"] == 1
    assert balanced["alerts_would_raise"] == 3
    assert balanced["by_severity_would_raise"] == {"medium": 1, "high": 1, "critical": 1}
    # low: the 2.4 blip is gone (below 3.0); 3.4 is only "medium"; 4.6 is "high"
    assert low["alerts_would_raise"] == 2
    assert low["by_severity_would_raise"] == {"medium": 1, "high": 1, "critical": 0}
    assert high["alerts_would_raise"] == 3
    assert high["by_severity_raised"] == {"medium": 1, "high": 1, "critical": 1}


def test_the_preview_counts_short_alerts_as_gone_when_persistence_is_required(db):
    _past_event(db, z=5.5, severity="critical", minutes=1)        # 1 minute: would not have lasted 90 s
    _past_event(db, z=5.5, severity="critical", minutes=10)
    persist = ds.validate_custom(ds.DEFAULT_CONFIG.z, ds.DEFAULT_CONFIG.ssh, 2)

    result = ds.preview(db, persist, interval_seconds=30, now=NOW)

    assert result["alerts_raised"] == 2 and result["alerts_would_raise"] == 1


def test_the_preview_applies_the_ssh_floor_to_failed_login_alerts(db):
    _past_event(db, z=0.0, severity="medium", minutes=5, metric="ssh_failed_logins_5min", value=8)
    assert ds.preview(db, ds.PRESETS["balanced"], interval_seconds=30, now=NOW)["alerts_would_raise"] == 1
    assert ds.preview(db, ds.PRESETS["low"], interval_seconds=30, now=NOW)["alerts_would_raise"] == 0


# --------------------------------------------------------------------
# 4. HTTP
# --------------------------------------------------------------------

client = TestClient(app)


@pytest.fixture(autouse=True)
def _no_leaked_auth_override():
    had = get_current_user in app.dependency_overrides
    saved = app.dependency_overrides.pop(get_current_user, None)
    yield
    if had:
        app.dependency_overrides[get_current_user] = saved


@pytest.fixture
def clean_settings():
    """The settings row is shared state in Postgres: start and end balanced."""
    def reset():
        session = SessionLocal()
        try:
            session.query(models.DetectionSettings).delete()
            session.commit()
        finally:
            session.close()
    reset()
    yield
    reset()


def _user(role):
    session = SessionLocal()
    try:
        user = crud.create_user(
            session, username=f"sens-{role}-{uuid.uuid4().hex[:6]}", password_hash=hash_password("x" * 12), role=role
        )
        return user, {"Authorization": f"Bearer {create_access_token(user)}"}
    finally:
        session.close()


URL = "/api/v1/settings/anomaly-detection"
CUSTOM = {"preset": "custom", "custom": {"z": {"medium": 2.5, "high": 3.5, "critical": 5.0},
                                         "ssh": {"medium": 8, "high": 20, "critical": 40}, "persist_minutes": 3}}


def test_the_default_is_balanced_and_nothing_changed(clean_settings):
    _, viewer_h = _user("viewer")

    assert client.get(URL).status_code == 401
    body = client.get(URL, headers=viewer_h).json()

    assert body["preset"] == "balanced"
    assert body["effective"]["z"] == {"medium": 2.0, "high": 3.0, "critical": 4.0}
    assert body["effective"]["confirm_ticks"] == 1
    assert set(body["presets"]) == {"low", "balanced", "high"}
    assert body["limits"]["persist_minutes"] == [0, 60]


def test_only_an_admin_can_change_or_preview_the_sensitivity(clean_settings):
    _, viewer_h = _user("viewer")
    _, operator_h = _user("operator")

    for headers in (viewer_h, operator_h):
        assert client.put(URL, headers=headers, json={"preset": "low"}).status_code == 403
        assert client.post(f"{URL}/preview", headers=headers, json={"preset": "low"}).status_code == 403
    assert client.put(URL, json={"preset": "low"}).status_code == 401
    assert client.get(URL, headers=viewer_h).json()["preset"] == "balanced"          # untouched


def test_an_admin_can_pick_a_preset_and_it_takes_effect(clean_settings):
    admin, admin_h = _user("admin")

    response = client.put(URL, headers=admin_h, json={"preset": "low"})

    assert response.status_code == 200
    body = response.json()
    assert body["preset"] == "low" and body["updated_by"] == admin.username and body["updated_at"]
    assert body["effective"]["z"]["medium"] == 3.0 and body["effective"]["confirm_ticks"] == 4   # 2 min @ 30 s
    session = SessionLocal()
    try:
        assert ds.effective_config(session) is ds.PRESETS["low"]                      # what the detector will read
    finally:
        session.close()


def test_custom_values_are_validated_and_stored(clean_settings):
    _, admin_h = _user("admin")

    ok = client.put(URL, headers=admin_h, json=CUSTOM)
    assert ok.status_code == 200 and ok.json()["effective"]["z"]["critical"] == 5.0
    assert ok.json()["effective"]["ssh"]["medium"] == 8

    bad = client.put(URL, headers=admin_h, json={**CUSTOM, "custom": {**CUSTOM["custom"],
                                                                      "z": {"medium": 4, "high": 3, "critical": 5}}})
    assert bad.status_code == 422 and "medium < high < critical" in bad.json()["detail"]
    assert client.put(URL, headers=admin_h, json={"preset": "custom"}).status_code == 422       # no numbers
    assert client.put(URL, headers=admin_h, json={"preset": "extreme"}).status_code == 422
    assert client.get(URL, headers=admin_h).json()["effective"]["z"]["critical"] == 5.0          # bad PUT changed nothing


def test_switching_back_to_a_preset_remembers_the_custom_numbers(clean_settings):
    _, admin_h = _user("admin")
    client.put(URL, headers=admin_h, json=CUSTOM)

    client.put(URL, headers=admin_h, json={"preset": "balanced"})
    body = client.get(URL, headers=admin_h).json()

    assert body["preset"] == "balanced" and body["effective"]["z"]["medium"] == 2.0
    assert body["custom"]["z"]["medium"] == 2.5 and body["custom"]["persist_minutes"] == 3


def test_the_preview_endpoint_saves_nothing(clean_settings):
    _, admin_h = _user("admin")

    response = client.post(f"{URL}/preview", headers=admin_h, json={"preset": "low"})

    assert response.status_code == 200
    assert set(response.json()) >= {"window_days", "alerts_raised", "alerts_would_raise",
                                    "by_severity_raised", "by_severity_would_raise", "unaffected_service_alerts"}
    assert client.get(URL, headers=admin_h).json()["preset"] == "balanced"
    assert client.post(f"{URL}/preview", headers=admin_h, json={"preset": "custom"}).status_code == 422


# --------------------------------------------------------------------
# 5. Detection -> e-mail in one pass (roadmap 4.7)
# --------------------------------------------------------------------

def test_a_critical_alert_is_emailed_by_the_same_pass_that_raised_it(db, monkeypatch):
    """No waiting for the next dispatcher tick: detection and delivery are one step."""
    from app import main

    sent = []
    monkeypatch.setattr(alert_routing.alert_email, "smtp_configured", lambda: True)
    monkeypatch.setattr(alert_routing.alert_email, "send_email", lambda to, subject, body: sent.append(subject))
    _scripted_z(monkeypatch, 6.0)
    monkeypatch.setattr(main, "detect_anomalies", lambda session: _tick(session, ds.DEFAULT_CONFIG, host="compute-42"))

    started = datetime.utcnow()
    main._detect_and_notify(db)

    assert len(sent) == 1 and "CRITICAL" in sent[0] and "compute-42" in sent[0]
    email = db.query(models.AlertNotification).filter_by(channel="email").one()
    assert email.status == "sent"
    assert (email.sent_at - started).total_seconds() < 5            # seconds, not a dispatcher interval


def test_a_dispatch_failure_does_not_fail_detection(db, monkeypatch):
    from app import main

    ran = []
    monkeypatch.setattr(main, "detect_anomalies", lambda session: ran.append("detected"))
    monkeypatch.setattr(main.alert_routing, "dispatch_due", lambda session: (_ for _ in ()).throw(RuntimeError("smtp")))

    main._detect_and_notify(db)             # must not raise

    assert ran == ["detected"]
