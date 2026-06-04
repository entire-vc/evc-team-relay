"""Stub migration to restore the revision graph.

Revision ID: 202602190001
Revises: 202602030003
Create Date: 2026-02-19 00:00:00.000000

This revision was applied to the production database before the
share_agent_keys migration (202605220001) was written. The migration file
was never committed to the repository. The schema changes it made are
already present in the production database; this stub exists solely to
make alembic's revision graph consistent so that `alembic upgrade head`
can traverse the chain.

No upgrade or downgrade actions — prod schema already reflects this state.
"""

from typing import Sequence, Union

from alembic import op

revision: str = "202602190001"
down_revision: Union[str, None] = "202602030003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
