"""Add web_doc_id field for real-time sync.

Revision ID: 202602020004
Revises: 202602020003
Create Date: 2026-02-02 21:00:00

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "202602020004"
down_revision: Union[str, None] = "202602020003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add web_doc_id column for y-sweet document ID."""
    op.add_column("shares", sa.Column("web_doc_id", sa.String(512), nullable=True))


def downgrade() -> None:
    """Remove web_doc_id column."""
    op.drop_column("shares", "web_doc_id")
