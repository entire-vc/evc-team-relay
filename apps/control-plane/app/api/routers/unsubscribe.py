"""One-click unsubscribe for lifecycle nudges (RFC 8058 compatible GET).

Sets user_email_preferences.lifecycle_emails = false for the given user.
Token is HMAC-SHA256(user_id, LIFECYCLE_UNSUBSCRIBE_SECRET) to prevent forgery.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.models import UserEmailPreferences
from app.db.session import get_db
from app.services.lifecycle_service import verify_unsubscribe_token

router = APIRouter(prefix="/api/v1/public", tags=["unsubscribe"])


@router.get("/unsubscribe/lifecycle", response_class=HTMLResponse, include_in_schema=False)
def unsubscribe_lifecycle(
    user: str = Query(...),
    token: str = Query(...),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    """Record lifecycle email opt-out and show confirmation page."""
    settings = get_settings()
    try:
        user_id = uuid.UUID(user)
    except ValueError:
        raise HTTPException(status_code=400, detail="invalid request")

    if not verify_unsubscribe_token(user_id, token, settings.lifecycle_unsubscribe_secret):
        raise HTTPException(status_code=400, detail="invalid request")

    prefs = db.execute(
        select(UserEmailPreferences).where(UserEmailPreferences.user_id == user_id)
    ).scalar_one_or_none()

    if prefs is None:
        prefs = UserEmailPreferences(user_id=user_id, lifecycle_emails=False)
        db.add(prefs)
    else:
        prefs.lifecycle_emails = False
    db.commit()

    return HTMLResponse(
        content=(
            "<html><body style='font-family:sans-serif;max-width:480px;margin:60px auto;'>"
            "<h2>Отписка оформлена</h2>"
            "<p>Вы отписались от советов по настройке Team Relay. "
            "Управлять настройками уведомлений можно в профиле.</p>"
            "</body></html>"
        )
    )
