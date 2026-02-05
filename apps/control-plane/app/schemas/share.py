from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field, ValidationInfo, field_validator

from app.db.models import ShareKind, ShareMemberRole, ShareVisibility


class ShareBase(BaseModel):
    kind: ShareKind = ShareKind.DOC
    path: str = Field(min_length=1, max_length=512)
    visibility: ShareVisibility = ShareVisibility.PRIVATE


class ShareCreate(ShareBase):
    password: str | None = Field(default=None, min_length=8, max_length=128)
    web_published: bool = False
    web_slug: str | None = Field(default=None, min_length=1, max_length=255)
    web_noindex: bool = True
    web_sync_mode: str = "manual"

    @field_validator("password")
    @classmethod
    def validate_password(cls, value: str | None, info: ValidationInfo):
        visibility = info.data.get("visibility")
        if visibility == ShareVisibility.PROTECTED and not value:
            raise ValueError("Password required for protected shares")
        return value

    @field_validator("web_sync_mode")
    @classmethod
    def validate_web_sync_mode(cls, value: str):
        if value not in ("manual", "auto"):
            raise ValueError("web_sync_mode must be 'manual' or 'auto'")
        return value


class FolderItem(BaseModel):
    """Item in a folder for web publishing navigation."""

    path: str  # Relative path within folder
    name: str  # Display name
    type: str  # "doc", "folder", "canvas"


class ShareUpdate(BaseModel):
    kind: ShareKind | None = None
    path: str | None = Field(default=None, min_length=1, max_length=512)
    visibility: ShareVisibility | None = None
    password: str | None = Field(default=None, min_length=8, max_length=128)
    web_published: bool | None = None
    web_slug: str | None = Field(default=None, min_length=1, max_length=255)
    web_noindex: bool | None = None
    web_sync_mode: str | None = None
    web_content: str | None = None  # Document content for web publishing
    web_folder_items: list[FolderItem] | None = None  # Folder contents for web publishing
    web_doc_id: str | None = None  # Y-sweet document ID for real-time sync

    @field_validator("web_sync_mode")
    @classmethod
    def validate_web_sync_mode(cls, value: str | None):
        if value is not None and value not in ("manual", "auto"):
            raise ValueError("web_sync_mode must be 'manual' or 'auto'")
        return value


class ShareRead(BaseModel):
    id: uuid.UUID
    kind: ShareKind
    path: str
    visibility: ShareVisibility
    owner_user_id: uuid.UUID
    created_at: datetime
    updated_at: datetime
    web_published: bool
    web_slug: str | None
    web_noindex: bool
    web_sync_mode: str
    web_url: str | None = None  # Computed field, set by service
    web_doc_id: str | None = None  # Y-sweet document ID for real-time sync

    model_config = {"from_attributes": True}


class ShareMemberCreate(BaseModel):
    user_id: uuid.UUID
    role: ShareMemberRole = ShareMemberRole.VIEWER


class ShareMemberUpdate(BaseModel):
    role: ShareMemberRole


class ShareMemberRead(BaseModel):
    id: uuid.UUID
    share_id: uuid.UUID
    user_id: uuid.UUID
    user_email: str  # Email of the user for better UX
    role: ShareMemberRole

    model_config = {"from_attributes": True}


class ShareListItem(BaseModel):
    """Share with user's role information for list view"""

    id: uuid.UUID
    kind: ShareKind
    path: str
    visibility: ShareVisibility
    owner_user_id: uuid.UUID
    created_at: datetime
    updated_at: datetime
    # User's relationship to this share
    is_owner: bool
    user_role: ShareMemberRole | None = None  # None if owner or not a member
    # Web publishing fields
    web_published: bool
    web_slug: str | None
    web_noindex: bool
    web_sync_mode: str
    web_url: str | None = None  # Computed field, set by service

    model_config = {"from_attributes": True}
