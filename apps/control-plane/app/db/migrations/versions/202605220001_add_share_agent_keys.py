"""Add share_agent_keys table for Mesh artifact upload auth.

Revision ID: 202605220001
Revises: 202602030003
Create Date: 2026-05-22 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "202605220001"
down_revision: Union[str, None] = "202602190001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "share_agent_keys",
        sa.Column("id", postgresql.UUID(as_uuid=False), primary_key=True, nullable=False),
        sa.Column(
            "share_id",
            postgresql.UUID(as_uuid=False),
            sa.ForeignKey("shares.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("key_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("label", sa.String(255), nullable=True),
        sa.Column("scopes", sa.String(255), nullable=False, server_default="write"),
        sa.Column(
            "created_by",
            postgresql.UUID(as_uuid=False),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_share_agent_keys_key_hash",
        "share_agent_keys",
        ["key_hash"],
        if_not_exists=True,
    )
    op.create_index(
        "ix_share_agent_keys_share_id",
        "share_agent_keys",
        ["share_id"],
        if_not_exists=True,
    )


def downgrade() -> None:
    op.drop_index("ix_share_agent_keys_key_hash", table_name="share_agent_keys")
    op.drop_index("ix_share_agent_keys_share_id", table_name="share_agent_keys")
    op.drop_table("share_agent_keys")
