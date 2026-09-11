from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from .. import schemas
from ..db import get_db
from ..services import alert_email

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
