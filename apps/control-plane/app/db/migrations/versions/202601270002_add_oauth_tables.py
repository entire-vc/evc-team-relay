"""add oauth tables for OAuth/OIDC authentication"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "202601270002"
down_revision = "202601270001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Create OAuth provider type enum (idempotent)
    op.execute("""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'oauthprovidertype') THEN
                CREATE TYPE oauthprovidertype AS ENUM ('oidc', 'oauth2');
            END IF;
        END $$;
    """)

    # Add new audit actions to the enum
    op.execute("""
        ALTER TYPE auditaction ADD VALUE IF NOT EXISTS 'oauth_login';
        ALTER TYPE auditaction ADD VALUE IF NOT EXISTS 'oauth_account_linked';
        ALTER TYPE auditaction ADD VALUE IF NOT EXISTS 'oauth_account_unlinked';
    """)

    # Create oauth_providers table
    op.create_table(
        "oauth_providers",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("name", sa.String(50), unique=True, nullable=False),
        sa.Column(
            "provider_type",
            postgresql.ENUM("oidc", "oauth2", name="oauthprovidertype", create_type=False),
            nullable=False,
            server_default="oidc",
        ),
        sa.Column("issuer_url", sa.String(500), nullable=False),
        sa.Column("client_id", sa.String(255), nullable=False),
        sa.Column("client_secret_encrypted", sa.String(500), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("auto_register", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("timezone('utc', now())"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("timezone('utc', now())"),
            nullable=False,
        ),
    )

    # Create indexes for oauth_providers
    op.create_index("ix_oauth_providers_name", "oauth_providers", ["name"], unique=True)

    # Create user_oauth_accounts table
    op.create_table(
        "user_oauth_accounts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "provider_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("oauth_providers.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("provider_user_id", sa.String(255), nullable=False),
        sa.Column("email", sa.String(320), nullable=False),
        sa.Column("name", sa.String(255), nullable=True),
        sa.Column("picture_url", sa.String(500), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("timezone('utc', now())"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("timezone('utc', now())"),
            nullable=False,
        ),
    )

    # Create indexes for user_oauth_accounts
    op.create_index("ix_user_oauth_accounts_user_id", "user_oauth_accounts", ["user_id"])
    op.create_index("ix_user_oauth_accounts_provider_id", "user_oauth_accounts", ["provider_id"])
    op.create_index(
        "ix_user_oauth_accounts_provider_user_id", "user_oauth_accounts", ["provider_user_id"]
    )

    # Create unique constraint for provider_id + provider_user_id
    op.create_unique_constraint(
        "uq_provider_user", "user_oauth_accounts", ["provider_id", "provider_user_id"]
    )


def downgrade() -> None:
    op.drop_constraint("uq_provider_user", "user_oauth_accounts", type_="unique")
    op.drop_index("ix_user_oauth_accounts_provider_user_id", table_name="user_oauth_accounts")
    op.drop_index("ix_user_oauth_accounts_provider_id", table_name="user_oauth_accounts")
    op.drop_index("ix_user_oauth_accounts_user_id", table_name="user_oauth_accounts")
    op.drop_table("user_oauth_accounts")

    op.drop_index("ix_oauth_providers_name", table_name="oauth_providers")
    op.drop_table("oauth_providers")

    op.execute("DROP TYPE oauthprovidertype;")

    # Note: Cannot remove enum values from auditaction in PostgreSQL
