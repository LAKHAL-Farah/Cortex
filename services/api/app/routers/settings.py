import logging
import os
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from .. import models, schemas
from ..auth import require_admin
from ..db import get_db
from ..services import alert_email, anomaly_detector, detection_settings, weekly_digest

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/settings", tags=["settings"])


def _settings_out(settings) -> dict:
    return {"recipient_email": settings.recipient_email, "enabled": settings.enabled, "smtp_configured": alert_email.smtp_configured()}


@router.get("/alert-email")
def get_alert_email_settings(db: Session = Depends(get_db)):
    settings = alert_email.get_settings(db)
    db.commit()
    return _settings_out(settings)


@router.put("/alert-email", dependencies=[Depends(require_admin)])
def update_alert_email_settings(payload: schemas.AlertEmailSettingsUpdate, db: Session = Depends(get_db)):
    settings = alert_email.get_settings(db)
    settings.recipient_email, settings.enabled = payload.recipient_email, payload.enabled
    db.commit()
    db.refresh(settings)
    return _settings_out(settings)


@router.post("/alert-email/test", dependencies=[Depends(require_admin)])
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


# -- Anomaly detection sensitivity ---------------------------------------

def _detection_out(db: Session) -> dict:
    row = detection_settings.get_row(db)
    cfg = detection_settings._config_from_row(row)
    interval = anomaly_detector.DETECTION_INTERVAL_SECONDS
    stored_custom = detection_settings.DetectionConfig(
        "custom",
        {"medium": row.z_medium, "high": row.z_high, "critical": row.z_critical},
        {"medium": row.ssh_medium, "high": row.ssh_high, "critical": row.ssh_critical},
        row.persist_minutes,
    )
    return {
        "preset": row.preset,
        "effective": {**cfg.as_dict(), "confirm_ticks": cfg.confirm_ticks(interval)},
        "presets": {name: preset.as_dict() for name, preset in detection_settings.PRESETS.items()},
        "custom": stored_custom.as_dict(),
        "limits": {
            "z": [detection_settings.Z_MIN, detection_settings.Z_MAX],
            "ssh": [detection_settings.SSH_MIN, detection_settings.SSH_MAX],
            "persist_minutes": [0, detection_settings.PERSIST_MAX_MINUTES],
        },
        "detection_interval_seconds": interval,
        "updated_at": row.updated_at.isoformat() + "Z" if row.updated_at else None,
        "updated_by": row.updated_by,
    }


@router.get("/anomaly-detection")
def get_detection_settings(db: Session = Depends(get_db)):
    """The current sensitivity, the presets, and the limits for custom values.
    Readable by any signed-in account (the alert meters use the thresholds)."""
    out = _detection_out(db)
    db.commit()
    return out


def _config_from_update(payload: schemas.DetectionSettingsUpdate) -> detection_settings.DetectionConfig:
    try:
        if payload.preset != "custom":
            return detection_settings.PRESETS[payload.preset]
        if payload.custom is None:
            raise detection_settings.InvalidSettings("Custom sensitivity needs its thresholds.")
        return detection_settings.validate_custom(
            payload.custom.z, payload.custom.ssh, payload.custom.persist_minutes
        )
    except detection_settings.InvalidSettings as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc


@router.put("/anomaly-detection")
def update_detection_settings(
    payload: schemas.DetectionSettingsUpdate,
    db: Session = Depends(get_db),
    admin: models.User = Depends(require_admin),
):
    """Admin-only: changes what counts as an alert for the whole installation."""
    _config_from_update(payload)  # validate first -> clean 422, nothing written
    detection_settings.save(
        db,
        preset=payload.preset,
        custom=payload.custom.model_dump() if payload.custom else None,
        username=admin.username,
    )
    return _detection_out(db)


@router.post("/anomaly-detection/preview", dependencies=[Depends(require_admin)])
def preview_detection_settings(payload: schemas.DetectionSettingsUpdate, db: Session = Depends(get_db)):
    """Nothing is saved: replays the last 7 days of alerts against these
    settings so the admin can see the effect before choosing."""
    cfg = _config_from_update(payload)
    return detection_settings.preview(db, cfg, days=7, interval_seconds=anomaly_detector.DETECTION_INTERVAL_SECONDS)
