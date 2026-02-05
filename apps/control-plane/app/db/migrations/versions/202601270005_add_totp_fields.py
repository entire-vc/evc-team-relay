"""add TOTP 2FA fields to users table"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "202601270005"
down_revision = "202601270004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add new audit actions to the enum (idempotent)
    op.execute("""
        ALTER TYPE auditaction ADD VALUE IF NOT EXISTS 'totp_enabled';
        ALTER TYPE auditaction ADD VALUE IF NOT EXISTS 'totp_disabled';
        ALTER TYPE auditaction ADD VALUE IF NOT EXISTS 'totp_backup_used';
    """)

    # Add TOTP fields to users table
    op.add_column(
        "users",
        sa.Column("totp_secret_encrypted", sa.String(255), nullable=True),
    )
    op.add_column(
        "users",
        sa.Column("totp_enabled", sa.Boolean(), nullable=False, server_default="false"),
    )
    op.add_column(
        "users",
        sa.Column("backup_codes_encrypted", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("users", "backup_codes_encrypted")
    op.drop_column("users", "totp_enabled")
    op.drop_column("users", "totp_secret_encrypted")
    # Note: Cannot remove enum values from auditaction in PostgreSQL
