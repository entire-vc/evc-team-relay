from __future__ import annotations

import uuid

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core import security
from app.core.config import get_settings
from app.db import models
from app.schemas import auth as auth_schema
from app.schemas import user as user_schema
from app.services import audit_service, session_service, user_service


def authenticate_user(db: Session, email: str, password: str) -> models.User:
    user = user_service.get_user_by_email(db, email)
    if not user or not security.verify_password(password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Incorrect email or password"
        )
    if not user.is_active:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="User is inactive")
    return user


def login(
    db: Session,
    payload: auth_schema.LoginRequest,
    device_name: str | None = None,
    user_agent: str | None = None,
    ip_address: str | None = None,
) -> auth_schema.TokenResponse:
    """Authenticate user and create session with refresh token.

    Args:
        db: Database session
        payload: Login credentials
        device_name: Device name (optional)
        user_agent: User agent string (optional)
        ip_address: IP address (optional)

    Returns:
        TokenResponse with access_token and refresh_token
    """
    settings = get_settings()
    user = authenticate_user(db, payload.email, payload.password)

    # Create session with refresh token
    session, refresh_token = session_service.create_session(
        db=db,
        user_id=user.id,
        device_name=device_name,
        user_agent=user_agent,
        ip_address=ip_address,
        expires_days=settings.refresh_token_expire_days,
    )

    # Create access token with session_id
    access_token = security.create_access_token(str(user.id), session_id=str(session.id))

    return auth_schema.TokenResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        expires_in=settings.access_token_expire_minutes * 60,
    )


def register_user(
    db: Session, payload: auth_schema.RegisterRequest, actor_user_id: uuid.UUID | None = None
) -> models.User:
    user_payload = user_schema.UserCreate(
        email=payload.email,
        password=payload.password,
        is_admin=payload.is_admin,
        is_active=True,
    )
    return user_service.create_user(db, user_payload, actor_user_id=actor_user_id)


def bootstrap_admin_if_needed(db: Session) -> None:
    settings = get_settings()
    stmt = select(models.User.id)
    has_user = db.execute(stmt.limit(1)).first()
    if has_user:
        return
    if not settings.bootstrap_admin_email or not settings.bootstrap_admin_password:
        return
    bootstrap_payload = user_schema.UserCreate(
        email=settings.bootstrap_admin_email,
        password=settings.bootstrap_admin_password,
        is_admin=True,
        is_active=True,
    )
    user_service.create_user(db, bootstrap_payload)


def create_access_token(user_id: uuid.UUID) -> str:
    """Create JWT access token for user."""
    return security.create_access_token(str(user_id))


def log_login(
    db: Session, user: models.User, ip_address: str | None, user_agent: str | None
) -> None:
    """Log user login event."""
    audit_service.log_action(
        db=db,
        action=models.AuditAction.USER_LOGIN,
        actor_user_id=user.id,
        ip_address=ip_address,
        user_agent=user_agent,
    )
    db.commit()


def log_logout(
    db: Session, user: models.User, ip_address: str | None, user_agent: str | None
) -> None:
    """Log user logout event."""
    audit_service.log_action(
        db=db,
        action=models.AuditAction.USER_LOGOUT,
        actor_user_id=user.id,
        ip_address=ip_address,
        user_agent=user_agent,
    )
    db.commit()
