"""Reconcile model/schema drift accumulated since 202608200003.

Team Relay CI (`check-migration-drift`, `alembic check`) has been red on
`main` since before this migration existed, masked by `allow_failure: true`
on the GitLab job — see the task this migration closes. `alembic autogenerate`
found ~50 operations; this migration applies only the ones that are actually
safe and correct. It deliberately does NOT apply every operation autogenerate
proposed — six indexes it wanted to drop turned out to be live, deliberately
partial indexes matching real hot-path queries (row-claim workers, active-
webhook/agent-key lookups, the public web-publish slug lookup); those were
fixed by describing them explicitly in the SQLAlchemy models instead
(`app/db/models.py`, `Index(..., postgresql_where=...)` in `__table_args__`),
so they now round-trip with zero diff and this migration never touches them.

What IS in this migration, grouped by why it's safe:

1. Unique-constraint consolidation (8 tables). Each of these columns has had
   TWO separate DB objects enforcing the same uniqueness invariant since an
   early migration: a table-level UNIQUE CONSTRAINT (Postgres auto-named
   `<table>_<col>_key`) AND a separately-created index. For 3 of them
   (admin_login_pending_tokens.token_hash, email_verification_tokens.
   token_hash, share_agent_keys.key_hash) the second object is a *non-unique*
   named index (`ix_...`) that duplicates the lookup the constraint's own
   backing index already does; for the other 5 (oauth_providers.name,
   password_reset_tokens.token_hash, share_invites.token, user_sessions.
   refresh_token_hash, users.email) the second object is already a *unique*
   named index that fully replaces the constraint. Either way: drop the
   redundant `_key` constraint, and where the surviving index was non-unique,
   flip it to `unique=True`. Uniqueness stays enforced throughout — verified
   by reading each column's original CREATE migration before touching it,
   not just trusting the diff. Fewer duplicate indexes to maintain on writes
   is a minor bonus, not the point.

2. Index renames, idx_* -> ix_* (9 indexes, all plain non-unique, same
   column, no WHERE clause). Purely a naming-convention alignment with
   SQLAlchemy's autogenerate default — the column being indexed and the rows
   covered are unchanged.

3. Drop `idx_lifecycle_state_scheduled`. The only column in this migration
   dropped outright, not renamed. `lifecycle_state.scheduled_at` is written
   but never read back by any query in this codebase (checked
   app/services/lifecycle_service.py and app/workers/lifecycle_worker.py,
   which iterate users and consult `state`/`user_id`/`trigger_key` only) —
   unlike the four other originally-flagged "removed, no replacement"
   indexes (email_queue.status, webhook_deliveries.status, webhooks.active,
   shares.web_slug, share_agent_keys' partial share_id index), which turned
   out to be genuinely queried and were fixed in the model instead of
   dropped here. This one really is dead weight.

4. NOT NULL tightening on 3 tables' created_at/updated_at (6 columns). These
   have always effectively been non-null in practice (SQLAlchemy populates
   both via server_default on every insert, and the ORM model already
   declared `nullable=False` in TimestampMixin) — the DB just never enforced
   it, because the original migration didn't say NOT NULL explicitly.
   Verified zero existing NULLs on prod (read-only) before writing this:
   users/shares/share_members all 0 across both columns.

5. Two real (non-cosmetic) type changes that ADD a length cap where none
   existed (TEXT -> VARCHAR(N)), matching what the model has always
   declared: users.backup_codes_encrypted -> String(2000), webhook_
   deliveries.response_body -> String(1024). Verified safe against prod
   (read-only) before writing this: backup_codes_encrypted max length in use
   is 0 (column is currently empty), response_body max length in use is 35 —
   both far under the new caps.

   The other three TEXT -> String() type changes in this migration
   (email_queue.body_text/body_html/error_message) are NOT length caps —
   the model declares bare `String` with no length, which Postgres stores
   identically to TEXT (unbounded VARCHAR). Pure type-label cosmetics,
   included for completeness so `alembic check` has nothing left to flag.

Revision ID: 202608260001
Revises: 202608200003
Create Date: 2026-08-26 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "202608260001"
down_revision: str | None = "202608200003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # --- 1. Unique-constraint consolidation ---------------------------------
    # Paired case: redundant non-unique index promoted to unique, `_key`
    # constraint (and its own backing index) dropped.
    op.drop_constraint(
        "admin_login_pending_tokens_token_hash_key",
        "admin_login_pending_tokens",
        type_="unique",
    )
    op.drop_index(
        "ix_admin_login_pending_tokens_token_hash", table_name="admin_login_pending_tokens"
    )
    op.create_index(
        "ix_admin_login_pending_tokens_token_hash",
        "admin_login_pending_tokens",
        ["token_hash"],
        unique=True,
    )

    op.drop_constraint(
        "email_verification_tokens_token_hash_key", "email_verification_tokens", type_="unique"
    )
    op.drop_index("ix_email_verification_tokens_token_hash", table_name="email_verification_tokens")
    op.create_index(
        "ix_email_verification_tokens_token_hash",
        "email_verification_tokens",
        ["token_hash"],
        unique=True,
    )

    op.drop_constraint("share_agent_keys_key_hash_key", "share_agent_keys", type_="unique")
    op.drop_index("ix_share_agent_keys_key_hash", table_name="share_agent_keys")
    op.create_index("ix_share_agent_keys_key_hash", "share_agent_keys", ["key_hash"], unique=True)

    # Bare case: the surviving index was already unique — just drop the
    # redundant constraint.
    op.drop_constraint("oauth_providers_name_key", "oauth_providers", type_="unique")
    op.drop_constraint(
        "password_reset_tokens_token_hash_key", "password_reset_tokens", type_="unique"
    )
    op.drop_constraint("share_invites_token_key", "share_invites", type_="unique")
    op.drop_constraint("user_sessions_refresh_token_hash_key", "user_sessions", type_="unique")
    op.drop_constraint("users_email_key", "users", type_="unique")

    # --- 2. Index renames, idx_* -> ix_* -------------------------------------
    op.drop_index("idx_email_queue_email_type", table_name="email_queue")
    op.create_index("ix_email_queue_email_type", "email_queue", ["email_type"], unique=False)
    op.drop_index("idx_email_queue_status", table_name="email_queue")
    op.create_index("ix_email_queue_status", "email_queue", ["status"], unique=False)

    op.drop_index("idx_lifecycle_state_user_id", table_name="lifecycle_state")
    op.create_index("ix_lifecycle_state_user_id", "lifecycle_state", ["user_id"], unique=False)

    op.drop_index("idx_share_invites_email", table_name="share_invites")
    op.create_index("ix_share_invites_email", "share_invites", ["email"], unique=False)

    op.drop_index("idx_webhook_deliveries_event_id", table_name="webhook_deliveries")
    op.create_index(
        "ix_webhook_deliveries_event_id", "webhook_deliveries", ["event_id"], unique=False
    )
    op.drop_index("idx_webhook_deliveries_status", table_name="webhook_deliveries")
    op.create_index("ix_webhook_deliveries_status", "webhook_deliveries", ["status"], unique=False)
    op.drop_index("idx_webhook_deliveries_webhook_id", table_name="webhook_deliveries")
    op.create_index(
        "ix_webhook_deliveries_webhook_id", "webhook_deliveries", ["webhook_id"], unique=False
    )

    op.drop_index("idx_webhooks_user_id", table_name="webhooks")
    op.create_index("ix_webhooks_user_id", "webhooks", ["user_id"], unique=False)

    # --- 3. Drop the one genuinely-unused index ------------------------------
    op.drop_index(
        "idx_lifecycle_state_scheduled",
        table_name="lifecycle_state",
        postgresql_where=sa.text("(state = 'pending')"),
    )

    # --- 4. NOT NULL tightening (verified zero NULLs on prod first) ---------
    op.alter_column(
        "share_members", "created_at", existing_type=sa.DateTime(timezone=True), nullable=False
    )
    op.alter_column(
        "share_members", "updated_at", existing_type=sa.DateTime(timezone=True), nullable=False
    )
    op.alter_column(
        "shares", "created_at", existing_type=sa.DateTime(timezone=True), nullable=False
    )
    op.alter_column(
        "shares", "updated_at", existing_type=sa.DateTime(timezone=True), nullable=False
    )
    op.alter_column("users", "created_at", existing_type=sa.DateTime(timezone=True), nullable=False)
    op.alter_column("users", "updated_at", existing_type=sa.DateTime(timezone=True), nullable=False)

    # --- 5. Type changes ------------------------------------------------------
    op.alter_column(
        "email_queue",
        "body_text",
        existing_type=sa.TEXT(),
        type_=sa.String(),
        existing_nullable=False,
    )
    op.alter_column(
        "email_queue",
        "body_html",
        existing_type=sa.TEXT(),
        type_=sa.String(),
        existing_nullable=False,
    )
    op.alter_column(
        "email_queue",
        "error_message",
        existing_type=sa.TEXT(),
        type_=sa.String(),
        existing_nullable=True,
    )
    # Real length caps — verified against prod data first (see docstring).
    op.alter_column(
        "users",
        "backup_codes_encrypted",
        existing_type=sa.TEXT(),
        type_=sa.String(length=2000),
        existing_nullable=True,
    )
    op.alter_column(
        "webhook_deliveries",
        "response_body",
        existing_type=sa.TEXT(),
        type_=sa.String(length=1024),
        existing_nullable=True,
    )


def downgrade() -> None:
    op.alter_column(
        "webhook_deliveries",
        "response_body",
        existing_type=sa.String(length=1024),
        type_=sa.TEXT(),
        existing_nullable=True,
    )
    op.alter_column(
        "users",
        "backup_codes_encrypted",
        existing_type=sa.String(length=2000),
        type_=sa.TEXT(),
        existing_nullable=True,
    )
    op.alter_column(
        "email_queue",
        "error_message",
        existing_type=sa.String(),
        type_=sa.TEXT(),
        existing_nullable=True,
    )
    op.alter_column(
        "email_queue",
        "body_html",
        existing_type=sa.String(),
        type_=sa.TEXT(),
        existing_nullable=False,
    )
    op.alter_column(
        "email_queue",
        "body_text",
        existing_type=sa.String(),
        type_=sa.TEXT(),
        existing_nullable=False,
    )

    op.alter_column("users", "updated_at", existing_type=sa.DateTime(timezone=True), nullable=True)
    op.alter_column("users", "created_at", existing_type=sa.DateTime(timezone=True), nullable=True)
    op.alter_column("shares", "updated_at", existing_type=sa.DateTime(timezone=True), nullable=True)
    op.alter_column("shares", "created_at", existing_type=sa.DateTime(timezone=True), nullable=True)
    op.alter_column(
        "share_members", "updated_at", existing_type=sa.DateTime(timezone=True), nullable=True
    )
    op.alter_column(
        "share_members", "created_at", existing_type=sa.DateTime(timezone=True), nullable=True
    )

    op.create_index(
        "idx_lifecycle_state_scheduled",
        "lifecycle_state",
        ["scheduled_at"],
        unique=False,
        postgresql_where=sa.text("(state = 'pending')"),
    )

    op.drop_index("ix_webhooks_user_id", table_name="webhooks")
    op.create_index("idx_webhooks_user_id", "webhooks", ["user_id"], unique=False)

    op.drop_index("ix_webhook_deliveries_webhook_id", table_name="webhook_deliveries")
    op.create_index(
        "idx_webhook_deliveries_webhook_id", "webhook_deliveries", ["webhook_id"], unique=False
    )
    op.drop_index("ix_webhook_deliveries_status", table_name="webhook_deliveries")
    op.create_index("idx_webhook_deliveries_status", "webhook_deliveries", ["status"], unique=False)
    op.drop_index("ix_webhook_deliveries_event_id", table_name="webhook_deliveries")
    op.create_index(
        "idx_webhook_deliveries_event_id", "webhook_deliveries", ["event_id"], unique=False
    )

    op.drop_index("ix_share_invites_email", table_name="share_invites")
    op.create_index("idx_share_invites_email", "share_invites", ["email"], unique=False)

    op.drop_index("ix_lifecycle_state_user_id", table_name="lifecycle_state")
    op.create_index("idx_lifecycle_state_user_id", "lifecycle_state", ["user_id"], unique=False)

    op.drop_index("ix_email_queue_status", table_name="email_queue")
    op.create_index("idx_email_queue_status", "email_queue", ["status"], unique=False)
    op.drop_index("ix_email_queue_email_type", table_name="email_queue")
    op.create_index("idx_email_queue_email_type", "email_queue", ["email_type"], unique=False)

    op.create_unique_constraint("users_email_key", "users", ["email"])
    op.create_unique_constraint(
        "user_sessions_refresh_token_hash_key", "user_sessions", ["refresh_token_hash"]
    )
    op.create_unique_constraint("share_invites_token_key", "share_invites", ["token"])
    op.create_unique_constraint(
        "password_reset_tokens_token_hash_key", "password_reset_tokens", ["token_hash"]
    )
    op.create_unique_constraint("oauth_providers_name_key", "oauth_providers", ["name"])

    op.drop_index("ix_share_agent_keys_key_hash", table_name="share_agent_keys")
    op.create_index("ix_share_agent_keys_key_hash", "share_agent_keys", ["key_hash"], unique=False)
    op.create_unique_constraint("share_agent_keys_key_hash_key", "share_agent_keys", ["key_hash"])

    op.drop_index("ix_email_verification_tokens_token_hash", table_name="email_verification_tokens")
    op.create_index(
        "ix_email_verification_tokens_token_hash",
        "email_verification_tokens",
        ["token_hash"],
        unique=False,
    )
    op.create_unique_constraint(
        "email_verification_tokens_token_hash_key", "email_verification_tokens", ["token_hash"]
    )

    op.drop_index(
        "ix_admin_login_pending_tokens_token_hash", table_name="admin_login_pending_tokens"
    )
    op.create_index(
        "ix_admin_login_pending_tokens_token_hash",
        "admin_login_pending_tokens",
        ["token_hash"],
        unique=False,
    )
    op.create_unique_constraint(
        "admin_login_pending_tokens_token_hash_key", "admin_login_pending_tokens", ["token_hash"]
    )
