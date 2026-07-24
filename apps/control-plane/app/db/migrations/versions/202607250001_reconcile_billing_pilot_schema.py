"""Reconcile the 2026-02 billing-pilot schema into the OSS migration graph.

TR·edition/billing (#f75f04bb): a short enterprise-only billing pilot ran
2026-02-09/11 against this same prod database, applying schema changes from
migration files that lived only in relay-onprem-enterprise and were never
merged into this repo's history — the same class of gap already reconciled
once by 202602190001_stub_prod_head. Verified live against prod (read-only,
2026-07-24) before writing this: `users.casdoor_id`, `users.
billing_subscription_id`, the `billing_webhook_events` table, and all 5
`billing_*` auditaction enum labels already exist on prod exactly as defined
below. This migration is written to be safe on BOTH prod (everything already
present, guards make every statement a no-op) and a fresh dev/CI database
(nothing present, guards let every statement actually create it) — a blind
`pass` stub would leave fresh databases missing this schema entirely, which
the OSS-only `casdoor_id` raw-SQL consumers (listmonk_service.py) and the new
billing router both need.

`casdoor_id` predates the billing pilot (down_revision 202602030003 in the
original enterprise chain, i.e. applied first) but was never billing-specific
in itself — it is folded in here rather than as a separate migration because
both come from the same undocumented out-of-band deploy and both are needed
for a consistent revision graph.

Revision ID: 202607250001
Revises: 202607210003
Create Date: 2026-07-24 19:00:00.000000

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "202607250001"
down_revision: str | None = "202607210003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # users.casdoor_id — Casdoor OAuth subject ID. Already relied upon via
    # raw SQL by oauth_service.py/listmonk_service.py without an ORM column
    # or migration ever existing for it in this repo.
    op.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS casdoor_id VARCHAR(64)")
    op.execute("CREATE UNIQUE INDEX IF NOT EXISTS ix_users_casdoor_id ON users (casdoor_id)")

    # users.billing_subscription_id — Billing Service subscription ID.
    op.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS billing_subscription_id VARCHAR(255)")

    # billing_webhook_events — dedup record for processed Billing Service webhooks.
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS billing_webhook_events (
            id UUID PRIMARY KEY,
            event_id VARCHAR(255) NOT NULL,
            event_type VARCHAR(100) NOT NULL,
            processed_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS ix_billing_webhook_events_event_id "
        "ON billing_webhook_events (event_id)"
    )

    # auditaction enum — billing event labels. Must not be referenced by any
    # row/default in this same transaction (Postgres forbids using a
    # freshly-added enum value before the adding transaction commits) —
    # this migration only adds the labels, nothing consumes them here.
    op.execute("ALTER TYPE auditaction ADD VALUE IF NOT EXISTS 'billing_subscription_created'")
    op.execute("ALTER TYPE auditaction ADD VALUE IF NOT EXISTS 'billing_subscription_updated'")
    op.execute("ALTER TYPE auditaction ADD VALUE IF NOT EXISTS 'billing_subscription_cancelled'")
    op.execute("ALTER TYPE auditaction ADD VALUE IF NOT EXISTS 'billing_subscription_activated'")
    op.execute("ALTER TYPE auditaction ADD VALUE IF NOT EXISTS 'billing_payment_failed'")


def downgrade() -> None:
    # Postgres has no ALTER TYPE ... DROP VALUE — removing enum labels
    # requires recreating the type. Not attempted: harmless to leave unused
    # labels behind (same reasoning as 202607210003's downgrade).
    op.execute("DROP TABLE IF EXISTS billing_webhook_events")
    op.execute("ALTER TABLE users DROP COLUMN IF EXISTS billing_subscription_id")
    op.execute("ALTER TABLE users DROP COLUMN IF EXISTS casdoor_id")
