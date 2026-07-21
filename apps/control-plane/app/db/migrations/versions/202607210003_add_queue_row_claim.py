"""Add row-claim support (SENDING status + claimed_at) to email/webhook queues.

TR-11 (#8a509876): root cause of the 08-09.07 duplicate-email incident —
two worker instances could read and process the same PENDING row (split
brain), and a SIGKILL between sending and committing left the row PENDING
forever poised to resend. This migration adds the schema for a
claim-before-send pattern (SELECT ... FOR UPDATE SKIP LOCKED, flip to
SENDING before the send attempt, with a lease timeout for crash recovery) —
see email_service.claim_pending_emails / webhook_service.claim_pending_deliveries.

Revision ID: 202607210003
Revises: 202607210001
Create Date: 2026-07-21 17:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "202607210003"
down_revision: str | None = "202607210001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # New in-flight/claimed status on both queue enums. Must not be used
    # (e.g. in a server_default or UPDATE) in this same transaction —
    # Postgres forbids using a freshly-added enum value before the
    # transaction that added it commits.
    op.execute("ALTER TYPE emailstatus ADD VALUE IF NOT EXISTS 'sending'")
    op.execute("ALTER TYPE webhookdeliverystatus ADD VALUE IF NOT EXISTS 'sending'")

    op.add_column(
        "email_queue",
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "webhook_deliveries",
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("webhook_deliveries", "claimed_at")
    op.drop_column("email_queue", "claimed_at")

    # Postgres has no ALTER TYPE ... DROP VALUE — removing an enum value
    # requires recreating the type. Not attempted here: no row can be left
    # in 'sending' by the time a downgrade runs (claim_pending_* always
    # transitions it back out before any code path returns), so leaving the
    # unused enum label behind is harmless.
