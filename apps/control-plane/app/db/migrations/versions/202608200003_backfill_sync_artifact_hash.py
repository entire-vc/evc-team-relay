"""Backfill sha256/size/modified_at/source on plugin-pushed web_folder_items.

Revision ID: 202608200003
Revises: 202608200002
Create Date: 2026-08-20 00:00:00.000000

Renumbered from 202608200001 -> 202608200003 (unchanged otherwise) after
merging main: task #ac65cfe5's reconciliation migration (202608200002) landed
on main first, forking from the same 202607250001 parent this one originally
did. Rather than merge two heads, this one now chains after it — the two
migrations touch disjoint data (email_queue vs. web_folder_items) so order
between them doesn't matter functionally, only the DAG needs to be linear.

Items pushed through POST /shares/{slug}/files (the Obsidian plugin's content-push
path, fixed alongside this migration) carried `content` but no `sha256`/`source`
before today — invisible and unwritable through the sync protocol added by #ee1745ce
(GET .../files-index filters on source == "sync-artifact" AND non-empty sha256).
This backfills the ones the app-level fix cannot reach retroactively: existing rows
with content already sitting in the JSONB column.

Scope of the backfill is deliberately narrow — only items with NO `source` at all
AND non-null `content`:
  - `source == "mesh-artifact"` (upload_mesh_artifact) is excluded on purpose: those
    are intentionally not part of the sync protocol, and flipping their source would
    change what they are, not just fill in a missing field.
  - `source == "sync-artifact"` already has a real sha256 (sync-upload/sync-write
    compute it at write time) and is left untouched.
  - `content is None` items (binary assets from the presigned-upload flow) have
    nothing to hash here; they already carry their own sha256 from the upload token.

`modified_at` has no historical value to recover — the JSONB item never carried one.
Using the share row's own `updated_at` (falls back to `created_at`) as the least
dishonest available proxy: closer to the actual last-touch time than "now", which
would make every backfilled row look like it just changed in this same instant.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence

from alembic import op
from sqlalchemy import text

revision: str = "202608200003"
down_revision: str | None = "202608200002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    conn = op.get_bind()
    rows = conn.execute(
        text(
            """
            SELECT id, web_folder_items, updated_at, created_at
            FROM shares
            WHERE web_folder_items IS NOT NULL
            """
        )
    ).fetchall()

    touched = 0
    for share_id, folder_items, updated_at, created_at in rows:
        if not folder_items:
            continue
        stamp = (updated_at or created_at).isoformat()
        changed = False
        new_items = []
        for item in folder_items:
            content = item.get("content")
            if item.get("source") is None and content is not None and not item.get("sha256"):
                item = dict(item)
                item["sha256"] = hashlib.sha256(content.encode("utf-8")).hexdigest()
                item["size"] = len(content.encode("utf-8"))
                item.setdefault("modified_at", stamp)
                item["source"] = "sync-artifact"
                changed = True
            new_items.append(item)
        if changed:
            conn.execute(
                text("UPDATE shares SET web_folder_items = CAST(:items AS JSONB) WHERE id = :id"),
                {"items": json.dumps(new_items), "id": share_id},
            )
            touched += 1

    print(f"backfilled sync-artifact hash on {touched} share(s)")


def downgrade() -> None:
    # Backfill is intentionally non-reversible — there is no record of which
    # sha256/source/size values were synthesized here vs. genuinely written by
    # sync-upload/sync-write after this migration ran.
    pass
