"""Password reset service for handling password recovery."""

from __future__ import annotations

import hashlib
import secrets
import uuid
from datetime import timedelta

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.core.security import get_password_hash, utcnow
from app.db import models


def _hash_reset_token(token: str) -> str:
    """Hash reset token using SHA-256."""
    return hashlib.sha256(token.encode()).hexdigest()


def create_reset_token(db: Session, email: str, expires_hours: int = 1) -> str | None:
    """Create password reset token for user.

    Args:
        db: Database session
        email: User email address
        expires_hours: Token expiration in hours (default 1)

    Returns:
        Plain reset token string if user found, None otherwise
    """
    # Find user by email
    stmt = select(models.User).where(models.User.email == email)
    user = db.execute(stmt).scalar_one_or_none()

    if not user:
        return None

    # Generate secure token (32 bytes = 64 hex chars)
    reset_token = secrets.token_hex(32)
    token_hash = _hash_reset_token(reset_token)

    now = utcnow()
    expires_at = now + timedelta(hours=expires_hours)

    # Create reset token record
    reset_token_record = models.PasswordResetToken(
        id=uuid.uuid4(),
        user_id=user.id,
        token_hash=token_hash,
        expires_at=expires_at,
        created_at=now,
    )

    db.add(reset_token_record)
    db.flush()

    return reset_token


def validate_reset_token(db: Session, token: str) -> models.User | None:
    """Validate reset token and return user.

    Args:
        db: Database session
        token: Reset token string

    Returns:
        User object if token valid, None if invalid/expired
    """
    token_hash = _hash_reset_token(token)
    now = utcnow()

    # Find matching token that hasn't expired or been used
    stmt = select(models.PasswordResetToken).where(
        models.PasswordResetToken.token_hash == token_hash,
        models.PasswordResetToken.expires_at > now,
        models.PasswordResetToken.used_at.is_(None),
    )

    reset_token_record = db.execute(stmt).scalar_one_or_none()

    if not reset_token_record:
        return None

    # Get associated user
    stmt = select(models.User).where(models.User.id == reset_token_record.user_id)
    user = db.execute(stmt).scalar_one_or_none()

    return user


def complete_reset(db: Session, token: str, new_password: str) -> bool:
    """Complete password reset.

    Args:
        db: Database session
        token: Reset token string
        new_password: New password to set

    Returns:
        True if successful, False if token invalid
    """
    # Validate token
    user = validate_reset_token(db, token)
    if not user:
        return False

    # Update user's password
    user.password_hash = get_password_hash(new_password)

    # Mark token as used
    token_hash = _hash_reset_token(token)
    stmt = select(models.PasswordResetToken).where(
        models.PasswordResetToken.token_hash == token_hash
    )
    reset_token_record = db.execute(stmt).scalar_one_or_none()

    if reset_token_record:
        reset_token_record.used_at = utcnow()

    # Revoke all user sessions (force re-login)
    stmt = delete(models.UserSession).where(models.UserSession.user_id == user.id)
    db.execute(stmt)

    db.flush()

    return True


def cleanup_expired_tokens(db: Session) -> int:
    """Delete all expired password reset tokens.

    Args:
        db: Database session

    Returns:
        Number of tokens deleted
    """
    now = utcnow()
    stmt = delete(models.PasswordResetToken).where(models.PasswordResetToken.expires_at <= now)
    result = db.execute(stmt)
    db.flush()
    return result.rowcount
