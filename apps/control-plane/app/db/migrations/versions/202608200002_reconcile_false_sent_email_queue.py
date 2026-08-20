"""Reconcile pre-fix email_queue rows falsely marked 'sent'.

Revision ID: 202608200002
Revises: 202607250001
Create Date: 2026-08-20 00:00:00.000000

Between 2026-07-10 (Helsinki migration) and 2026-08-20 15:53 UTC (EMAIL_ENABLED
flipped back on, task ac65cfe5), the queue worker marked every row 'sent' the
moment it was claimed, regardless of whether SMTP was even configured — fixed
in #193, which changed that path to release an unsent claim back to PENDING
instead. That fix does not touch rows that were ALREADY marked 'sent' before it
shipped; those are stuck lying about delivery until reconciled here.

Policy (task ac65cfe5, agreed by Bill/Daedalus 2026-08-20): do NOT bulk-resend.
The affected rows are 1-40+ days stale by the time this runs; resending e.g. a
month-old "new login detected" or "you haven't created a share in 24h" reads as
spam from the product, not a fix, and the real-world harm from the missed send
already happened and isn't undone by a late copy. Mark them honestly instead.

`failed` is the safe terminal state for this: email_service.py's claim query
(`_claim_pending_emails`) only ever re-picks status='pending' (next_retry_at <=
now) or a lease-expired 'sending' row — never 'failed' — so this cannot trigger
an accidental resend now or on any future worker restart.

Scope is a fixed cutoff (the exact moment EMAIL_ENABLED was flipped back on),
not "all sent rows ever" — anything sent after that moment is a real send this
migration must not touch.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
from sqlalchemy import text

revision: str = "202608200002"
down_revision: str | None = "202607250001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# The exact moment control-plane/email-worker/webhook-worker/lifecycle-worker
# were recreated with EMAIL_ENABLED=true + real SMTP config live (task
# ac65cfe5) -- anything marked 'sent' before this was marked so by the old,
# buggy claim path and never actually delivered; anything at/after is real.
CUTOFF = "2026-08-20 15:53:00+00"

RECONCILIATION_NOTE = (
    "Reconciled 2026-08-20 (task ac65cfe5): EMAIL_ENABLED=false at send time "
    "(Helsinki migration gap, 2026-07-10..2026-08-20) -- never actually "
    "delivered despite status=sent. Not resent by policy; see task history."
)


def upgrade() -> None:
    conn = op.get_bind()
    result = conn.execute(
        text(
            """
            UPDATE email_queue
            SET status = 'failed',
                error_message = :note
            WHERE status = 'sent' AND created_at < :cutoff
            """
        ),
        {"note": RECONCILIATION_NOTE, "cutoff": CUTOFF},
    )
    print(f"reconciled {result.rowcount} falsely-sent email_queue row(s)")


def downgrade() -> None:
    # Non-reversible by design: this only ever touches rows this exact
    # migration marked, but nothing distinguishes them from a 'failed' row
    # a genuine SMTP error produced later -- reverting by error_message match
    # would be unsafe if that text is ever reused elsewhere.
    pass
