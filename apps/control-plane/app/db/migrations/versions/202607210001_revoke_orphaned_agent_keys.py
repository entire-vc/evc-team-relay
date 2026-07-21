"""Revoke agent keys whose creator has no standing authority over the share.

TR-03 (#ae52ba05): ShareAgentKey.created_by is ON DELETE SET NULL, and
neither member removal nor user deletion used to revoke the departing
user's keys, so a key kept authenticating indefinitely after its creator
lost access. This closes the currently-live exploit (confirmed: 18 active
keys on non-members, 13 on deleted users) by revoking every affected row
that exists today. Going forward, _auth_agent_key/_require_private_web_auth
reject unauthorized-creator keys at auth time regardless of revoked_at, and
remove_member/delete_user revoke them explicitly — this migration is the
one-time data remediation for keys created before that code shipped.

"Standing authority" matches _agent_key_creator_authorized in web.py: owner,
active member, OR a currently-active global admin (key creation itself is
admin-or-owner gated — see create_agent_key's _require_share_owner_or_admin
— so an admin-created key for a share they don't own/belong to is normal,
not orphaned, as long as that admin is still active). Omitting the admin
branch here would revoke a large share of legitimate fleet-wide Mesh-agent
keys that happen to have been issued by an admin account.

Idempotent: only touches rows where revoked_at IS NULL, so re-running (or
running against a DB that already had no orphaned keys) is a no-op.

Revision ID: 202607210001
Revises: 202606070001
Create Date: 2026-07-21 00:00:00.000000

"""

from typing import Sequence, Union

from alembic import op

revision: str = "202607210001"
down_revision: Union[str, None] = "202606070001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        UPDATE share_agent_keys sak
        SET revoked_at = now()
        WHERE sak.revoked_at IS NULL
          AND (
            sak.created_by IS NULL
            OR (
              NOT EXISTS (
                SELECT 1 FROM shares s
                WHERE s.id = sak.share_id AND s.owner_user_id = sak.created_by
              )
              AND NOT EXISTS (
                SELECT 1 FROM share_members sm
                WHERE sm.share_id = sak.share_id AND sm.user_id = sak.created_by
              )
              AND NOT EXISTS (
                SELECT 1 FROM users u
                WHERE u.id = sak.created_by AND u.is_admin = true AND u.is_active = true
              )
            )
          )
        """
    )


def downgrade() -> None:
    # Non-reversible: revoking these rows is the whole point of the fix, and
    # un-revoking without knowing which were legitimately revoked before this
    # migration ran would risk re-opening the exploit. Same precedent as
    # 202606040001 (label backfill).
    pass
