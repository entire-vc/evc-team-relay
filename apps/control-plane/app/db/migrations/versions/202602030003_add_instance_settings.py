"""Add instance_settings table

Revision ID: 202602030003
Revises: 202602030002
Create Date: 2026-02-03 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "202602030003"
down_revision: Union[str, None] = "202602030002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "instance_settings",
        sa.Column("key", sa.String(64), primary_key=True, nullable=False),
        sa.Column("value", sa.Text(), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )

    # Insert default branding values
    op.execute(
        """
        INSERT INTO instance_settings (key, value) VALUES
        ('branding_name', 'Relay Server'),
        ('branding_logo_url', '/static/img/evc-ava.svg'),
        ('branding_favicon_url', '/static/img/evc-ava.svg')
        """
    )


def downgrade() -> None:
    op.drop_table("instance_settings")
