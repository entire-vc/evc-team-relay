"""Add partial index on share_agent_keys(share_id) WHERE revoked_at IS NULL.

Revision ID: 202606010001
Revises: 202605220001
Create Date: 2026-06-01 00:00:00.000000

"""

from typing import Sequence, Union

from alembic import op

revision: str = "202606010001"
down_revision: Union[str, None] = "202605220001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_share_agent_keys_share_id_active
        ON share_agent_keys (share_id)
        WHERE revoked_at IS NULL
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_share_agent_keys_share_id_active")
