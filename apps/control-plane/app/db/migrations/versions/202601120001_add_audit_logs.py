"""add audit logs table"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "202601120001"
down_revision = "202412210001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Create action enum for audit logs
    op.execute("""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'auditaction') THEN
                CREATE TYPE auditaction AS ENUM (
                    'user_created',
                    'user_updated',
                    'user_deleted',
                    'user_login',
                    'user_logout',
                    'share_created',
                    'share_updated',
                    'share_deleted',
                    'share_member_added',
                    'share_member_updated',
                    'share_member_removed',
                    'token_issued'
                );
            END IF;
        END$$;
    """)

    op.create_table(
        "audit_logs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column(
            "timestamp",
            sa.DateTime(timezone=True),
            server_default=sa.text("timezone('utc', now())"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "action",
            postgresql.ENUM(
                "user_created",
                "user_updated",
                "user_deleted",
                "user_login",
                "user_logout",
                "share_created",
                "share_updated",
                "share_deleted",
                "share_member_added",
                "share_member_updated",
                "share_member_removed",
                "token_issued",
                name="auditaction",
                create_type=False,
            ),
            nullable=False,
        ),
        sa.Column(
            "actor_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "target_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "target_share_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("shares.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("details", postgresql.JSONB, nullable=True),
        sa.Column("ip_address", sa.String(45), nullable=True),
        sa.Column("user_agent", sa.String(512), nullable=True),
    )
    op.create_index("ix_audit_logs_actor_user_id", "audit_logs", ["actor_user_id"])
    op.create_index("ix_audit_logs_action", "audit_logs", ["action"])


def downgrade() -> None:
    op.drop_index("ix_audit_logs_action", table_name="audit_logs")
    op.drop_index("ix_audit_logs_actor_user_id", table_name="audit_logs")
    op.drop_table("audit_logs")
    op.execute("DROP TYPE IF EXISTS auditaction")
