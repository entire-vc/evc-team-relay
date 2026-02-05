"""add share invites table"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "202601260001"
down_revision = "202601120001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add new audit actions to the enum
    op.execute("""
        ALTER TYPE auditaction ADD VALUE IF NOT EXISTS 'invite_created';
        ALTER TYPE auditaction ADD VALUE IF NOT EXISTS 'invite_revoked';
        ALTER TYPE auditaction ADD VALUE IF NOT EXISTS 'invite_redeemed';
    """)

    # Create share_invites table
    op.create_table(
        "share_invites",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column(
            "share_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("shares.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("token", sa.String(64), unique=True, nullable=False),
        sa.Column(
            "created_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "role",
            postgresql.ENUM("viewer", "editor", name="sharememberrole", create_type=False),
            nullable=False,
            server_default="viewer",
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("max_uses", sa.Integer, nullable=True),
        sa.Column("use_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("timezone('utc', now())"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("timezone('utc', now())"),
            nullable=False,
        ),
    )

    # Create indexes
    op.create_index("ix_share_invites_token", "share_invites", ["token"], unique=True)
    op.create_index("ix_share_invites_share_id", "share_invites", ["share_id"])
    op.create_index("ix_share_invites_expires_at", "share_invites", ["expires_at"])


def downgrade() -> None:
    op.drop_index("ix_share_invites_expires_at", table_name="share_invites")
    op.drop_index("ix_share_invites_share_id", table_name="share_invites")
    op.drop_index("ix_share_invites_token", table_name="share_invites")
    op.drop_table("share_invites")

    # Note: Cannot remove enum values in PostgreSQL, they will remain but unused
