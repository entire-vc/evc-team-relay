"""Email verification service."""

from __future__ import annotations

import hashlib
import secrets
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import models


def generate_verification_token() -> str:
    """Generate a secure verification token (32 bytes = 64 hex chars)."""
    return secrets.token_hex(32)


def hash_token(token: str) -> str:
    """Hash a token using SHA-256."""
    return hashlib.sha256(token.encode()).hexdigest()


def create_verification_token(
    db: Session,
    user_id: uuid.UUID,
    expires_hours: int = 24,
) -> str:
    """Create a new email verification token.

    Args:
        db: Database session
        user_id: User ID to create token for
        expires_hours: Token expiration time in hours (default: 24)

    Returns:
        The raw (unhashed) verification token
    """
    # Invalidate any existing verification tokens for this user
    stmt = select(models.EmailVerificationToken).where(
        models.EmailVerificationToken.user_id == user_id,
        models.EmailVerificationToken.verified_at.is_(None),
    )
    existing_tokens = db.execute(stmt).scalars().all()
    for token in existing_tokens:
        db.delete(token)

    # Generate new token
    raw_token = generate_verification_token()
    token_hash = hash_token(raw_token)
    expires_at = datetime.now(timezone.utc) + timedelta(hours=expires_hours)

    # Create token record
    verification_token = models.EmailVerificationToken(
        id=uuid.uuid4(),
        user_id=user_id,
        token_hash=token_hash,
        expires_at=expires_at,
    )
    db.add(verification_token)
    db.commit()

    return raw_token


def validate_verification_token(
    db: Session,
    token: str,
) -> models.User | None:
    """Validate a verification token and return the associated user.

    Args:
        db: Database session
        token: The raw verification token

    Returns:
        The user if token is valid, None otherwise
    """
    token_hash = hash_token(token)

    stmt = select(models.EmailVerificationToken).where(
        models.EmailVerificationToken.token_hash == token_hash,
    )
    token_record = db.execute(stmt).scalar_one_or_none()

    if not token_record:
        return None

    # Check if already used
    if token_record.verified_at is not None:
        return None

    # Check if expired - handle both naive and aware datetimes
    now = datetime.now(timezone.utc)
    expires_at = token_record.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if expires_at < now:
        return None

    # Get user
    user = db.get(models.User, token_record.user_id)
    return user


def complete_verification(
    db: Session,
    token: str,
) -> models.User | None:
    """Complete email verification and mark user as verified.

    Args:
        db: Database session
        token: The raw verification token

    Returns:
        The verified user if successful, None otherwise
    """
    token_hash = hash_token(token)

    stmt = select(models.EmailVerificationToken).where(
        models.EmailVerificationToken.token_hash == token_hash,
    )
    token_record = db.execute(stmt).scalar_one_or_none()

    if not token_record:
        return None

    # Check if already used
    if token_record.verified_at is not None:
        return None

    # Check if expired - handle both naive and aware datetimes
    now = datetime.now(timezone.utc)
    expires_at = token_record.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if expires_at < now:
        return None

    # Get user
    user = db.get(models.User, token_record.user_id)
    if not user:
        return None

    # Mark token as used
    token_record.verified_at = datetime.now(timezone.utc)

    # Mark user as verified
    user.email_verified = True

    db.commit()
    db.refresh(user)

    return user


def is_user_verified(db: Session, user_id: uuid.UUID) -> bool:
    """Check if a user's email is verified.

    Args:
        db: Database session
        user_id: User ID to check

    Returns:
        True if user's email is verified, False otherwise
    """
    user = db.get(models.User, user_id)
    if not user:
        return False
    return user.email_verified


def cleanup_expired_tokens(db: Session) -> int:
    """Delete expired verification tokens.

    Args:
        db: Database session

    Returns:
        Number of tokens deleted
    """
    now = datetime.now(timezone.utc)
    # For SQLite compatibility, we'll fetch all and filter in Python
    stmt = select(models.EmailVerificationToken)
    all_tokens = db.execute(stmt).scalars().all()

    count = 0
    for token in all_tokens:
        expires_at = token.expires_at
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        if expires_at < now:
            db.delete(token)
            count += 1

    db.commit()
    return count
