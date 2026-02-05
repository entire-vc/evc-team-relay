from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field

from app.db.models import ShareMemberRole


class InviteCreate(BaseModel):
    """Schema for creating an invite link."""

    role: ShareMemberRole = ShareMemberRole.VIEWER
    expires_in_days: int | None = Field(default=7, ge=1, le=30)
    max_uses: int | None = Field(default=None, ge=1)


class InviteRead(BaseModel):
    """Schema for reading invite link details."""

    id: uuid.UUID
    share_id: uuid.UUID
    token: str
    created_by: uuid.UUID
    role: ShareMemberRole
    expires_at: datetime | None
    max_uses: int | None
    use_count: int
    revoked_at: datetime | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class InvitePublicInfo(BaseModel):
    """Public information about an invite (no auth required)."""

    share_path: str
    share_kind: str
    owner_email: str
    role: ShareMemberRole
    is_valid: bool
    error: str | None = None
    expires_at: datetime | None = None


class InviteRedeemNewUser(BaseModel):
    """Schema for redeeming invite as a new user."""

    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=8, max_length=128)


class InviteRedeemResponse(BaseModel):
    """Response after successfully redeeming an invite."""

    user_id: uuid.UUID
    user_email: str
    share_id: uuid.UUID
    share_path: str
    role: ShareMemberRole
    access_token: str | None = None  # Only set for new user registration
