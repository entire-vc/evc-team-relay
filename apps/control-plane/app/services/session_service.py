"""Session management service for refresh tokens."""

from __future__ import annotations

import hashlib
import secrets
import uuid
from datetime import timedelta

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.core.security import utcnow
from app.db import models


def _hash_refresh_token(token: str) -> str:
    """Hash refresh token using SHA-256."""
    return hashlib.sha256(token.encode()).hexdigest()


def create_session(
    db: Session,
    user_id: uuid.UUID,
    device_name: str | None = None,
    user_agent: str | None = None,
    ip_address: str | None = None,
    expires_days: int = 30,
) -> tuple[models.UserSession, str]:
    """Create a new user session with refresh token.

    Args:
        db: Database session
        user_id: User ID
        device_name: Device name (optional)
        user_agent: User agent string (optional)
        ip_address: IP address (optional)
        expires_days: Token expiration in days (default 30)

    Returns:
        Tuple of (UserSession object, refresh_token string)
    """
    # Generate cryptographically secure refresh token (256 bits)
    refresh_token = secrets.token_hex(32)  # 64 hex characters
    token_hash = _hash_refresh_token(refresh_token)

    now = utcnow()
    expires_at = now + timedelta(days=expires_days)

    session = models.UserSession(
        id=uuid.uuid4(),
        user_id=user_id,
        refresh_token_hash=token_hash,
        device_name=device_name,
        user_agent=user_agent,
        ip_address=ip_address,
        last_activity=now,
        expires_at=expires_at,
        created_at=now,
    )

    db.add(session)
    db.flush()

    return session, refresh_token


def validate_refresh_token(db: Session, refresh_token: str) -> models.UserSession | None:
    """Validate refresh token and return session if valid.

    Args:
        db: Database session
        refresh_token: Refresh token string

    Returns:
        UserSession object if valid, None otherwise
    """
    token_hash = _hash_refresh_token(refresh_token)
    now = utcnow()

    stmt = select(models.UserSession).where(
        models.UserSession.refresh_token_hash == token_hash,
        models.UserSession.expires_at > now,
    )

    session = db.execute(stmt).scalar_one_or_none()
    return session


def rotate_refresh_token(db: Session, session_id: uuid.UUID) -> tuple[models.UserSession, str]:
    """Rotate refresh token (single-use pattern).

    Args:
        db: Database session
        session_id: Session ID to rotate

    Returns:
        Tuple of (Updated UserSession object, new refresh_token string)
    """
    # Get existing session
    stmt = select(models.UserSession).where(models.UserSession.id == session_id)
    session = db.execute(stmt).scalar_one_or_none()

    if not session:
        raise ValueError(f"Session {session_id} not found")

    # Generate new refresh token
    new_refresh_token = secrets.token_hex(32)
    new_token_hash = _hash_refresh_token(new_refresh_token)

    # Update session
    session.refresh_token_hash = new_token_hash
    session.last_activity = utcnow()

    db.flush()

    return session, new_refresh_token


def revoke_session(db: Session, session_id: uuid.UUID) -> bool:
    """Revoke (delete) a session.

    Args:
        db: Database session
        session_id: Session ID to revoke

    Returns:
        True if session was deleted, False if not found
    """
    stmt = delete(models.UserSession).where(models.UserSession.id == session_id)
    result = db.execute(stmt)
    db.flush()
    return result.rowcount > 0


def get_user_sessions(db: Session, user_id: uuid.UUID) -> list[models.UserSession]:
    """Get all active sessions for a user.

    Args:
        db: Database session
        user_id: User ID

    Returns:
        List of active UserSession objects
    """
    now = utcnow()
    stmt = (
        select(models.UserSession)
        .where(
            models.UserSession.user_id == user_id,
            models.UserSession.expires_at > now,
        )
        .order_by(models.UserSession.last_activity.desc())
    )

    result = db.execute(stmt)
    return list(result.scalars().all())


def cleanup_expired_sessions(db: Session) -> int:
    """Delete all expired sessions.

    Args:
        db: Database session

    Returns:
        Number of sessions deleted
    """
    now = utcnow()
    stmt = delete(models.UserSession).where(models.UserSession.expires_at <= now)
    result = db.execute(stmt)
    db.flush()
    return result.rowcount


def get_session_by_id(db: Session, session_id: uuid.UUID) -> models.UserSession | None:
    """Get session by ID.

    Args:
        db: Database session
        session_id: Session ID

    Returns:
        UserSession object if found, None otherwise
    """
    stmt = select(models.UserSession).where(models.UserSession.id == session_id)
    session = db.execute(stmt).scalar_one_or_none()
    return session


def revoke_all_user_sessions(
    db: Session, user_id: uuid.UUID, except_session_id: uuid.UUID | None = None
) -> int:
    """Revoke all sessions for a user.

    Args:
        db: Database session
        user_id: User ID
        except_session_id: Optional session ID to keep (not revoke)

    Returns:
        Number of sessions revoked
    """
    stmt = delete(models.UserSession).where(models.UserSession.user_id == user_id)

    if except_session_id:
        stmt = stmt.where(models.UserSession.id != except_session_id)

    result = db.execute(stmt)
    db.flush()
    return result.rowcount
