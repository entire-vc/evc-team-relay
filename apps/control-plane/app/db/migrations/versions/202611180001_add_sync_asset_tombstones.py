"""add sync_asset_tombstones for sync DELETE

Expand-only: a new table, no change to existing ones. Old code ignores it.
A row marks a path removed from a share's index by sync DELETE so the public
asset route stops serving the object left in storage.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "202611180001"
down_revision = "202610210001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "sync_asset_tombstones",
        sa.Column(
            "share_id",
            postgresql.UUID(as_uuid=True).with_variant(sa.Uuid(), "sqlite"),
            sa.ForeignKey("shares.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("path", sa.String(length=1024), primary_key=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("sync_asset_tombstones")
