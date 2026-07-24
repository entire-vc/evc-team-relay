from __future__ import annotations

import hashlib
import secrets
import uuid
from datetime import timedelta

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core import security
from app.core.config import get_settings
from app.db import models
from app.schemas import auth as auth_schema
from app.schemas import user as user_schema
from app.services import audit_service, session_service, user_service

ADMIN_2FA_PENDING_TOKEN_EXPIRE_MINUTES = 5


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


def _hash_admin_2fa_pending_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def create_admin_2fa_pending_token(db: Session, user_id: uuid.UUID) -> str:
    """Create the second-factor handle for the admin-ui login flow (TR-06, #fceefc4f).

    Password verified, TOTP still required. Deliberately a separate DB-backed
    single-use token (same shape as PasswordResetToken/EmailVerificationToken)
    rather than a JWT, so it can never be confused with — or replayed as — a
    real admin_token session cookie even if a caller forgets to check its
    purpose. See AdminLoginPendingToken's docstring for the full rationale.
    """
    raw_token = secrets.token_hex(32)
    token_hash = _hash_admin_2fa_pending_token(raw_token)
    now = security.utcnow()
    record = models.AdminLoginPendingToken(
        id=uuid.uuid4(),
        user_id=user_id,
        token_hash=token_hash,
        expires_at=now + timedelta(minutes=ADMIN_2FA_PENDING_TOKEN_EXPIRE_MINUTES),
        created_at=now,
    )
    db.add(record)
    db.commit()
    return raw_token


def validate_admin_2fa_pending_token(db: Session, raw_token: str) -> models.User | None:
    """Validate + single-use-consume a pending-2FA token, returning its user.

    Returns None (never raises) for any invalid/expired/already-used/missing
    token — callers should treat that as "restart the login flow", not
    surface the specific reason (avoids leaking token-guessing feedback).
    """
    if not raw_token:
        return None
    token_hash = _hash_admin_2fa_pending_token(raw_token)
    now = security.utcnow()
    stmt = select(models.AdminLoginPendingToken).where(
        models.AdminLoginPendingToken.token_hash == token_hash,
        models.AdminLoginPendingToken.expires_at > now,
        models.AdminLoginPendingToken.used_at.is_(None),
    )
    record = db.execute(stmt).scalar_one_or_none()
    if not record:
        return None

    user = db.get(models.User, record.user_id)
    if not user or not user.is_active:
        return None
    return user


def mark_admin_2fa_pending_token_used(db: Session, raw_token: str) -> None:
    """Mark a pending-2FA token consumed so it can't be replayed (TR-06)."""
    token_hash = _hash_admin_2fa_pending_token(raw_token)
    stmt = select(models.AdminLoginPendingToken).where(
        models.AdminLoginPendingToken.token_hash == token_hash,
    )
    record = db.execute(stmt).scalar_one_or_none()
    if record and record.used_at is None:
        record.used_at = security.utcnow()
        db.commit()


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
