"""
services/api/tests/test_alert_routing.py

Roadmap 4.7: alerts route by severity (dashboard vs. email) with basic
escalation. The acceptance lines this file exists to prove:

- "critical alert reaches email within 1 min"  -> section 2
- "low severity stays dashboard-only"          -> section 1

Sections:
1. Routing: which channels each severity gets, idempotency, severity upgrades.
2. Delivery: the outbox dispatcher, alert-to-inbox latency, retries.
3. Escalation: unacknowledged alerts, acknowledgement, resolution.
4. The detector actually routes (integration), and the HTTP surface.

1-3 run on an in-memory SQLite database with an injected clock and sender, so
there is no SMTP, no sleeping and nothing shared between tests.
"""
import uuid
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app import crud, models
from app.auth import create_access_token, get_current_user, hash_password
from app.db import SessionLocal
from app.main import app
from app.services import alert_routing as routing

T0 = datetime(2026, 10, 10, 12, 0, 0)


@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:")
    models.Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, autoflush=False)()
    yield session
    session.close()


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    for name in (
        "ALERT_EMAIL_SEVERITIES", "ALERT_ESCALATE_CRITICAL_AFTER_MINUTES", "ALERT_ESCALATE_HIGH_AFTER_MINUTES",
        "ALERT_ESCALATION_RECIPIENT_EMAIL", "ALERT_EMAIL_MAX_ATTEMPTS", "ALERT_EMAIL_RETRY_SECONDS",
    ):
        monkeypatch.delenv(name, raising=False)


def _event(severity="critical", host=None, started_at=T0) -> models.AnomalyEvent:
    return models.AnomalyEvent(
        hostname=host or f"node-{uuid.uuid4().hex[:6]}", metric_name="cpu_usage",
        current_value=97.0, z_score=5.0, severity=severity, method="robust_zscore", started_at=started_at,
    )


def _rows(db, event) -> list[models.AlertNotification]:
    return list(db.execute(
        select(models.AlertNotification).where(models.AlertNotification.event_id == event.id)
        .order_by(models.AlertNotification.created_at, models.AlertNotification.channel)
    ).scalars())


def _route(db, event, now=T0):
    created = routing.route_event(db, event, now=now)
    db.commit()
    return created


class Mailbox:
    """Stands in for SMTP and records what would have been sent."""

    def __init__(self):
        self.sent: list[tuple[str, str, str]] = []
        self.fail_times = 0

    def __call__(self, recipient, subject, body):
        if self.fail_times > 0:
            self.fail_times -= 1
            raise ConnectionError("smtp down")
        self.sent.append((recipient, subject, body))


class Clock:
    def __init__(self, start=T0):
        self.now = start

    def __call__(self):
        return self.now

    def advance(self, **kw):
        self.now += timedelta(**kw)


# --------------------------------------------------------------------
# 1. Routing
# --------------------------------------------------------------------

@pytest.mark.parametrize("severity", ["medium", "low", "normal", "something-new"])
def test_low_severity_stays_dashboard_only(db, severity):
    event = _event(severity)
    _route(db, event)

    rows = _rows(db, event)
    assert [(r.channel, r.status) for r in rows] == [("dashboard", "delivered")]
    assert routing.channels_for(severity) == ("dashboard",)


@pytest.mark.parametrize("severity", ["high", "critical"])
def test_high_and_critical_go_to_the_dashboard_and_the_email_outbox(db, severity):
    event = _event(severity)
    _route(db, event)

    by_channel = {r.channel: r for r in _rows(db, event)}
    assert by_channel["dashboard"].status == "delivered"
    assert by_channel["email"].status == "pending"
    assert by_channel["email"].recipient
    assert by_channel["email"].kind == "initial"


def test_the_emailed_severities_are_configurable(db, monkeypatch):
    monkeypatch.setenv("ALERT_EMAIL_SEVERITIES", "critical")
    high, critical = _event("high"), _event("critical")
    _route(db, high)
    _route(db, critical)

    assert [r.channel for r in _rows(db, high)] == ["dashboard"]
    assert sorted(r.channel for r in _rows(db, critical)) == ["dashboard", "email"]


def test_routing_the_same_severity_twice_adds_nothing(db):
    event = _event("critical")
    _route(db, event)
    again = _route(db, event)

    assert again == []
    assert len(_rows(db, event)) == 2


def test_an_alert_that_gets_worse_is_routed_again(db):
    event = _event("medium")
    _route(db, event)
    assert [r.channel for r in _rows(db, event)] == ["dashboard"]

    event.severity = "critical"
    _route(db, event, now=T0 + timedelta(minutes=5))

    rows = _rows(db, event)
    assert sorted((r.channel, r.kind, r.severity) for r in rows) == [
        ("dashboard", "initial", "medium"),
        ("dashboard", "severity_upgrade", "critical"),
        ("email", "severity_upgrade", "critical"),
    ]


def test_email_switched_off_in_settings_is_recorded_as_skipped_not_lost(db):
    settings = routing.alert_email.get_settings(db)
    settings.enabled = False
    db.commit()
    event = _event("critical")
    _route(db, event)

    email = next(r for r in _rows(db, event) if r.channel == "email")
    assert email.status == "skipped"
    assert "switched off" in email.last_error


def test_routing_never_raises_into_the_detector(db, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("settings table exploded")

    monkeypatch.setattr(routing.alert_email, "get_settings", boom)
    event = _event("critical")

    assert routing.route_event(db, event, now=T0) == []   # swallowed, logged
    db.commit()
    assert db.get(models.AnomalyEvent, event.id) is not None   # the alert itself survived


# --------------------------------------------------------------------
# 2. Delivery
# --------------------------------------------------------------------

def test_critical_alert_reaches_email_within_one_minute(db):
    """The roadmap's acceptance line, measured the way an operator would: from
    the moment the alert exists to the moment the mail is handed to SMTP."""
    clock, mailbox = Clock(), Mailbox()
    event = _event("critical", host="compute-07")
    _route(db, event, now=clock())

    # The dispatcher ticks every ALERT_DISPATCH_INTERVAL_SECONDS (10 s); the
    # worst case is an alert created just after a tick.
    clock.advance(seconds=10)
    summary = routing.dispatch_due(db, sender=mailbox, clock=clock)

    assert summary["sent"] == 1
    recipient, subject, body = mailbox.sent[0]
    assert "CRITICAL" in subject and "compute-07" in subject
    assert "Host/service: compute-07" in body
    email = next(r for r in _rows(db, event) if r.channel == "email")
    assert email.status == "sent"
    latency = (email.sent_at - email.created_at).total_seconds()
    assert latency <= 60, f"alert took {latency}s to reach email"
    assert routing.notification_out(email)["latency_seconds"] == latency


def test_a_low_severity_alert_sends_nothing(db):
    mailbox = Mailbox()
    _route(db, _event("medium"))

    summary = routing.dispatch_due(db, sender=mailbox, clock=Clock(T0 + timedelta(minutes=5)))

    assert summary == {"sent": 0, "failed": 0, "retrying": 0}
    assert mailbox.sent == []


def test_a_failed_send_is_retried_with_backoff_and_then_delivered(db):
    clock, mailbox = Clock(), Mailbox()
    mailbox.fail_times = 2
    event = _event("critical")
    _route(db, event, now=clock())

    first = routing.dispatch_due(db, sender=mailbox, clock=clock)
    assert first["retrying"] == 1 and not mailbox.sent
    email = next(r for r in _rows(db, event) if r.channel == "email")
    assert email.status == "pending" and email.attempts == 1 and "smtp down" in email.last_error
    assert email.next_attempt_at == clock() + timedelta(seconds=10)

    # Not due yet -> not retried early.
    assert routing.dispatch_due(db, sender=mailbox, clock=clock)["retrying"] == 0
    clock.advance(seconds=10)
    assert routing.dispatch_due(db, sender=mailbox, clock=clock)["retrying"] == 1   # waits 20 s now
    clock.advance(seconds=20)
    assert routing.dispatch_due(db, sender=mailbox, clock=clock)["sent"] == 1

    db.refresh(email)
    assert email.status == "sent" and email.attempts == 3 and email.last_error is None


def test_it_gives_up_after_max_attempts_and_says_why(db, monkeypatch):
    monkeypatch.setenv("ALERT_EMAIL_MAX_ATTEMPTS", "2")
    clock, mailbox = Clock(), Mailbox()
    mailbox.fail_times = 99
    event = _event("critical")
    _route(db, event, now=clock())

    routing.dispatch_due(db, sender=mailbox, clock=clock)
    clock.advance(minutes=1)
    summary = routing.dispatch_due(db, sender=mailbox, clock=clock)

    email = next(r for r in _rows(db, event) if r.channel == "email")
    assert summary["failed"] == 1 and email.status == "failed" and email.attempts == 2
    assert "smtp down" in email.last_error
    # ...and the dashboard row is untouched: the alert is still visible.
    assert next(r for r in _rows(db, event) if r.channel == "dashboard").status == "delivered"


def test_without_smtp_configured_the_row_fails_fast_instead_of_retrying_forever(db, monkeypatch):
    monkeypatch.setattr(routing.alert_email, "smtp_configured", lambda: False)
    event = _event("critical")
    _route(db, event)

    summary = routing.dispatch_due(db, clock=Clock(T0 + timedelta(seconds=5)))   # no sender -> real SMTP path

    email = next(r for r in _rows(db, event) if r.channel == "email")
    assert summary["failed"] == 1 and email.status == "failed"
    assert "SMTP is not configured" in email.last_error


def test_each_email_is_sent_once(db):
    mailbox, clock = Mailbox(), Clock()
    _route(db, _event("critical"), now=clock())

    routing.dispatch_due(db, sender=mailbox, clock=clock)
    routing.dispatch_due(db, sender=mailbox, clock=clock)

    assert len(mailbox.sent) == 1


def test_a_severity_upgrade_email_says_so(db):
    clock, mailbox = Clock(), Mailbox()
    event = _event("high", host="storage-01")
    _route(db, event, now=clock())
    routing.dispatch_due(db, sender=mailbox, clock=clock)
    event.severity = "critical"
    _route(db, event, now=clock())
    routing.dispatch_due(db, sender=mailbox, clock=clock)

    assert len(mailbox.sent) == 2
    _, subject, body = mailbox.sent[1]
    assert "severity raised" in subject and "CRITICAL" in subject
    assert "got worse" in body


# --------------------------------------------------------------------
# 3. Escalation
# --------------------------------------------------------------------

def _open_and_send(db, severity="critical", host=None, clock=None):
    clock = clock or Clock()
    event = _event(severity, host=host)
    _route(db, event, now=clock())
    mailbox = Mailbox()
    routing.dispatch_due(db, sender=mailbox, clock=clock)
    return event, clock, mailbox


def test_an_unacknowledged_critical_alert_escalates_after_fifteen_minutes(db, monkeypatch):
    monkeypatch.setenv("ALERT_ESCALATION_RECIPIENT_EMAIL", "oncall-lead@example.com")
    event, clock, mailbox = _open_and_send(db, host="compute-09")

    clock.advance(minutes=14)
    assert routing.escalate_unacknowledged(db, now=clock()) == 0        # not yet

    clock.advance(minutes=1)
    assert routing.escalate_unacknowledged(db, now=clock()) == 1
    routing.dispatch_due(db, sender=mailbox, clock=clock)

    recipient, subject, body = mailbox.sent[-1]
    assert recipient == "oncall-lead@example.com"
    assert "ESCALATION" in subject and "compute-09" in subject
    assert "Acknowledge it" in body
    assert routing.routing_summary(_rows(db, event))["escalated"] is True


def test_escalation_happens_once_per_severity(db):
    event, clock, mailbox = _open_and_send(db)
    clock.advance(hours=3)

    assert routing.escalate_unacknowledged(db, now=clock()) == 1
    assert routing.escalate_unacknowledged(db, now=clock()) == 0
    routing.dispatch_due(db, sender=mailbox, clock=clock)
    clock.advance(hours=3)
    assert routing.escalate_unacknowledged(db, now=clock()) == 0


def test_escalation_falls_back_to_the_normal_recipient(db):
    event, clock, mailbox = _open_and_send(db)
    normal = next(r for r in _rows(db, event) if r.channel == "email").recipient
    clock.advance(minutes=20)
    routing.escalate_unacknowledged(db, now=clock())
    routing.dispatch_due(db, sender=mailbox, clock=clock)

    assert mailbox.sent[-1][0] == normal


def test_acknowledging_stops_escalation(db):
    event, clock, _ = _open_and_send(db)
    clock.advance(minutes=10)

    assert routing.acknowledge(db, event, username="alice", now=clock()) is True
    clock.advance(hours=2)

    assert routing.escalate_unacknowledged(db, now=clock()) == 0
    assert event.acknowledged_by == "alice"


def test_the_first_acknowledgement_is_kept(db):
    event, clock, _ = _open_and_send(db)
    assert routing.acknowledge(db, event, username="alice", now=clock()) is True
    clock.advance(minutes=5)

    assert routing.acknowledge(db, event, username="bob", now=clock()) is False
    assert event.acknowledged_by == "alice"


def test_a_resolved_alert_does_not_escalate(db):
    event, clock, _ = _open_and_send(db)
    event.resolved_at = clock() + timedelta(minutes=5)
    db.commit()
    clock.advance(hours=2)

    assert routing.escalate_unacknowledged(db, now=clock()) == 0


def test_a_high_alert_waits_an_hour_and_medium_never_escalates(db):
    high, clock, _ = _open_and_send(db, "high")
    medium = _event("medium")
    _route(db, medium, now=clock())

    clock.advance(minutes=59)
    assert routing.escalate_unacknowledged(db, now=clock()) == 0
    clock.advance(minutes=1)
    assert routing.escalate_unacknowledged(db, now=clock()) == 1
    clock.advance(days=2)
    routing.escalate_unacknowledged(db, now=clock())
    assert [r.channel for r in _rows(db, medium)] == ["dashboard"]


def test_escalation_can_be_switched_off_per_severity(db, monkeypatch):
    monkeypatch.setenv("ALERT_ESCALATE_CRITICAL_AFTER_MINUTES", "0")
    _, clock, _ = _open_and_send(db)
    clock.advance(days=1)

    assert routing.escalate_unacknowledged(db, now=clock()) == 0


def test_a_worsening_alert_restarts_its_escalation_clock(db):
    """Medium for an hour, then critical: the critical timer runs from when it
    *became* critical, so nobody is paged for an alert that only just got bad."""
    clock = Clock()
    event = _event("medium")
    _route(db, event, now=clock())
    clock.advance(hours=1)
    event.severity = "critical"
    _route(db, event, now=clock())

    clock.advance(minutes=10)
    assert routing.escalate_unacknowledged(db, now=clock()) == 0
    clock.advance(minutes=5)
    assert routing.escalate_unacknowledged(db, now=clock()) == 1


def test_an_alert_with_no_routing_history_is_never_escalated(db):
    """Alerts that opened before routing existed have no rows; they must not all
    page someone the moment this ships."""
    event = _event("critical")
    db.add(event)
    db.commit()

    assert routing.escalate_unacknowledged(db, now=T0 + timedelta(days=30)) == 0


def test_run_cycle_queues_escalations_and_sends(db, monkeypatch):
    sent = Mailbox()
    monkeypatch.setattr(routing.alert_email, "smtp_configured", lambda: True)
    monkeypatch.setattr(routing.alert_email, "send_email", sent)
    event = _event("critical", started_at=datetime.utcnow() - timedelta(hours=2))
    routing.route_event(db, event, now=datetime.utcnow() - timedelta(hours=1))
    db.commit()

    summary = routing.run_cycle(db)

    assert summary["escalations_queued"] == 1 and summary["sent"] == 2
    assert [("ESCALATION" in s) for _, s, _ in sent.sent].count(True) == 1


# --------------------------------------------------------------------
# 4. Integration + HTTP
# --------------------------------------------------------------------

def test_routing_is_called_when_an_episode_opens_and_worsens(db, monkeypatch):
    """Drive the detector's own bookkeeping with scripted severities and check
    the alert_notifications it leaves behind."""
    from app.services import anomaly_detector as det

    severities = iter(["medium", "critical", "critical"])
    monkeypatch.setattr(det, "score_current_value", lambda *a, **k: (4.0, next(severities), "robust_zscore", 40))

    now = datetime.utcnow()
    for _ in range(3):
        det._score_and_record(db, "compute-01", "cpu_usage", 91.0, now.weekday(), now.hour, now)
    db.commit()

    event = db.execute(select(models.AnomalyEvent).where(models.AnomalyEvent.hostname == "compute-01")).scalar_one()
    rows = _rows(db, event)
    assert sorted((r.channel, r.severity) for r in rows) == [
        ("dashboard", "critical"), ("dashboard", "medium"), ("email", "critical"),
    ]


client = TestClient(app)


@pytest.fixture(autouse=True)
def _no_leaked_auth_override():
    had = get_current_user in app.dependency_overrides
    saved = app.dependency_overrides.pop(get_current_user, None)
    yield
    if had:
        app.dependency_overrides[get_current_user] = saved


def _user(role):
    session = SessionLocal()
    try:
        user = crud.create_user(
            session, username=f"alert-test-{uuid.uuid4().hex[:8]}", password_hash=hash_password("x" * 12), role=role
        )
        return user, {"Authorization": f"Bearer {create_access_token(user)}"}
    finally:
        session.close()


def _persisted_event(severity="critical") -> uuid.UUID:
    session = SessionLocal()
    try:
        event = _event(severity, started_at=datetime.utcnow())
        routing.route_event(session, event)
        session.commit()
        return event.id
    finally:
        session.close()


def test_ack_endpoint_roles_and_idempotency():
    event_id = _persisted_event()
    _, viewer_h = _user("viewer")
    operator, operator_h = _user("operator")
    _, admin_h = _user("admin")
    url = f"/api/v1/anomalies/events/{event_id}/ack"

    assert client.post(url).status_code == 401
    assert client.post(url, headers=viewer_h).status_code == 403          # read-only cannot silence escalation
    first = client.post(url, headers=operator_h)
    assert first.status_code == 200
    assert first.json()["acknowledged_by"] == operator.username and first.json()["already_acknowledged"] is False
    second = client.post(url, headers=admin_h)
    assert second.json()["already_acknowledged"] is True
    assert second.json()["acknowledged_by"] == operator.username          # first one wins
    assert client.post(f"/api/v1/anomalies/events/{uuid.uuid4()}/ack", headers=admin_h).status_code == 404


def test_history_and_notifications_endpoints_show_where_an_alert_went():
    _, admin_h = _user("admin")
    critical, medium = _persisted_event("critical"), _persisted_event("medium")

    history = {row["id"]: row for row in client.get("/api/v1/anomalies/history?limit=500", headers=admin_h).json()}
    assert history[str(critical)]["routing"]["channels"] == ["dashboard", "email"]
    assert history[str(critical)]["routing"]["email_status"] in ("pending", "sent", "failed")
    assert history[str(medium)]["routing"]["channels"] == ["dashboard"]
    assert history[str(medium)]["routing"]["email_status"] is None

    detail = client.get(f"/api/v1/anomalies/events/{critical}/notifications", headers=admin_h).json()
    assert {n["channel"] for n in detail["routing"]["notifications"]} == {"dashboard", "email"}


def test_only_an_admin_can_change_who_gets_the_alerts():
    _, viewer_h = _user("viewer")
    _, operator_h = _user("operator")
    body = {"recipient_email": "someone@example.com", "enabled": True}

    assert client.put("/api/v1/settings/alert-email", headers=viewer_h, json=body).status_code == 403
    assert client.put("/api/v1/settings/alert-email", headers=operator_h, json=body).status_code == 403
    assert client.post("/api/v1/settings/alert-email/test", headers=operator_h).status_code == 403
    assert client.get("/api/v1/settings/alert-email", headers=viewer_h).status_code == 200
