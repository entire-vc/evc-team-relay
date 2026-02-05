from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import models


def log_action(
    db: Session,
    action: models.AuditAction,
    actor_user_id: uuid.UUID | None = None,
    target_user_id: uuid.UUID | None = None,
    target_share_id: uuid.UUID | None = None,
    details: dict | None = None,
    ip_address: str | None = None,
    user_agent: str | None = None,
) -> models.AuditLog:
    """
    Create an audit log entry.

    Args:
        db: Database session
        action: The action being logged
        actor_user_id: User who performed the action
        target_user_id: User who was affected (for user operations)
        target_share_id: Share that was affected (for share operations)
        details: Additional context as JSON
        ip_address: IP address of the request
        user_agent: User agent string

    Returns:
        Created AuditLog instance
    """
    audit_log = models.AuditLog(
        action=action,
        actor_user_id=actor_user_id,
        target_user_id=target_user_id,
        target_share_id=target_share_id,
        details=details,
        ip_address=ip_address,
        user_agent=user_agent,
    )
    db.add(audit_log)
    db.commit()
    db.refresh(audit_log)
    return audit_log


def list_audit_logs(
    db: Session,
    action: models.AuditAction | None = None,
    actor_user_id: uuid.UUID | None = None,
    target_user_id: uuid.UUID | None = None,
    target_share_id: uuid.UUID | None = None,
    start_date: datetime | None = None,
    end_date: datetime | None = None,
    skip: int = 0,
    limit: int = 100,
) -> list[models.AuditLog]:
    """
    List audit logs with optional filters.

    Args:
        db: Database session
        action: Filter by action type
        actor_user_id: Filter by actor
        target_user_id: Filter by target user
        target_share_id: Filter by target share
        start_date: Filter logs after this timestamp
        end_date: Filter logs before this timestamp
        skip: Pagination offset
        limit: Maximum results (capped at 100)

    Returns:
        List of AuditLog instances
    """
    limit = min(limit, 100)  # Cap at 100

    stmt = select(models.AuditLog)

    # Apply filters
    if action:
        stmt = stmt.where(models.AuditLog.action == action)
    if actor_user_id:
        stmt = stmt.where(models.AuditLog.actor_user_id == actor_user_id)
    if target_user_id:
        stmt = stmt.where(models.AuditLog.target_user_id == target_user_id)
    if target_share_id:
        stmt = stmt.where(models.AuditLog.target_share_id == target_share_id)
    if start_date:
        stmt = stmt.where(models.AuditLog.timestamp >= start_date)
    if end_date:
        stmt = stmt.where(models.AuditLog.timestamp <= end_date)

    # Order by timestamp descending (most recent first)
    stmt = stmt.order_by(models.AuditLog.timestamp.desc())

    # Pagination
    stmt = stmt.offset(skip).limit(limit)

    return list(db.execute(stmt).scalars().all())
