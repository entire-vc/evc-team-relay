"""Change web_folder_items column type from JSON to JSONB.

PostgreSQL JSON type doesn't support equality operators, which breaks
DISTINCT queries. JSONB supports comparison operators and is also more
efficient for querying.

Revision ID: 202602030001
Revises: 202602020004
Create Date: 2026-02-03 08:00:00.000000

"""

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "202602030001"
down_revision: Union[str, None] = "202602020004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Change column type from JSON to JSONB using PostgreSQL's USING clause
    op.execute(
        "ALTER TABLE shares ALTER COLUMN web_folder_items TYPE JSONB USING web_folder_items::jsonb"
    )


def downgrade() -> None:
    # Revert back to JSON type
    op.execute(
        "ALTER TABLE shares ALTER COLUMN web_folder_items TYPE JSON USING web_folder_items::json"
    )
