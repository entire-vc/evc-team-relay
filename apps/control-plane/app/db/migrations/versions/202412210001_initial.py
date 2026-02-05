"""initial schema for control plane"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "202412210001"
down_revision = None
branch_labels = None
depends_on = None

# Note: Enum types are created manually via op.execute() in upgrade()
# These references should not trigger automatic creation


def upgrade() -> None:
    # Create enums with explicit idempotency check using PostgreSQL DO block
    op.execute("""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'sharekind') THEN
                CREATE TYPE sharekind AS ENUM ('doc', 'folder');
            END IF;
        END$$;
    """)

    op.execute("""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'sharevisibility') THEN
                CREATE TYPE sharevisibility AS ENUM ('private', 'public', 'protected');
            END IF;
        END$$;
    """)

    op.execute("""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'sharememberrole') THEN
                CREATE TYPE sharememberrole AS ENUM ('viewer', 'editor');
            END IF;
        END$$;
    """)

    op.create_table(
        "users",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("password_hash", sa.String(length=255), nullable=False),
        sa.Column("is_admin", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("timezone('utc', now())"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("timezone('utc', now())"),
            server_onupdate=sa.text("timezone('utc', now())"),
        ),
        sa.UniqueConstraint("email"),
    )
    op.create_index("ix_users_email", "users", ["email"], unique=True)

    op.create_table(
        "shares",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column(
            "kind",
            postgresql.ENUM("doc", "folder", name="sharekind", create_type=False),
            nullable=False,
        ),
        sa.Column("path", sa.String(length=512), nullable=False),
        sa.Column(
            "visibility",
            postgresql.ENUM(
                "private", "public", "protected", name="sharevisibility", create_type=False
            ),
            nullable=False,
        ),
        sa.Column("password_hash", sa.String(length=255), nullable=True),
        sa.Column(
            "owner_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("timezone('utc', now())"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("timezone('utc', now())"),
            server_onupdate=sa.text("timezone('utc', now())"),
        ),
    )

    op.create_table(
        "share_members",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column(
            "share_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("shares.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "role",
            postgresql.ENUM("viewer", "editor", name="sharememberrole", create_type=False),
            nullable=False,
            server_default="viewer",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("timezone('utc', now())"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("timezone('utc', now())"),
            server_onupdate=sa.text("timezone('utc', now())"),
        ),
        sa.UniqueConstraint("share_id", "user_id", name="uq_share_member"),
    )


def downgrade() -> None:
    op.drop_table("share_members")
    op.drop_table("shares")
    op.drop_index("ix_users_email", table_name="users")
    op.drop_table("users")

    share_member_role_enum.drop(op.get_bind(), checkfirst=True)
    share_visibility_enum.drop(op.get_bind(), checkfirst=True)
    share_kind_enum.drop(op.get_bind(), checkfirst=True)
