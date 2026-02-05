from __future__ import annotations

import uuid
from datetime import datetime
from enum import Enum

from sqlalchemy import JSON, DateTime, ForeignKey, String, Text, UniqueConstraint, func
from sqlalchemy import Enum as PgEnum
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):  # type: ignore[too-many-ancestors]
    pass


class ShareKind(str, Enum):
    DOC = "doc"
    FOLDER = "folder"


class ShareVisibility(str, Enum):
    PRIVATE = "private"
    PUBLIC = "public"
    PROTECTED = "protected"


class ShareMemberRole(str, Enum):
    VIEWER = "viewer"
    EDITOR = "editor"


class AuditAction(str, Enum):
    USER_CREATED = "user_created"
    USER_UPDATED = "user_updated"
    USER_DELETED = "user_deleted"
    USER_LOGIN = "user_login"
    USER_LOGOUT = "user_logout"
    SHARE_CREATED = "share_created"
    SHARE_UPDATED = "share_updated"
    SHARE_DELETED = "share_deleted"
    SHARE_MEMBER_ADDED = "share_member_added"
    SHARE_MEMBER_UPDATED = "share_member_updated"
    SHARE_MEMBER_REMOVED = "share_member_removed"
    TOKEN_ISSUED = "token_issued"
    INVITE_CREATED = "invite_created"
    INVITE_REVOKED = "invite_revoked"
    INVITE_REDEEMED = "invite_redeemed"
    SESSION_CREATED = "session_created"
    SESSION_REVOKED = "session_revoked"
    TOKEN_REFRESHED = "token_refreshed"
    OAUTH_LOGIN = "oauth_login"
    OAUTH_ACCOUNT_LINKED = "oauth_account_linked"
    OAUTH_ACCOUNT_UNLINKED = "oauth_account_unlinked"
    PASSWORD_RESET_REQUESTED = "password_reset_requested"
    PASSWORD_RESET_COMPLETED = "password_reset_completed"
    EMAIL_VERIFICATION_SENT = "email_verification_sent"
    EMAIL_VERIFIED = "email_verified"
    TOTP_ENABLED = "totp_enabled"
    TOTP_DISABLED = "totp_disabled"
    TOTP_BACKUP_USED = "totp_backup_used"


class OAuthProviderType(str, Enum):
    OIDC = "oidc"
    OAUTH2 = "oauth2"


class WebhookDeliveryStatus(str, Enum):
    PENDING = "pending"
    SUCCESS = "success"
    FAILED = "failed"
    MAX_RETRIES_EXCEEDED = "max_retries_exceeded"


class EmailStatus(str, Enum):
    PENDING = "pending"
    SENT = "sent"
    FAILED = "failed"


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class User(Base, TimestampMixin):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    email: Mapped[str] = mapped_column(String(320), unique=True, nullable=False, index=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    is_admin: Mapped[bool] = mapped_column(default=False, nullable=False)
    is_active: Mapped[bool] = mapped_column(default=True, nullable=False)
    email_verified: Mapped[bool] = mapped_column(default=False, nullable=False)
    totp_secret_encrypted: Mapped[str | None] = mapped_column(String(255), nullable=True)
    totp_enabled: Mapped[bool] = mapped_column(default=False, nullable=False)
    backup_codes_encrypted: Mapped[str | None] = mapped_column(String(2000), nullable=True)

    shares_owned: Mapped[list["Share"]] = relationship(
        back_populates="owner",
        cascade="all, delete-orphan",
    )
    memberships: Mapped[list["ShareMember"]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
    )
    sessions: Mapped[list["UserSession"]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
    )
    oauth_accounts: Mapped[list["UserOAuthAccount"]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
    )
    webhooks: Mapped[list["Webhook"]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
    )
    email_preferences: Mapped["UserEmailPreferences | None"] = relationship(
        back_populates="user",
        uselist=False,
        cascade="all, delete-orphan",
    )


class Share(Base, TimestampMixin):
    __tablename__ = "shares"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    kind: Mapped[ShareKind] = mapped_column(
        PgEnum(ShareKind, name="sharekind", values_callable=lambda x: [e.value for e in x]),
        nullable=False,
    )
    path: Mapped[str] = mapped_column(String(512), nullable=False)
    visibility: Mapped[ShareVisibility] = mapped_column(
        PgEnum(
            ShareVisibility, name="sharevisibility", values_callable=lambda x: [e.value for e in x]
        ),
        nullable=False,
    )
    password_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)
    owner_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )

    # Web publishing fields
    web_published: Mapped[bool] = mapped_column(default=False, nullable=False)
    web_slug: Mapped[str | None] = mapped_column(String(255), unique=True, nullable=True)
    web_noindex: Mapped[bool] = mapped_column(default=True, nullable=False)
    web_sync_mode: Mapped[str] = mapped_column(String(10), default="manual", nullable=False)
    web_content: Mapped[str | None] = mapped_column(Text, nullable=True)
    web_content_updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # Use JSONB for PostgreSQL (supports DISTINCT), JSON for SQLite (tests)
    web_folder_items: Mapped[list | None] = mapped_column(
        JSON().with_variant(JSONB(), "postgresql"), nullable=True
    )
    # Y-sweet document ID for real-time sync (S3RN encoded)
    web_doc_id: Mapped[str | None] = mapped_column(String(512), nullable=True)

    owner: Mapped["User"] = relationship(back_populates="shares_owned")
    members: Mapped[list["ShareMember"]] = relationship(
        back_populates="share",
        cascade="all, delete-orphan",
    )
    invites: Mapped[list["ShareInvite"]] = relationship(
        back_populates="share",
        cascade="all, delete-orphan",
    )


class ShareMember(Base, TimestampMixin):
    __tablename__ = "share_members"
    __table_args__ = (UniqueConstraint("share_id", "user_id", name="uq_share_member"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    share_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("shares.id", ondelete="CASCADE"),
        nullable=False,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    role: Mapped[ShareMemberRole] = mapped_column(
        PgEnum(
            ShareMemberRole, name="sharememberrole", values_callable=lambda x: [e.value for e in x]
        ),
        nullable=False,
        default=ShareMemberRole.VIEWER,
    )

    share: Mapped["Share"] = relationship(back_populates="members")
    user: Mapped["User"] = relationship(back_populates="memberships")


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
        index=True,
    )
    action: Mapped[AuditAction] = mapped_column(
        PgEnum(AuditAction, name="auditaction", values_callable=lambda x: [e.value for e in x]),
        nullable=False,
        index=True,
    )
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    target_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    target_share_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("shares.id", ondelete="SET NULL"),
        nullable=True,
    )
    details: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    ip_address: Mapped[str | None] = mapped_column(String(45), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(512), nullable=True)


class ShareInvite(Base, TimestampMixin):
    __tablename__ = "share_invites"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    share_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("shares.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    token: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    created_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    role: Mapped[ShareMemberRole] = mapped_column(
        PgEnum(
            ShareMemberRole, name="sharememberrole", values_callable=lambda x: [e.value for e in x]
        ),
        nullable=False,
        default=ShareMemberRole.VIEWER,
    )
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        index=True,
    )
    max_uses: Mapped[int | None] = mapped_column(nullable=True)
    use_count: Mapped[int] = mapped_column(default=0, nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    email: Mapped[str | None] = mapped_column(String(320), nullable=True, index=True)

    share: Mapped["Share"] = relationship(back_populates="invites")
    creator: Mapped["User"] = relationship()


class UserSession(Base):
    __tablename__ = "user_sessions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    refresh_token_hash: Mapped[str] = mapped_column(
        String(255), unique=True, nullable=False, index=True
    )
    device_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(500), nullable=True)
    ip_address: Mapped[str | None] = mapped_column(String(45), nullable=True)
    last_activity: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    user: Mapped["User"] = relationship(back_populates="sessions")


class OAuthProvider(Base, TimestampMixin):
    __tablename__ = "oauth_providers"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(50), unique=True, nullable=False, index=True)
    provider_type: Mapped[OAuthProviderType] = mapped_column(
        PgEnum(
            OAuthProviderType,
            name="oauthprovidertype",
            values_callable=lambda x: [e.value for e in x],
        ),
        nullable=False,
        default=OAuthProviderType.OIDC,
    )
    issuer_url: Mapped[str] = mapped_column(String(500), nullable=False)
    client_id: Mapped[str] = mapped_column(String(255), nullable=False)
    client_secret_encrypted: Mapped[str] = mapped_column(String(500), nullable=False)
    enabled: Mapped[bool] = mapped_column(default=True, nullable=False)
    auto_register: Mapped[bool] = mapped_column(default=True, nullable=False)

    user_accounts: Mapped[list["UserOAuthAccount"]] = relationship(
        back_populates="provider",
        cascade="all, delete-orphan",
    )


class UserOAuthAccount(Base, TimestampMixin):
    __tablename__ = "user_oauth_accounts"
    __table_args__ = (UniqueConstraint("provider_id", "provider_user_id", name="uq_provider_user"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    provider_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("oauth_providers.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    provider_user_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    email: Mapped[str] = mapped_column(String(320), nullable=False)
    name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    picture_url: Mapped[str | None] = mapped_column(Text, nullable=True)

    user: Mapped["User"] = relationship(back_populates="oauth_accounts")
    provider: Mapped["OAuthProvider"] = relationship(back_populates="user_accounts")


class PasswordResetToken(Base):
    __tablename__ = "password_reset_tokens"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        index=True,
    )
    used_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    user: Mapped["User"] = relationship()


class EmailVerificationToken(Base):
    __tablename__ = "email_verification_tokens"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        index=True,
    )
    verified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    user: Mapped["User"] = relationship()


class Webhook(Base, TimestampMixin):
    __tablename__ = "webhooks"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=True,  # NULL for admin/global webhooks
        index=True,
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    url: Mapped[str] = mapped_column(String(2048), nullable=False)
    secret: Mapped[str] = mapped_column(String(64), nullable=False)
    events: Mapped[list] = mapped_column(JSON, nullable=False)  # Array of event types
    active: Mapped[bool] = mapped_column(default=True, nullable=False)
    failure_count: Mapped[int] = mapped_column(default=0, nullable=False)

    user: Mapped["User | None"] = relationship(back_populates="webhooks")
    deliveries: Mapped[list["WebhookDelivery"]] = relationship(
        back_populates="webhook",
        cascade="all, delete-orphan",
    )


class WebhookDelivery(Base, TimestampMixin):
    __tablename__ = "webhook_deliveries"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    webhook_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("webhooks.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    event_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    event_type: Mapped[str] = mapped_column(String(50), nullable=False)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False)
    status: Mapped[WebhookDeliveryStatus] = mapped_column(
        PgEnum(
            WebhookDeliveryStatus,
            name="webhookdeliverystatus",
            values_callable=lambda x: [e.value for e in x],
        ),
        nullable=False,
        default=WebhookDeliveryStatus.PENDING,
    )
    response_status_code: Mapped[int | None] = mapped_column(nullable=True)
    response_body: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    attempt_count: Mapped[int] = mapped_column(default=0, nullable=False)
    next_retry_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        index=True,
    )

    webhook: Mapped["Webhook"] = relationship(back_populates="deliveries")


class EmailQueue(Base):
    __tablename__ = "email_queue"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    to_email: Mapped[str] = mapped_column(String(320), nullable=False)
    subject: Mapped[str] = mapped_column(String(255), nullable=False)
    body_text: Mapped[str] = mapped_column(String, nullable=False)
    body_html: Mapped[str] = mapped_column(String, nullable=False)
    email_type: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    status: Mapped[EmailStatus] = mapped_column(
        PgEnum(
            EmailStatus,
            name="emailstatus",
            values_callable=lambda x: [e.value for e in x],
        ),
        nullable=False,
        default=EmailStatus.PENDING,
    )
    attempt_count: Mapped[int] = mapped_column(default=0, nullable=False)
    error_message: Mapped[str | None] = mapped_column(String, nullable=True)
    next_retry_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    sent_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )


class UserEmailPreferences(Base):
    __tablename__ = "user_email_preferences"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
    )
    invite_notifications: Mapped[bool] = mapped_column(default=True, nullable=False)
    share_update_notifications: Mapped[bool] = mapped_column(default=True, nullable=False)
    security_alerts: Mapped[bool] = mapped_column(default=True, nullable=False)
    member_notifications: Mapped[bool] = mapped_column(default=True, nullable=False)
    digest_emails: Mapped[bool] = mapped_column(default=False, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    user: Mapped["User"] = relationship(back_populates="email_preferences")


class InstanceSetting(Base):
    __tablename__ = "instance_settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
