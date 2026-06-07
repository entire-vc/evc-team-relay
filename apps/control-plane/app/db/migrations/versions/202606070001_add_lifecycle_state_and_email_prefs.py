"""Add lifecycle_state table and lifecycle_emails pref.

Revision ID: 202606070001
Revises: 202606040001
Create Date: 2026-06-07 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "202606070001"
down_revision: Union[str, None] = "202606040001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # lifecycle_state — idempotency-keyed trigger tracker for the email lifecycle engine.
    # unique(user_id, trigger_key) is the core idempotency constraint; the worker uses it
    # to upsert state without ever double-sending for a given (user, trigger) pair.
    op.create_table(
        "lifecycle_state",
        sa.Column(
            "id",
            sa.UUID(),
            nullable=False,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("trigger_key", sa.String(length=64), nullable=False),
        sa.Column(
            "state",
            sa.String(length=32),
            nullable=False,
            server_default=sa.text("'pending'"),
        ),
        sa.Column("scheduled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("suppressed_reason", sa.Text(), nullable=True),
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
        sa.UniqueConstraint("user_id", "trigger_key", name="uq_lifecycle_state_user_trigger"),
    )
    op.create_index("idx_lifecycle_state_user_id", "lifecycle_state", ["user_id"], unique=False)
    op.create_index(
        "idx_lifecycle_state_scheduled",
        "lifecycle_state",
        ["scheduled_at"],
        unique=False,
        postgresql_where=sa.text("state = 'pending'"),
    )

    # lifecycle_emails opt-out: missing row is treated as all-on (DEFAULT true).
    # Current DB has 0 rows in user_email_preferences, so no backfill needed.
    op.add_column(
        "user_email_preferences",
        sa.Column(
            "lifecycle_emails",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
    )


def downgrade() -> None:
    op.drop_column("user_email_preferences", "lifecycle_emails")
    op.drop_index("idx_lifecycle_state_scheduled", table_name="lifecycle_state")
    op.drop_index("idx_lifecycle_state_user_id", table_name="lifecycle_state")
    op.drop_table("lifecycle_state")
