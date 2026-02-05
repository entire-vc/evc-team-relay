"""Add web_folder_items field to shares for folder content listing.

Revision ID: 202602020003
Revises: 202602020002
Create Date: 2026-02-02 19:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "202602020003"
down_revision: str | None = "202602020002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Add web_folder_items column for storing folder file listing as JSON
    # Format: [{"path": "file.md", "name": "file", "type": "doc"}, ...]
    op.add_column(
        "shares",
        sa.Column("web_folder_items", sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("shares", "web_folder_items")
