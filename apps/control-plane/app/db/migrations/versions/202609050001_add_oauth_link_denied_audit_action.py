"""add oauth_account_link_denied audit action"""

from __future__ import annotations

from alembic import op

revision = "202609050001"
down_revision = "202608260001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        ALTER TYPE auditaction ADD VALUE IF NOT EXISTS 'oauth_account_link_denied';
    """)


def downgrade() -> None:
    # Note: Cannot remove enum values from auditaction in PostgreSQL
    pass
