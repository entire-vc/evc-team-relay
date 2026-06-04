"""Backfill NULL labels in share_agent_keys with 'legacy-<hash_prefix>'.

Revision ID: 202606040001
Revises: 202606010001
Create Date: 2026-06-04 00:00:00.000000

"""

from typing import Sequence, Union

from alembic import op

revision: str = "202606040001"
down_revision: Union[str, None] = "202606010001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        UPDATE share_agent_keys
        SET label = 'legacy-' || left(key_hash, 6)
        WHERE label IS NULL
        """
    )


def downgrade() -> None:
    # Backfill is intentionally non-reversible — labels set here are cosmetic
    pass
