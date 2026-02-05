"""Add webhooks and webhook_deliveries tables.

Revision ID: 202601270006
Revises: 202601270005
Create Date: 2026-01-27 12:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "202601270006"
down_revision: str | None = "202601270005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Create webhook_delivery_status enum
    webhook_delivery_status = postgresql.ENUM(
        "pending",
        "success",
        "failed",
        "max_retries_exceeded",
        name="webhookdeliverystatus",
        create_type=False,
    )
    webhook_delivery_status.create(op.get_bind(), checkfirst=True)

    # Create webhooks table
    op.create_table(
        "webhooks",
        sa.Column("id", sa.UUID(), nullable=False, server_default=sa.text("gen_random_uuid()")),
        sa.Column("user_id", sa.UUID(), nullable=True),  # NULL for admin/global webhooks
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("url", sa.String(length=2048), nullable=False),
        sa.Column("secret", sa.String(length=64), nullable=False),
        sa.Column("events", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("failure_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_webhooks_user_id", "webhooks", ["user_id"], unique=False)
    op.create_index(
        "idx_webhooks_active",
        "webhooks",
        ["active"],
        unique=False,
        postgresql_where=sa.text("active = true"),
    )

    # Create webhook_deliveries table
    op.create_table(
        "webhook_deliveries",
        sa.Column("id", sa.UUID(), nullable=False, server_default=sa.text("gen_random_uuid()")),
        sa.Column("webhook_id", sa.UUID(), nullable=False),
        sa.Column("event_id", sa.UUID(), nullable=False),  # Unique event identifier
        sa.Column("event_type", sa.String(length=50), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "status",
            postgresql.ENUM(
                "pending",
                "success",
                "failed",
                "max_retries_exceeded",
                name="webhookdeliverystatus",
                create_type=False,
            ),
            nullable=False,
            server_default=sa.text("'pending'"),
        ),
        sa.Column("response_status_code", sa.Integer(), nullable=True),
        sa.Column("response_body", sa.Text(), nullable=True),  # Truncated to 1KB
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("next_retry_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["webhook_id"], ["webhooks.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "idx_webhook_deliveries_webhook_id", "webhook_deliveries", ["webhook_id"], unique=False
    )
    op.create_index("idx_webhook_deliveries_status", "webhook_deliveries", ["status"], unique=False)
    op.create_index(
        "idx_webhook_deliveries_next_retry",
        "webhook_deliveries",
        ["next_retry_at"],
        unique=False,
        postgresql_where=sa.text("status = 'pending'"),
    )
    op.create_index(
        "idx_webhook_deliveries_event_id", "webhook_deliveries", ["event_id"], unique=False
    )


def downgrade() -> None:
    op.drop_table("webhook_deliveries")
    op.drop_table("webhooks")

    # Drop enum type
    postgresql.ENUM(name="webhookdeliverystatus").drop(op.get_bind(), checkfirst=True)
