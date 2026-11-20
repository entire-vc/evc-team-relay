"""Tombstones for paths removed by sync DELETE (see models.SyncAssetTombstone)."""

from __future__ import annotations

import uuid

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.core import security
from app.db import models


def record(db: Session, share_id: uuid.UUID, path: str) -> None:
    """Mark a path deleted; caller commits (same transaction as the index removal)."""
    db.merge(models.SyncAssetTombstone(share_id=share_id, path=path, deleted_at=security.utcnow()))


def is_deleted(db: Session, share_id: uuid.UUID, path: str) -> bool:
    return (
        db.execute(
            select(models.SyncAssetTombstone.path).where(
                models.SyncAssetTombstone.share_id == share_id,
                models.SyncAssetTombstone.path == path,
            )
        ).first()
        is not None
    )


def clear(db: Session, share_id: uuid.UUID, path: str) -> None:
    """An untagged upload republished the path: serve it again. Commits."""
    try:
        db.execute(
            delete(models.SyncAssetTombstone).where(
                models.SyncAssetTombstone.share_id == share_id,
                models.SyncAssetTombstone.path == path,
            )
        )
        db.commit()
    except BaseException:
        db.rollback()
        raise
