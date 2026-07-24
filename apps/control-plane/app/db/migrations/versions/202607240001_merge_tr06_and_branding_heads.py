"""Merge the TR-06 admin_login_pending_tokens head with the branding-logo head.

Two revisions both descend from 202607210001, creating two alembic heads:

  * 202607210002 (TR-06, #113) — add admin_login_pending_tokens
  * 202607220002 (branding-logo absolute-URL, #141) — currently the deployed
    DB head

TR-06's migration was authored off a base that predated the branding-logo
migrations, so when #113 merged it branched the graph. `alembic upgrade head`
(singular, as the control-plane-migrate service runs) fails on multiple heads.
This no-op merge unifies them back to a single head so the standard deploy
applies 202607210002 cleanly on top of the current 202607220002 DB state.

Revision ID: 202607240001
Revises: 202607210002, 202607220002
Create Date: 2026-07-24 00:00:00.000000

"""

from typing import Sequence, Union

revision: str = "202607240001"
down_revision: Union[str, Sequence[str], None] = ("202607210002", "202607220002")
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
