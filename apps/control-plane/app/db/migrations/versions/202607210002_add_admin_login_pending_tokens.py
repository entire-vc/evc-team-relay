"""Add admin_login_pending_tokens table for the admin-ui 2FA login step.

TR-06 (#fceefc4f): /admin-ui/login used to issue the full admin_token cookie
on password alone, never checking totp_enabled. This table holds the
short-lived, single-use "password verified, TOTP still required" handle
between the two admin-ui login steps — see AdminLoginPendingToken's
docstring in app/db/models.py for why this is a separate table rather than
a JWT.

Revision ID: 202607210002
Revises: 202607210001
Create Date: 2026-07-21 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "202607210002"
down_revision: Union[str, None] = "202607210001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "admin_login_pending_tokens",
        sa.Column("id", postgresql.UUID(as_uuid=False), primary_key=True, nullable=False),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=False),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False, index=True),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_admin_login_pending_tokens_token_hash",
        "admin_login_pending_tokens",
        ["token_hash"],
        if_not_exists=True,
    )
    op.create_index(
        "ix_admin_login_pending_tokens_user_id",
        "admin_login_pending_tokens",
        ["user_id"],
        if_not_exists=True,
    )
    op.create_index(
        "ix_admin_login_pending_tokens_expires_at",
        "admin_login_pending_tokens",
        ["expires_at"],
        if_not_exists=True,
    )


def downgrade() -> None:
    table = "admin_login_pending_tokens"
    op.drop_index("ix_admin_login_pending_tokens_token_hash", table_name=table)
    op.drop_index("ix_admin_login_pending_tokens_user_id", table_name=table)
    op.drop_index("ix_admin_login_pending_tokens_expires_at", table_name=table)
    op.drop_table(table)
