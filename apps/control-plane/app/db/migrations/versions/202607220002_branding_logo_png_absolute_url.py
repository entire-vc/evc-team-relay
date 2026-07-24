"""Fix branding_logo_url PNG switch for instances storing an absolute URL

Follow-up to 202607220001, which only matched the relative-path default
(`/static/img/evc-ava.svg`) and silently matched zero rows on prod — prod's
stored value is `https://cp.tr.entire.vc/static/img/evc-ava.svg` (absolute,
presumably set via an earlier admin-branding-settings write). Use a
suffix-preserving REPLACE so it works whether the stored value is relative,
absolute, or on any other domain — only rows still pointing at the default
evc-ava.svg asset are touched, any genuinely custom logo is left alone.

Revision ID: 202607220002
Revises: 202607220001
Create Date: 2026-07-22 00:00:01.000000

"""

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "202607220002"
down_revision: Union[str, None] = "202607220001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

OLD_SUFFIX = "/static/img/evc-ava.svg"
NEW_SUFFIX = "/static/img/evc-ava.png"


def upgrade() -> None:
    op.execute(
        f"""
        UPDATE instance_settings
        SET value = REPLACE(value, '{OLD_SUFFIX}', '{NEW_SUFFIX}')
        WHERE key = 'branding_logo_url' AND value LIKE '%{OLD_SUFFIX}'
        """
    )


def downgrade() -> None:
    op.execute(
        f"""
        UPDATE instance_settings
        SET value = REPLACE(value, '{NEW_SUFFIX}', '{OLD_SUFFIX}')
        WHERE key = 'branding_logo_url' AND value LIKE '%{NEW_SUFFIX}'
        """
    )
