"""SMTP delivery for alert emails, plus the settings row.

*Which* alerts get emailed, when, and the retry/escalation behaviour live in
services/alert_routing.py (roadmap 4.7); this module is the transport and the
message body.
"""
import logging
import os
import smtplib
from email.message import EmailMessage

from sqlalchemy.orm import Session

from .. import models

logger = logging.getLogger(__name__)
DEFAULT_RECIPIENT = os.getenv("ALERT_DEFAULT_RECIPIENT_EMAIL", "yosratig0@gmail.com")


def get_settings(db: Session) -> models.AlertEmailSettings:
    settings = db.get(models.AlertEmailSettings, 1)
    if settings is None:
        settings = models.AlertEmailSettings(id=1, recipient_email=DEFAULT_RECIPIENT, enabled=True)
        db.add(settings)
        db.flush()
    return settings


def smtp_configured() -> bool:
    return bool(os.getenv("SMTP_HOST") and os.getenv("SMTP_FROM"))


def send_email(recipient: str, subject: str, text: str, html: str | None = None) -> None:
    """Send a plain-text email, or a multipart/alternative one when `html` is
    given (text part first so clients that can't render HTML still get a
    readable message). Raises on any SMTP problem -- callers decide whether
    delivery is best-effort (alerts) or must be reported (digest 'Send now')."""
    host, sender = os.getenv("SMTP_HOST"), os.getenv("SMTP_FROM")
    if not host or not sender:
        raise RuntimeError("SMTP is not configured (SMTP_HOST and SMTP_FROM are required)")
    message = EmailMessage()
    message["From"], message["To"], message["Subject"] = sender, recipient, subject
    message.set_content(text)
    if html:
        message.add_alternative(html, subtype="html")
    with smtplib.SMTP(host, int(os.getenv("SMTP_PORT", "587")), timeout=15) as smtp:
        if os.getenv("SMTP_STARTTLS", "true").lower() == "true":
            smtp.starttls()
        if os.getenv("SMTP_USERNAME") and os.getenv("SMTP_PASSWORD"):
            smtp.login(os.environ["SMTP_USERNAME"], os.environ["SMTP_PASSWORD"])
        smtp.send_message(message)


def _send(recipient: str, subject: str, body: str) -> None:
    send_email(recipient, subject, body)


def _event_body(event: models.AnomalyEvent) -> str:
    """Build a useful, plain-text notification without exposing SMTP secrets."""
    lines = [
        "A new Cortex alert requires attention.",
        f"Host/service: {event.hostname}",
        f"Metric: {event.metric_name}",
        f"Severity: {event.severity.upper()}",
        f"Current value: {event.current_value}",
        f"Detection method: {event.method}",
        f"Detected at (UTC): {event.started_at.isoformat()}",
    ]
    if event.z_score is not None:
        lines.append(f"Anomaly score (z-score): {event.z_score:.2f}")
    if event.baseline_n is not None:
        lines.append(f"Baseline samples: {event.baseline_n}")
    if event.details:
        lines.extend(["", "Additional details:", str(event.details)])
    return "\n".join(lines)


def send_test_email(db: Session) -> None:
    settings = get_settings(db)
    # Same shape as a real critical alert, without adding synthetic state to
    # anomaly_flags/anomaly_events or polluting the operator's history.
    _send(settings.recipient_email, "[Cortex] CRITICAL alert: test-node", "\n".join([
        "A new Cortex alert requires attention.",
        "Host/service: test-node",
        "Metric: CPU usage",
        "Severity: CRITICAL",
        "Detected at: test notification",
        "",
        "This is a test notification. No alert was added to Cortex.",
    ]))


def notify_new_anomaly(db: Session, event: models.AnomalyEvent) -> None:
    """Kept for existing callers: routes the alert (dashboard, plus email for
    high/critical) via services/alert_routing.py. Nothing is sent from here --
    the routing layer queues it and its dispatcher delivers; see that module."""
    from . import alert_routing  # local: alert_routing imports this module

    alert_routing.route_event(db, event)
