"""Canonical read/write scope policy for share agent keys.

Why this module exists
----------------------
Until 2026-08-19 the same question — "may this agent key read this share's
content?" — was answered by two different pieces of code with two different
answers (task #b69d73fb):

* ``_require_private_web_auth`` (web.py) treated **write as implying read**, and
  served document content to a write-only key on ``GET /v1/web/shares/{slug}``,
  ``/files`` and ``/assets``.
* ``_auth_share_read_access`` (web.py) required the **literal** ``read`` scope,
  and answered 403 to that same key on ``/download`` and ``/files-index``.

Both routes return the same bytes, so one key on one share could read a document
through one route and be refused it through the other. The split was invisible
to callers, who reasonably read the 403 as "this endpoint does not exist".

The policy is LITERAL: a scope is satisfied only by itself.  ``write`` does not
imply ``read``.  The decision and its evidence are recorded in
``docs/adrs/0001-unified-agent-key-read-scope-policy.md``.

Migrating to it without breaking live integrations
--------------------------------------------------
Tightening the lenient routes is a breaking change for any key that lacks a
literal ``read`` and reads through them today.  So the tightening ships behind
``AGENT_KEY_LENIENT_READ_GRACE`` (see ``Settings.agent_key_lenient_read_grace``):

* grace **on** (phase 1, current default) — a write-only key is still allowed
  through a read gate, exactly as before, but every such call is logged as a
  WARNING naming the key and the route.  Behaviour is unchanged; the previously
  invisible population becomes measurable.
* grace **off** (phase 3, after phase 2 migrates the keys the WARNING names) —
  the literal policy applies everywhere and the two route families finally
  agree.

Flipping is a config change, so it is reversible in seconds without a redeploy.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def parse_scopes(raw: str | None) -> set[str]:
    """Split the stored comma-separated ``scopes`` column into a set.

    Tolerates padding and empty segments — the column is free-form text, and a
    stray space must not silently strip a scope.
    """
    if not raw:
        return set()
    return {s.strip() for s in raw.split(",") if s.strip()}


def satisfies(scopes: set[str], required: str) -> bool:
    """The canonical policy: literal scope match, no implication between scopes."""
    return required in scopes


def would_be_denied_without_grace(scopes: set[str], required: str) -> bool:
    """True when only the legacy write-implies-read leniency lets this through.

    Used to log the migration signal, and to decide whether grace is what is
    carrying the request.
    """
    return not satisfies(scopes, required) and required == "read" and "write" in scopes


def check(
    scopes: set[str],
    required: str,
    *,
    grace: bool,
    key_id: object = None,
    share_id: object = None,
    route: str = "",
) -> bool:
    """Decide whether a key holding ``scopes`` may perform ``required``.

    Returns True/False rather than raising: the two call sites in web.py raise
    different HTTPException shapes, and preserving those exact messages matters
    more than sharing the raise.

    When ``grace`` is on and the call only passes because of it, emits a WARNING
    naming the key, share and route — that log line is the measurement that
    tells us which keys still need migrating before the grace can be withdrawn.
    """
    if satisfies(scopes, required):
        return True

    if grace and would_be_denied_without_grace(scopes, required):
        logger.warning(
            "agent key passed a read gate only via the legacy write-implies-read "
            "grace; it will be denied once AGENT_KEY_LENIENT_READ_GRACE is off",
            extra={
                "agent_key_id": str(key_id) if key_id is not None else None,
                "share_id": str(share_id) if share_id is not None else None,
                "route": route,
                "scopes": sorted(scopes),
                "required_scope": required,
                "event": "agent_key_read_scope_grace",
            },
        )
        return True

    return False
