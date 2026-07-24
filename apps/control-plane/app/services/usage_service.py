"""Usage counting helpers for billing limit enforcement."""

from __future__ import annotations

import asyncio
import time
import uuid

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.clients import storage_client
from app.core.config import get_settings
from app.db import models


def count_user_shares(db: Session, user_id: uuid.UUID) -> int:
    """Count total shares owned by user."""
    result = db.execute(select(func.count()).where(models.Share.owner_user_id == user_id))
    return result.scalar() or 0


def count_share_members(db: Session, share_id: uuid.UUID) -> int:
    """Count members of a share (excluding owner)."""
    result = db.execute(select(func.count()).where(models.ShareMember.share_id == share_id))
    return result.scalar() or 0


def count_web_published(db: Session, user_id: uuid.UUID) -> int:
    """Count web-published shares owned by user."""
    result = db.execute(
        select(func.count()).where(
            models.Share.owner_user_id == user_id,
            models.Share.web_published.is_(True),
        )
    )
    return result.scalar() or 0


# In-memory storage cache: {user_id_str: (bytes, timestamp)}
_storage_cache: dict[str, tuple[int, float]] = {}
_STORAGE_CACHE_TTL = 900  # 15 minutes


async def get_user_storage_bytes(db: Session, user_id: uuid.UUID) -> int | None:
    """Calculate total storage bytes for a user from MinIO.

    Args:
        db: Database session
        user_id: User UUID

    Returns:
        Total storage in bytes or None if MinIO not configured/unavailable
    """
    settings = get_settings()
    if not settings.minio_access_key:
        return None  # MinIO not configured

    user_key = str(user_id)
    now = time.monotonic()

    # Check cache
    cached = _storage_cache.get(user_key)
    if cached:
        bytes_val, ts = cached
        if now - ts < _STORAGE_CACHE_TTL:
            return bytes_val

    # Get all shares owned by user
    shares = (
        db.execute(select(models.Share.id).where(models.Share.owner_user_id == user_id))
        .scalars()
        .all()
    )

    # Calculate storage from MinIO in background thread
    total_bytes = 0
    try:
        for share_id in shares:
            prefix = f"{share_id}/"
            size = await asyncio.to_thread(storage_client.get_objects_size, prefix)
            total_bytes += size
    except Exception:
        # MinIO unavailable — return cached value or None
        if cached:
            return cached[0]
        return None

    # Update cache
    _storage_cache[user_key] = (total_bytes, now)
    return total_bytes
