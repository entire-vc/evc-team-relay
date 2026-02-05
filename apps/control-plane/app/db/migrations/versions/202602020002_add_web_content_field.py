"""Add web_content field to shares for document content storage.

Revision ID: 202602020002
Revises: 202602020001
Create Date: 2026-02-02 18:30:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "202602020002"
down_revision: str | None = "202602020001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Add web_content column for storing document markdown content
    op.add_column(
        "shares",
        sa.Column("web_content", sa.Text(), nullable=True),
    )
    # Add web_content_updated_at for tracking content freshness
    op.add_column(
        "shares",
        sa.Column(
            "web_content_updated_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("shares", "web_content_updated_at")
    op.drop_column("shares", "web_content")
