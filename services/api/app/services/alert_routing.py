"""Severity-based alert routing with basic escalation (roadmap 4.7, adr-0017).

The rule, in one table:

| severity        | dashboard | email                    | escalation                          |
|-----------------|-----------|--------------------------|-------------------------------------|
| medium / low    | yes       | no                       | none -- dashboard only              |
| high            | yes       | yes                      | after 60 min unacknowledged         |
| critical        | yes       | yes, as fast as possible | after 15 min unacknowledged         |

Every number and the set of emailed severities is an environment variable
(`load_policy`), so a deployment tunes it without a code change.

How it works
------------
1. The detectors (services/anomaly_detector.py, services/prometheus_health.py)
   open an `AnomalyEvent` and call `route_event`. That **only writes rows**
   into `alert_notifications`: a `dashboard` row (the alert *is* the
   delivery, so it is born `delivered`) and, for an emailed severity, a
   `pending` `email` row. No SMTP happens inside the detection pass -- a slow
   or dead mail server must never stall anomaly detection, and a failed send
   must not be lost.
2. A dispatcher (`run_cycle`, started from main.py every
   `ALERT_DISPATCH_INTERVAL_SECONDS`, default 10 s) sends the due `pending`
   rows. A failure is retried with exponential backoff up to
   `ALERT_EMAIL_MAX_ATTEMPTS` and then marked `failed` with the error kept;
   `sent_at - created_at` on the row is the alert-to-inbox latency, so the
   "critical reaches email within 1 minute" criterion is a query, not a hope.
3. Escalation (`escalate_unacknowledged`, same cycle): an alert that is still
   open and not acknowledged after its severity's grace period gets one
   `escalation` email, to `ALERT_ESCALATION_RECIPIENT_EMAIL` (falling back to
   the normal recipient). Acknowledging (`acknowledge`) or resolving the alert
   stops it. A severity that ratchets up restarts its own clock: the
   escalation timer for "critical" runs from when the alert *became*
   critical, not from when it first opened as medium.

Idempotency: one row per (episode, channel, kind, severity), enforced by a
unique constraint and checked before inserting, so a detector tick that
re-reports the same severity, or two API workers, send nothing twice.

Failure policy: routing must never break detection. `route_event` swallows and
logs its own errors (inside a SAVEPOINT, so a failed notification insert
cannot take the detector's own writes down with it).
"""
import logging
import os
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Callable, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import models
from . import alert_email

logger = logging.getLogger(__name__)

DASHBOARD = "dashboard"
EMAIL = "email"

INITIAL = "initial"
UPGRADE = "severity_upgrade"
ESCALATION = "escalation"

MAX_ERROR_LENGTH = 500
MAX_BACKOFF_SECONDS = 300


# --------------------------------------------------------------------
# Policy (pure, read from the environment at call time so tests and
# operators can change it without a restart-time import)
# --------------------------------------------------------------------

@dataclass(frozen=True)
class RoutingPolicy:
    email_severities: frozenset
    # severity -> how long an open, unacknowledged alert may sit before it is
    # escalated. A severity that is absent here never escalates.
    escalate_after: dict
    escalation_recipient: Optional[str]
    max_attempts: int
    retry_base_seconds: int


def _csv_env(name: str, default: str) -> frozenset:
    return frozenset(part.strip().lower() for part in os.getenv(name, default).split(",") if part.strip())


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        logger.warning("%s is not an integer; using %s", name, default)
        return default


def load_policy() -> RoutingPolicy:
    escalate_after = {}
    for severity, default_minutes in (("critical", 15), ("high", 60)):
        minutes = _int_env(f"ALERT_ESCALATE_{severity.upper()}_AFTER_MINUTES", default_minutes)
        if minutes > 0:  # 0 (or negative) switches that severity's escalation off
            escalate_after[severity] = timedelta(minutes=minutes)
    return RoutingPolicy(
        email_severities=_csv_env("ALERT_EMAIL_SEVERITIES", "high,critical"),
        escalate_after=escalate_after,
        escalation_recipient=(os.getenv("ALERT_ESCALATION_RECIPIENT_EMAIL") or "").strip() or None,
        max_attempts=max(1, _int_env("ALERT_EMAIL_MAX_ATTEMPTS", 6)),
        retry_base_seconds=max(1, _int_env("ALERT_EMAIL_RETRY_SECONDS", 10)),
    )


def channels_for(severity: str, policy: Optional[RoutingPolicy] = None) -> tuple[str, ...]:
    """Where an alert of this severity goes. The dashboard always shows it."""
    policy = policy or load_policy()
    return (DASHBOARD, EMAIL) if (severity or "").lower() in policy.email_severities else (DASHBOARD,)


# --------------------------------------------------------------------
# Routing: write the rows
# --------------------------------------------------------------------

def _existing_routes(db: Session, event_id: uuid.UUID) -> set[tuple[str, str, str]]:
    rows = db.execute(
        select(models.AlertNotification.channel, models.AlertNotification.kind, models.AlertNotification.severity)
        .where(models.AlertNotification.event_id == event_id)
    ).all()
    return {(r.channel, r.kind, r.severity) for r in rows}


def _email_row(
    db: Session, *, event_id: uuid.UUID, kind: str, severity: str, now: datetime, recipient: Optional[str] = None
) -> models.AlertNotification:
    """An `email` outbox row: `pending` when email alerts are switched on,
    `skipped` (with the reason) when an admin turned them off in settings."""
    settings = alert_email.get_settings(db)
    if settings.enabled:
        return models.AlertNotification(
            event_id=event_id, channel=EMAIL, kind=kind, severity=severity,
            recipient=recipient or settings.recipient_email,
            status="pending", attempts=0, next_attempt_at=now, created_at=now,
        )
    return models.AlertNotification(
        event_id=event_id, channel=EMAIL, kind=kind, severity=severity,
        recipient=recipient or settings.recipient_email,
        status="skipped", attempts=0, last_error="email alerts are switched off in settings", created_at=now,
    )


def route_event(
    db: Session,
    event: models.AnomalyEvent,
    *,
    now: Optional[datetime] = None,
    policy: Optional[RoutingPolicy] = None,
) -> list[models.AlertNotification]:
    """Decide where `event` goes *at its current severity* and record it.

    Call it when an episode opens and again whenever its severity ratchets up;
    calling it twice for the same (episode, severity) is a no-op. Adds rows to
    the caller's session (the detector commits them together with the event).
    Never raises: a routing failure is logged and the alert still shows on the
    dashboard.
    """
    now = now or datetime.utcnow()
    policy = policy or load_policy()
    created: list[models.AlertNotification] = []
    try:
        if event.id is None:
            event.id = uuid.uuid4()
        db.add(event)
        # The event must exist before notifications can point at it. Outside
        # the SAVEPOINT on purpose: if *this* fails it is the detector's own
        # write that is broken, and it would have failed at commit anyway.
        db.flush()

        with db.begin_nested():
            existing = _existing_routes(db, event.id)
            severity = event.severity
            kind = UPGRADE if any(k in (INITIAL, UPGRADE) for _, k, _ in existing) else INITIAL

            if (DASHBOARD, INITIAL, severity) not in existing and (DASHBOARD, UPGRADE, severity) not in existing:
                created.append(models.AlertNotification(
                    event_id=event.id, channel=DASHBOARD, kind=kind, severity=severity,
                    status="delivered", attempts=0, created_at=now, sent_at=now,
                ))

            wants_email = EMAIL in channels_for(severity, policy)
            already_emailed = (EMAIL, INITIAL, severity) in existing or (EMAIL, UPGRADE, severity) in existing
            if wants_email and not already_emailed:
                created.append(_email_row(db, event_id=event.id, kind=kind, severity=severity, now=now))

            db.add_all(created)
            db.flush()
    except Exception:  # noqa: BLE001 -- monitoring must not fail because routing did
        logger.exception("alert routing failed for %s/%s", event.hostname, event.metric_name)
        return []
    return created


# --------------------------------------------------------------------
# Escalation: one reminder per severity for an alert nobody picked up
# --------------------------------------------------------------------

def escalate_unacknowledged(
    db: Session, *, now: Optional[datetime] = None, policy: Optional[RoutingPolicy] = None
) -> int:
    """Queue an `escalation` email for every open, unacknowledged alert that has
    sat at its current severity longer than that severity's grace period.
    Returns how many were queued. Does not send; `dispatch_due` does."""
    now = now or datetime.utcnow()
    policy = policy or load_policy()
    queued = 0
    for severity, grace in policy.escalate_after.items():
        due = db.execute(
            select(models.AnomalyEvent, models.AlertNotification.created_at)
            .join(models.AlertNotification, models.AlertNotification.event_id == models.AnomalyEvent.id)
            .where(
                models.AlertNotification.channel == DASHBOARD,
                models.AlertNotification.severity == severity,
                models.AnomalyEvent.severity == severity,     # still at this level
                models.AnomalyEvent.resolved_at.is_(None),
                models.AnomalyEvent.acknowledged_at.is_(None),
                models.AlertNotification.created_at <= now - grace,
            )
        ).all()
        for event, _became_at in due:
            if (EMAIL, ESCALATION, severity) in _existing_routes(db, event.id):
                continue
            try:
                with db.begin_nested():
                    row = _email_row(
                        db, event_id=event.id, kind=ESCALATION, severity=severity, now=now,
                        recipient=policy.escalation_recipient,
                    )
                    db.add(row)
                    db.flush()
                queued += 1
            except Exception:  # noqa: BLE001
                logger.exception("could not queue escalation for %s/%s", event.hostname, event.metric_name)
    if queued:
        db.commit()
    return queued


def acknowledge(
    db: Session, event: models.AnomalyEvent, *, username: str, now: Optional[datetime] = None
) -> bool:
    """Mark an alert as picked up. Idempotent: the first acknowledgement wins and
    is never overwritten. Returns True when this call was the one that set it."""
    if event.acknowledged_at is not None:
        return False
    event.acknowledged_at = now or datetime.utcnow()
    event.acknowledged_by = username
    db.commit()
    return True


# --------------------------------------------------------------------
# Delivery: the outbox dispatcher
# --------------------------------------------------------------------

def build_message(event: models.AnomalyEvent, notification: models.AlertNotification, policy: RoutingPolicy) -> tuple[str, str]:
    severity = (notification.severity or event.severity).upper()
    body = alert_email._event_body(event)
    if notification.kind == ESCALATION:
        grace = policy.escalate_after.get(notification.severity)
        minutes = int(grace.total_seconds() // 60) if grace else None
        waited = f"for at least {minutes} minutes" if minutes else "for a while"
        subject = f"[Cortex] ESCALATION: {severity} alert on {event.hostname} not acknowledged"
        preface = (
            f"This {severity} alert has been open and unacknowledged {waited}.\n"
            "Acknowledge it in Cortex (Alerts) to stop further escalation.\n\n"
        )
        return subject, preface + body
    if notification.kind == UPGRADE:
        return (
            f"[Cortex] {severity} alert: {event.hostname} (severity raised)",
            f"This alert got worse and is now {severity}.\n\n" + body,
        )
    return f"[Cortex] {severity} alert: {event.hostname}", body


def _backoff(attempts: int, base_seconds: int) -> timedelta:
    return timedelta(seconds=min(MAX_BACKOFF_SECONDS, base_seconds * (2 ** max(0, attempts - 1))))


def dispatch_due(
    db: Session,
    *,
    sender: Optional[Callable[[str, str, str], None]] = None,
    clock: Callable[[], datetime] = datetime.utcnow,
    policy: Optional[RoutingPolicy] = None,
    batch: int = 20,
) -> dict:
    """Send the `pending` email rows that are due, one commit per row.

    `sender(recipient, subject, body)` defaults to the SMTP sender. Without one
    configured (no SMTP_HOST / SMTP_FROM) rows are marked `failed` straight
    away -- retrying cannot help until someone configures SMTP, and a visible
    `failed` row on the alert is the honest outcome (the dashboard still has it).
    """
    policy = policy or load_policy()
    summary = {"sent": 0, "failed": 0, "retrying": 0}
    if sender is None:
        smtp_ready = alert_email.smtp_configured()
        sender = alert_email.send_email if smtp_ready else None
    else:
        smtp_ready = True

    due_ids = list(db.execute(
        select(models.AlertNotification.id)
        .where(
            models.AlertNotification.channel == EMAIL,
            models.AlertNotification.status == "pending",
            models.AlertNotification.next_attempt_at <= clock(),
        )
        .order_by(models.AlertNotification.created_at)
        .limit(batch)
    ).scalars())

    for notification_id in due_ids:
        # Re-read under a row lock that skips rows another worker holds, so two
        # API processes never send the same email.
        row = db.execute(
            select(models.AlertNotification)
            .where(models.AlertNotification.id == notification_id, models.AlertNotification.status == "pending")
            .with_for_update(skip_locked=True)
            .execution_options(populate_existing=True)
        ).scalar_one_or_none()
        if row is None:
            db.rollback()
            continue
        event = db.get(models.AnomalyEvent, row.event_id)
        if event is None:  # cannot happen (FK), but never let one row wedge the queue
            row.status, row.last_error = "failed", "alert no longer exists"
            db.commit()
            summary["failed"] += 1
            continue
        if not smtp_ready or sender is None:
            row.status, row.attempts = "failed", row.attempts + 1
            row.last_error = "SMTP is not configured (SMTP_HOST and SMTP_FROM are required); the alert is on the dashboard only"
            db.commit()
            summary["failed"] += 1
            continue

        subject, body = build_message(event, row, policy)
        try:
            sender(row.recipient, subject, body)
        except Exception as exc:  # noqa: BLE001
            row.attempts += 1
            row.last_error = f"{type(exc).__name__}: {exc}"[:MAX_ERROR_LENGTH]
            if row.attempts >= policy.max_attempts:
                row.status = "failed"
                summary["failed"] += 1
                logger.error("alert email for %s gave up after %s attempts: %s", event.hostname, row.attempts, row.last_error)
            else:
                row.next_attempt_at = clock() + _backoff(row.attempts, policy.retry_base_seconds)
                summary["retrying"] += 1
                logger.warning("alert email for %s failed (attempt %s), will retry: %s", event.hostname, row.attempts, row.last_error)
        else:
            row.status, row.sent_at, row.last_error = "sent", clock(), None
            row.attempts += 1
            summary["sent"] += 1
        db.commit()
    return summary


def run_cycle(db: Session) -> dict:
    """One dispatcher tick: queue any escalations that came due, then send."""
    queued = 0
    try:
        queued = escalate_unacknowledged(db)
    except Exception:  # noqa: BLE001 -- escalation trouble must not stop plain delivery
        db.rollback()
        logger.exception("alert escalation pass failed")
    summary = dispatch_due(db)
    summary["escalations_queued"] = queued
    return summary


# --------------------------------------------------------------------
# Reading it back (the dashboard's view of "where did this alert go")
# --------------------------------------------------------------------

def _iso(moment: Optional[datetime]) -> Optional[str]:
    from datetime import timezone

    return moment.replace(tzinfo=timezone.utc).isoformat() if moment else None


def notification_out(row: models.AlertNotification) -> dict:
    latency = (row.sent_at - row.created_at).total_seconds() if row.sent_at and row.created_at else None
    return {
        "id": str(row.id),
        "channel": row.channel,
        "kind": row.kind,
        "severity": row.severity,
        "recipient": row.recipient,
        "status": row.status,
        "attempts": row.attempts,
        "last_error": row.last_error,
        "created_at": _iso(row.created_at),
        "sent_at": _iso(row.sent_at),
        "latency_seconds": latency,
    }


def notifications_for(db: Session, event_ids: list[uuid.UUID]) -> dict[uuid.UUID, list[models.AlertNotification]]:
    grouped: dict[uuid.UUID, list[models.AlertNotification]] = {}
    if not event_ids:
        return grouped
    rows = db.execute(
        select(models.AlertNotification)
        .where(models.AlertNotification.event_id.in_(event_ids))
        .order_by(models.AlertNotification.created_at, models.AlertNotification.channel)
    ).scalars()
    for row in rows:
        grouped.setdefault(row.event_id, []).append(row)
    return grouped


def routing_summary(rows: Optional[list[models.AlertNotification]]) -> Optional[dict]:
    """The one-glance answer for an alert: which channels, and did the email land.
    None for an alert from before routing existed (nothing was recorded)."""
    if not rows:
        return None
    emails = [r for r in rows if r.channel == EMAIL and r.kind != ESCALATION]
    escalations = [r for r in rows if r.kind == ESCALATION]
    latest_email = emails[-1] if emails else None
    return {
        "channels": [DASHBOARD] + ([EMAIL] if any(r.channel == EMAIL for r in rows) else []),
        "email_status": latest_email.status if latest_email else None,
        "email_sent_at": _iso(latest_email.sent_at) if latest_email else None,
        "escalated": bool(escalations),
        "escalation_status": escalations[-1].status if escalations else None,
        "notifications": [notification_out(r) for r in rows],
    }
