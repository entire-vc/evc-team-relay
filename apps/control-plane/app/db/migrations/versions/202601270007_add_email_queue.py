"""Add email_queue and user_email_preferences tables.

Revision ID: 202601270007
Revises: 202601270006
Create Date: 2026-01-27 12:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "202601270007"
down_revision: str | None = "202601270006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Create email_status enum
    email_status = postgresql.ENUM(
        "pending",
        "sent",
        "failed",
        name="emailstatus",
        create_type=False,
    )
    email_status.create(op.get_bind(), checkfirst=True)

    # Create email_queue table
    op.create_table(
        "email_queue",
        sa.Column("id", sa.UUID(), nullable=False, server_default=sa.text("gen_random_uuid()")),
        sa.Column("to_email", sa.String(length=320), nullable=False),
        sa.Column("subject", sa.String(length=255), nullable=False),
        sa.Column("body_text", sa.Text(), nullable=False),
        sa.Column("body_html", sa.Text(), nullable=False),
        sa.Column("email_type", sa.String(length=50), nullable=False),  # For tracking/metrics
        sa.Column(
            "status",
            postgresql.ENUM(
                "pending",
                "sent",
                "failed",
                name="emailstatus",
                create_type=False,
            ),
            nullable=False,
            server_default=sa.text("'pending'"),
        ),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("next_retry_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_email_queue_status", "email_queue", ["status"], unique=False)
    op.create_index(
        "idx_email_queue_next_retry",
        "email_queue",
        ["next_retry_at"],
        unique=False,
        postgresql_where=sa.text("status = 'pending'"),
    )
    op.create_index("idx_email_queue_email_type", "email_queue", ["email_type"], unique=False)

    # Create user_email_preferences table
    op.create_table(
        "user_email_preferences",
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column(
            "invite_notifications", sa.Boolean(), nullable=False, server_default=sa.text("true")
        ),
        sa.Column(
            "share_update_notifications",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
        sa.Column("security_alerts", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column(
            "member_notifications", sa.Boolean(), nullable=False, server_default=sa.text("true")
        ),
        sa.Column("digest_emails", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("user_id"),
    )

    # Add email column to share_invites for email-based invites
    op.add_column(
        "share_invites",
        sa.Column("email", sa.String(length=320), nullable=True),
    )
    op.create_index(
        "idx_share_invites_email",
        "share_invites",
        ["email"],
        unique=False,
        postgresql_where=sa.text("email IS NOT NULL"),
    )


def downgrade() -> None:
    # Remove email column from share_invites
    op.drop_index("idx_share_invites_email", table_name="share_invites")
    op.drop_column("share_invites", "email")

    # Drop user_email_preferences table
    op.drop_table("user_email_preferences")

    # Drop email_queue table
    op.drop_table("email_queue")

    # Drop enum type
    postgresql.ENUM(name="emailstatus").drop(op.get_bind(), checkfirst=True)
