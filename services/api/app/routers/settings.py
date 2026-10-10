import logging
import os
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from .. import models, schemas
from ..auth import require_admin
from ..db import get_db
from ..services import alert_email, weekly_digest

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/settings", tags=["settings"])


def _settings_out(settings) -> dict:
    return {"recipient_email": settings.recipient_email, "enabled": settings.enabled, "smtp_configured": alert_email.smtp_configured()}


@router.get("/alert-email")
def get_alert_email_settings(db: Session = Depends(get_db)):
    settings = alert_email.get_settings(db)
    db.commit()
    return _settings_out(settings)


@router.put("/alert-email")
def update_alert_email_settings(payload: schemas.AlertEmailSettingsUpdate, db: Session = Depends(get_db)):
    settings = alert_email.get_settings(db)
    settings.recipient_email, settings.enabled = payload.recipient_email, payload.enabled
    db.commit()
    db.refresh(settings)
    return _settings_out(settings)


@router.post("/alert-email/test")
def test_alert_email(db: Session = Depends(get_db)):
    try:
        alert_email.send_test_email(db)
    except Exception as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc
    return {"message": "test email sent"}


# -- Roadmap 4.5: weekly digest ------------------------------------------

def _digest_out(settings) -> dict:
    now = datetime.utcnow()
    return {
        "enabled": settings.digest_enabled,
        "weekday": settings.digest_weekday,
        "hour_utc": settings.digest_hour_utc,
        "recipient_email": settings.recipient_email,
        "smtp_configured": alert_email.smtp_configured(),
        "ai_summary_available": bool(os.environ.get("NVIDIA_API_KEY")),
        "last_sent_at": settings.digest_last_sent_at.isoformat() + "Z" if settings.digest_last_sent_at else None,
        "next_send_at": (
            weekly_digest.next_slot(now, settings.digest_weekday, settings.digest_hour_utc).isoformat() + "Z"
            if settings.digest_enabled else None
        ),
    }


@router.get("/weekly-digest")
def get_weekly_digest_settings(db: Session = Depends(get_db)):
    settings = alert_email.get_settings(db)
    db.commit()
    return _digest_out(settings)


@router.put("/weekly-digest")
def update_weekly_digest_settings(
    payload: schemas.WeeklyDigestSettingsUpdate,
    db: Session = Depends(get_db),
    _admin: models.User = Depends(require_admin),
):
    settings = alert_email.get_settings(db)
    schedule_changed = (settings.digest_weekday, settings.digest_hour_utc) != (payload.weekday, payload.hour_utc)
    settings.digest_enabled = payload.enabled
    settings.digest_weekday, settings.digest_hour_utc = payload.weekday, payload.hour_utc
    if schedule_changed or (payload.enabled and not settings.digest_last_sent_at):
        # Re-seed to the most recent slot of the *new* schedule: moving the
        # day to "yesterday" must not fire a digest immediately.
        settings.digest_last_sent_at = weekly_digest.latest_slot(datetime.utcnow(), payload.weekday, payload.hour_utc)
    db.commit()
    db.refresh(settings)
    return _digest_out(settings)


@router.post("/weekly-digest/send")
def send_weekly_digest_now(db: Session = Depends(get_db), _admin: models.User = Depends(require_admin)):
    """Builds and sends this week's digest right now (real data, real AI
    summary) without touching the schedule -- the way to verify SMTP +
    content end to end before trusting the Monday send."""
    try:
        result = weekly_digest.send_digest_now(db)
    except Exception as exc:
        logger.exception("weekly digest 'Send now' failed")
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc
    db.commit()
    return {"message": "weekly digest sent", **result}


@router.get("/weekly-digest/preview", response_class=HTMLResponse)
def preview_weekly_digest(use_ai: bool = True, db: Session = Depends(get_db)):
    """The exact HTML the email would contain, built from live data now.
    `use_ai=false` skips the NVIDIA NIM call (instant, free)."""
    try:
        _subject, _text, html, _data = weekly_digest.build_digest_email(db, use_ai=use_ai)
    except Exception as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, f"could not build digest: {exc}") from exc
    return HTMLResponse(html)
