"""Add web_sync_mode to shares

Revision ID: 202602030002
Revises: 202602030001
Create Date: 2026-02-03 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "202602030002"
down_revision: Union[str, None] = "202602030001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "shares",
        sa.Column("web_sync_mode", sa.String(10), server_default="manual", nullable=False),
    )


def downgrade() -> None:
    op.drop_column("shares", "web_sync_mode")
