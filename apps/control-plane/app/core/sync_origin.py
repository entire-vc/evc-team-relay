"""Origin of a sync-artifact version.

Every index item written through the sync protocol records who produced it:

- ``crdt``: an Obsidian client mirrored its live CRDT text. Every other client
  already holds that text through the relay, so its inbound downloader must
  not write the file to disk again (that duplicates lines in an open note).
- ``external``: anything else (Mesh/agent writes, legacy items without the
  field). The plugin's inbound downloader pulls it into the vault.
"""

from __future__ import annotations

from typing import Any

from fastapi import Request

SYNC_ORIGIN_CRDT = "crdt"
SYNC_ORIGIN_EXTERNAL = "external"
SYNC_ORIGIN_HEADER = "X-Sync-Origin"


def resolve_sync_origin(request: Request, auth_method: str) -> str:
    """The origin a write may claim: ``crdt`` only for a user JWT that asks for it.

    ``crdt`` makes every client skip the version on its inbound path, so an
    agent key must never obtain it: an agent write marked that way would
    silently never reach a vault. Anything else is ``external``.
    """
    claimed = (request.headers.get(SYNC_ORIGIN_HEADER) or "").strip().lower()
    if claimed == SYNC_ORIGIN_CRDT and auth_method == "user":
        return SYNC_ORIGIN_CRDT
    return SYNC_ORIGIN_EXTERNAL


def item_sync_origin(item: dict[str, Any]) -> str:
    """Stored origin of an index item; absent or unknown means external."""
    return SYNC_ORIGIN_CRDT if item.get("origin") == SYNC_ORIGIN_CRDT else SYNC_ORIGIN_EXTERNAL
