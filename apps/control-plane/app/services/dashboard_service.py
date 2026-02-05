from __future__ import annotations

import logging
from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import models
from app.schemas import dashboard as dashboard_schema

logger = logging.getLogger(__name__)


def get_admin_stats(db: Session) -> dashboard_schema.AdminStats:
    """
    Get statistics for admin dashboard.

    Returns aggregated counts for users, shares, and recent activity.
    If audit log queries fail, returns stats with safe defaults for audit-related fields.
    """
    try:
        # User statistics
        total_users = db.execute(select(func.count(models.User.id))).scalar_one()
        active_users = db.execute(
            select(func.count(models.User.id)).where(models.User.is_active == True)  # noqa: E712
        ).scalar_one()
        admin_users = db.execute(
            select(func.count(models.User.id)).where(models.User.is_admin == True)  # noqa: E712
        ).scalar_one()

        # Share statistics
        total_shares = db.execute(select(func.count(models.Share.id))).scalar_one()

        # Shares by kind
        shares_by_kind_result = db.execute(
            select(models.Share.kind, func.count(models.Share.id)).group_by(models.Share.kind)
        ).all()
        shares_by_kind = {str(kind.value): count for kind, count in shares_by_kind_result}

        # Shares by visibility
        shares_by_visibility_result = db.execute(
            select(models.Share.visibility, func.count(models.Share.id)).group_by(
                models.Share.visibility
            )
        ).all()
        shares_by_visibility = {
            str(visibility.value): count for visibility, count in shares_by_visibility_result
        }

        # Share members count
        total_share_members = db.execute(select(func.count(models.ShareMember.id))).scalar_one()

        # Recent activity (last 24 hours) - with error handling for audit logs
        twenty_four_hours_ago = datetime.utcnow() - timedelta(hours=24)

        try:
            recent_logins_count = db.execute(
                select(func.count(models.AuditLog.id)).where(
                    models.AuditLog.action == models.AuditAction.USER_LOGIN,
                    models.AuditLog.timestamp >= twenty_four_hours_ago,
                )
            ).scalar_one()
        except Exception as e:
            logger.warning(f"Failed to query recent logins from audit logs: {e}")
            recent_logins_count = 0

        try:
            recent_shares_count = db.execute(
                select(func.count(models.AuditLog.id)).where(
                    models.AuditLog.action == models.AuditAction.SHARE_CREATED,
                    models.AuditLog.timestamp >= twenty_four_hours_ago,
                )
            ).scalar_one()
        except Exception as e:
            logger.warning(f"Failed to query recent shares from audit logs: {e}")
            recent_shares_count = 0

        return dashboard_schema.AdminStats(
            total_users=total_users,
            active_users=active_users,
            admin_users=admin_users,
            total_shares=total_shares,
            shares_by_kind=shares_by_kind,
            shares_by_visibility=shares_by_visibility,
            total_share_members=total_share_members,
            recent_logins_count=recent_logins_count,
            recent_shares_count=recent_shares_count,
        )
    except Exception as e:
        logger.error(f"Dashboard stats query failed: {e}", exc_info=True)
        # Return safe defaults if entire query fails
        return dashboard_schema.AdminStats(
            total_users=0,
            active_users=0,
            admin_users=0,
            total_shares=0,
            shares_by_kind={},
            shares_by_visibility={},
            total_share_members=0,
            recent_logins_count=0,
            recent_shares_count=0,
        )


def get_user_stats(db: Session, user: models.User) -> dashboard_schema.UserStats:
    """
    Get statistics for user dashboard.

    Returns user-specific share and activity counts.
    """
    # Owned shares count
    owned_shares_count = db.execute(
        select(func.count(models.Share.id)).where(models.Share.owner_user_id == user.id)
    ).scalar_one()

    # Member shares count (shares where user is a member, not owner)
    member_shares_count = db.execute(
        select(func.count(models.ShareMember.id)).where(models.ShareMember.user_id == user.id)
    ).scalar_one()

    # Shares by kind (only owned shares)
    shares_by_kind_result = db.execute(
        select(models.Share.kind, func.count(models.Share.id))
        .where(models.Share.owner_user_id == user.id)
        .group_by(models.Share.kind)
    ).all()
    shares_by_kind = {str(kind.value): count for kind, count in shares_by_kind_result}

    # Total members across user's owned shares
    total_share_members = db.execute(
        select(func.count(models.ShareMember.id))
        .join(models.Share, models.ShareMember.share_id == models.Share.id)
        .where(models.Share.owner_user_id == user.id)
    ).scalar_one()

    # Recent activity (last 7 days) - actions where user is the actor
    seven_days_ago = datetime.utcnow() - timedelta(days=7)
    recent_activity_count = db.execute(
        select(func.count(models.AuditLog.id)).where(
            models.AuditLog.actor_user_id == user.id, models.AuditLog.timestamp >= seven_days_ago
        )
    ).scalar_one()

    return dashboard_schema.UserStats(
        owned_shares_count=owned_shares_count,
        member_shares_count=member_shares_count,
        shares_by_kind=shares_by_kind,
        total_share_members=total_share_members,
        recent_activity_count=recent_activity_count,
    )
