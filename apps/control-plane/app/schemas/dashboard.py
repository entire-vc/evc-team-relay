from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel

from app.db.models import ShareKind, ShareMemberRole, ShareVisibility


class AdminStats(BaseModel):
    """Admin dashboard statistics."""

    total_users: int
    active_users: int
    admin_users: int
    total_shares: int
    shares_by_kind: dict[str, int]
    shares_by_visibility: dict[str, int]
    total_share_members: int
    recent_logins_count: int  # Last 24 hours
    recent_shares_count: int  # Last 24 hours


class UserStats(BaseModel):
    """User dashboard statistics."""

    owned_shares_count: int
    member_shares_count: int
    shares_by_kind: dict[str, int]
    total_share_members: int  # Total members across user's owned shares
    recent_activity_count: int  # Recent actions in last 7 days


class RecentShare(BaseModel):
    """Recent share information."""

    id: uuid.UUID
    kind: ShareKind
    path: str
    visibility: ShareVisibility
    created_at: datetime
    is_owner: bool
    user_role: ShareMemberRole | None

    model_config = {"from_attributes": True}


class RecentActivity(BaseModel):
    """Recent activity log."""

    timestamp: datetime
    action: str
    details: str  # Human-readable description

    model_config = {"from_attributes": True}
