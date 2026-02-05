"""add email verification tokens and user email_verified field"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "202601270004"
down_revision = "202601270003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add new audit actions to the enum (idempotent)
    op.execute("""
        ALTER TYPE auditaction ADD VALUE IF NOT EXISTS 'email_verification_sent';
        ALTER TYPE auditaction ADD VALUE IF NOT EXISTS 'email_verified';
    """)

    # Add email_verified column to users table
    op.add_column(
        "users",
        sa.Column("email_verified", sa.Boolean(), nullable=False, server_default="false"),
    )

    # Create email_verification_tokens table
    op.create_table(
        "email_verification_tokens",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("token_hash", sa.String(64), unique=True, nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("timezone('utc', now())"),
            nullable=False,
        ),
    )

    # Create indexes for email_verification_tokens
    op.create_index(
        "ix_email_verification_tokens_user_id", "email_verification_tokens", ["user_id"]
    )
    op.create_index(
        "ix_email_verification_tokens_token_hash", "email_verification_tokens", ["token_hash"]
    )
    op.create_index(
        "ix_email_verification_tokens_expires_at", "email_verification_tokens", ["expires_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_email_verification_tokens_expires_at", table_name="email_verification_tokens")
    op.drop_index("ix_email_verification_tokens_token_hash", table_name="email_verification_tokens")
    op.drop_index("ix_email_verification_tokens_user_id", table_name="email_verification_tokens")
    op.drop_table("email_verification_tokens")
    op.drop_column("users", "email_verified")
    # Note: Cannot remove enum values from auditaction in PostgreSQL
