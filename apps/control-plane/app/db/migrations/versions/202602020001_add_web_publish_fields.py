"""add web publishing fields to shares table"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "202602020001"
down_revision = "b53f9d8e99a5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add web publishing fields to shares table
    op.add_column(
        "shares",
        sa.Column("web_published", sa.Boolean(), nullable=False, server_default="false"),
    )
    op.add_column(
        "shares",
        sa.Column("web_slug", sa.String(255), nullable=True),
    )
    op.add_column(
        "shares",
        sa.Column("web_noindex", sa.Boolean(), nullable=False, server_default="true"),
    )

    # Add unique constraint on web_slug
    op.create_unique_constraint("uq_shares_web_slug", "shares", ["web_slug"])

    # Add index on web_slug for fast lookups (partial index where not null)
    op.execute("CREATE INDEX idx_shares_web_slug ON shares(web_slug) WHERE web_slug IS NOT NULL")


def downgrade() -> None:
    # Drop index and constraint first
    op.execute("DROP INDEX IF EXISTS idx_shares_web_slug")
    op.drop_constraint("uq_shares_web_slug", "shares", type_="unique")

    # Drop columns
    op.drop_column("shares", "web_noindex")
    op.drop_column("shares", "web_slug")
    op.drop_column("shares", "web_published")
