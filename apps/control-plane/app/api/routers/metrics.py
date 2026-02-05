"""Prometheus metrics endpoint."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends
from fastapi.responses import PlainTextResponse
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.metrics import (
    DB_CONNECTIONS_ACTIVE,
    DB_HEALTH_STATUS,
    SESSIONS_ACTIVE,
    SHARE_MEMBERS_TOTAL,
    SHARES_TOTAL,
    USERS_ACTIVE_30D,
    USERS_TOTAL,
    init_app_info,
)
from app.db import models
from app.db.session import get_db

router = APIRouter(tags=["metrics"])


def update_business_metrics(db: Session) -> None:
    """Update business metrics from database."""
    try:
        # Users
        active_users = (
            db.query(func.count(models.User.id)).filter(models.User.is_active.is_(True)).scalar()
            or 0
        )
        inactive_users = (
            db.query(func.count(models.User.id)).filter(models.User.is_active.is_(False)).scalar()
            or 0
        )

        USERS_TOTAL.labels(status="active").set(active_users)
        USERS_TOTAL.labels(status="inactive").set(inactive_users)

        # Active users in last 30 days (users with sessions)
        thirty_days_ago = datetime.now(timezone.utc) - timedelta(days=30)
        active_30d = (
            db.query(func.count(func.distinct(models.UserSession.user_id)))
            .filter(models.UserSession.last_activity >= thirty_days_ago)
            .scalar()
            or 0
        )
        USERS_ACTIVE_30D.set(active_30d)

        # Shares by kind and visibility
        share_counts = (
            db.query(models.Share.kind, models.Share.visibility, func.count(models.Share.id))
            .group_by(models.Share.kind, models.Share.visibility)
            .all()
        )

        # Reset all share gauges first
        for kind in ["doc", "folder"]:
            for visibility in ["private", "public", "protected"]:
                SHARES_TOTAL.labels(kind=kind, visibility=visibility).set(0)

        for kind, visibility, count in share_counts:
            SHARES_TOTAL.labels(kind=kind.value, visibility=visibility.value).set(count)

        # Share members by role
        member_counts = (
            db.query(models.ShareMember.role, func.count(models.ShareMember.id))
            .group_by(models.ShareMember.role)
            .all()
        )

        for role in ["viewer", "editor"]:
            SHARE_MEMBERS_TOTAL.labels(role=role).set(0)

        for role, count in member_counts:
            SHARE_MEMBERS_TOTAL.labels(role=role.value).set(count)

        # Active sessions
        active_sessions = (
            db.query(func.count(models.UserSession.id))
            .filter(models.UserSession.expires_at > datetime.now(timezone.utc))
            .scalar()
            or 0
        )
        SESSIONS_ACTIVE.set(active_sessions)

        # Database health
        DB_HEALTH_STATUS.set(1)

    except Exception:
        DB_HEALTH_STATUS.set(0)
        raise


def update_db_connection_metrics(db: Session) -> None:
    """Update database connection pool metrics."""
    try:
        # Get connection pool stats from SQLAlchemy engine
        engine = db.get_bind()
        pool = engine.pool

        if hasattr(pool, "checkedout"):
            DB_CONNECTIONS_ACTIVE.set(pool.checkedout())
        if hasattr(pool, "checkedin"):
            from app.core.metrics import DB_CONNECTIONS_IDLE

            DB_CONNECTIONS_IDLE.set(pool.checkedin())
        if hasattr(pool, "size"):
            from app.core.metrics import DB_CONNECTIONS_TOTAL

            DB_CONNECTIONS_TOTAL.set(pool.size())
    except Exception:
        pass  # Connection pool metrics are best-effort


@router.get(
    "/metrics",
    response_class=PlainTextResponse,
    summary="Prometheus metrics",
    description="Returns metrics in Prometheus text format",
)
def get_metrics(db: Session = Depends(get_db)) -> PlainTextResponse:
    """Export Prometheus metrics."""
    # Initialize app info
    init_app_info()

    # Update business metrics from database
    try:
        update_business_metrics(db)
        update_db_connection_metrics(db)
    except Exception:
        # Log error but don't fail metrics endpoint
        pass

    # Generate Prometheus format
    metrics_output = generate_latest()

    return PlainTextResponse(
        content=metrics_output,
        media_type=CONTENT_TYPE_LATEST,
    )
