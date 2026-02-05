"""increase_picture_url_column_size

Revision ID: b53f9d8e99a5
Revises: 202601270007
Create Date: 2026-02-01

"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "b53f9d8e99a5"
down_revision = "202601270007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Change picture_url from VARCHAR(500) to TEXT to handle long Google profile URLs
    op.alter_column(
        "user_oauth_accounts",
        "picture_url",
        existing_type=sa.String(500),
        type_=sa.Text(),
        existing_nullable=True,
    )


def downgrade() -> None:
    # Revert to VARCHAR(500) - note: this may truncate existing data
    op.alter_column(
        "user_oauth_accounts",
        "picture_url",
        existing_type=sa.Text(),
        type_=sa.String(500),
        existing_nullable=True,
    )
