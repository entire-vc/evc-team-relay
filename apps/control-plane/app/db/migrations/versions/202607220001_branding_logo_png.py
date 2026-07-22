"""Switch default branding logo_url from SVG to PNG (TR-62)

Twitter/X and most social-card crawlers don't render SVG for og:image /
twitter:image, so a share of a web-published page rendered a broken card.
favicon_url is untouched — SVG favicons render fine in browsers and that
surface isn't scraped by social crawlers.

Only updates the row if it still holds the original default SVG path, so an
instance that has since set a custom logo is left alone.

Revision ID: 202607220001
Revises: 202607210001
Create Date: 2026-07-22 00:00:00.000000

"""

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "202607220001"
down_revision: Union[str, None] = "202607210001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

OLD_DEFAULT = "/static/img/evc-ava.svg"
NEW_DEFAULT = "/static/img/evc-ava.png"


def upgrade() -> None:
    op.execute(
        f"""
        UPDATE instance_settings
        SET value = '{NEW_DEFAULT}'
        WHERE key = 'branding_logo_url' AND value = '{OLD_DEFAULT}'
        """
    )


def downgrade() -> None:
    op.execute(
        f"""
        UPDATE instance_settings
        SET value = '{OLD_DEFAULT}'
        WHERE key = 'branding_logo_url' AND value = '{NEW_DEFAULT}'
        """
    )
