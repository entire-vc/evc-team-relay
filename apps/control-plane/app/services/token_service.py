from __future__ import annotations

import uuid
from datetime import timedelta

from fastapi import HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core import security
from app.core.config import get_settings
from app.core.metrics import RELAY_TOKENS_ISSUED_TOTAL
from app.db import models
from app.schemas import token as token_schema
from app.services import audit_service, share_service


def _find_share_by_id(db: Session, share_id: uuid.UUID) -> models.Share | None:
    """Look up a share by id without raising 404 (used for the H6 doc_id check)."""
    stmt = select(models.Share).where(models.Share.id == share_id)
    return db.execute(stmt).scalar_one_or_none()


def issue_relay_token(
    db: Session,
    request: Request,
    payload: token_schema.RelayTokenRequest,
    user: models.User | None,
    raw_agent_key: str | None = None,
) -> token_schema.RelayTokenResponse:
    share = share_service.get_share(db, payload.share_id)

    # TR-07 (#cecd6baf): an X-Agent-Key header authenticates in its own
    # right, scoped to `share` — validated up front so both the H6 check
    # below and the main permission check can treat "authenticated via
    # agent key" as settled. Unlike the browser-iframe agent-key path in
    # web.py (_require_private_web_auth), which falls back to a JWT/cookie
    # if the key doesn't check out, this is a machine-to-machine POST: an
    # invalid/wrong-share/expired key fails closed immediately rather than
    # silently trying a JWT the caller likely doesn't have.
    required_scope = "write" if payload.mode == token_schema.TokenMode.WRITE else "read"
    agent_key: models.ShareAgentKey | None = None
    if raw_agent_key:
        agent_key = share_service.authenticate_agent_key(
            db, share, raw_agent_key, required_scope=required_scope
        )

    # H6 — Confused-deputy issuer-side fix.
    #
    # doc_id is a client-chosen opaque string (a vault path, a per-file UUID, or
    # the share's own id when syncing the whole share) — the control-plane does
    # not maintain a registry of valid doc_ids per share, by design (see notes
    # below), so we cannot validate arbitrary doc_id values against the share.
    #
    # The concrete attack this DOES close: doc_id happening to equal ANOTHER
    # share's id. Because "doc_id == share_id" is the documented convention for
    # syncing a whole share, an attacker authorized on share A could otherwise
    # request a token for share A but with doc_id = share B's id, obtaining a
    # write-scoped token for share B's real document while never having been
    # authorized on share B. If doc_id resolves to a different, real share,
    # require the same read/write authorization on THAT share too.
    if str(payload.doc_id) != str(share.id):
        try:
            foreign_share_id = uuid.UUID(str(payload.doc_id))
        except ValueError:
            foreign_share_id = None
        if foreign_share_id is not None:
            foreign_share = _find_share_by_id(db, foreign_share_id)
            if foreign_share is not None:
                if agent_key is not None:
                    # An agent key is bound to exactly one share_id — it can
                    # never legitimately carry authority over a DIFFERENT
                    # real share, so this is always a reject, not a
                    # re-check (there's no "agent key access to share B" to
                    # verify — same conclusion the user-based branch below
                    # reaches via ensure_*_access, just without a User row).
                    raise HTTPException(
                        status_code=status.HTTP_403_FORBIDDEN,
                        detail="Agent key not valid for this share",
                    )
                if payload.mode == token_schema.TokenMode.WRITE:
                    share_service.ensure_write_access(db, foreign_share, user)
                else:
                    share_service.ensure_read_access(
                        db, foreign_share, user, password=payload.password
                    )

    # For folder shares (and per-file doc shares), membership check
    # (ensure_write_access/ensure_read_access below) is the sole authorization —
    # file-level doc_id validation beyond the check above is intentionally
    # skipped because:
    # 1. doc_id for individual files is a client-generated UUID or vault path,
    #    not a value the control-plane records anywhere (no per-file doc_id
    #    registry — local folder/file layouts differ between devices)
    # 2. Authorization is via share membership, not via doc_id — this is a
    #    pre-existing, tested design (see test_folder_share_any_doc_id_accepted,
    #    test_find_share_for_path_doc_precedence)

    # Check permissions — already settled above if an agent key authenticated.
    if agent_key is None:
        if payload.mode == token_schema.TokenMode.WRITE:
            share_service.ensure_write_access(db, share, user)
        else:
            share_service.ensure_read_access(db, share, user, password=payload.password)

    settings = get_settings()
    expires_in = timedelta(minutes=settings.relay_token_ttl_minutes)
    expires_at = security.utcnow() + expires_in

    # Generate Ed25519-signed CWT token for relay-server authentication.
    #
    # H6 — Confused-deputy notes:
    # The issuer-side check above closes the concrete cross-share vector where
    # doc_id equals a real, different share's id. Per-file doc_ids (paths/UUIDs
    # unrelated to any share id) remain scoped by membership only, per the
    # design notes above — the control-plane has no registry to validate them
    # against.
    #
    # share_id is additionally embedded as CWT_CLAIM_SHARE (-80203) so the
    # relay-server COULD cross-check the share→doc binding independently, but
    # confirmed (TR-22, 2026-07-21) that our relay-server fork does not read this
    # claim today — cwt.rs/auth.rs never reference -80203. It's inert, forward-compat
    # only, until relay-server gains support.
    # TODO(H6-relay-server): implement CWT_CLAIM_SHARE enforcement in
    # ghcr.io/entire-vc/evc-relay-server (crates/y-sweet-core/src/cwt.rs
    # parse_claims_map + auth.rs) — separate task from TR-22, file if not already open.
    private_key = request.app.state.relay_private_key
    key_id = request.app.state.relay_key_id

    token = security.create_relay_token_cwt(
        private_key=private_key,
        key_id=key_id,
        doc_id=payload.doc_id,
        mode=payload.mode.value,
        expires_minutes=settings.relay_token_ttl_minutes,
        audience=settings.effective_relay_audience,
        issuer=settings.relay_token_issuer,
        share_id=str(share.id),
    )
    RELAY_TOKENS_ISSUED_TOTAL.labels(mode=payload.mode.value).inc()

    # Log token issuance with file path for folder shares
    details = {
        "doc_id": payload.doc_id,
        "mode": payload.mode.value,
        "expires_at": expires_at.isoformat(),
    }
    if share.kind == models.ShareKind.FOLDER and payload.file_path:
        details["file_path"] = payload.file_path
    if agent_key is not None:
        details["agent_key_id"] = str(agent_key.id)
        agent_key.last_used_at = security.utcnow()

    audit_service.log_action(
        db=db,
        action=models.AuditAction.TOKEN_ISSUED,
        actor_user_id=user.id if user else None,
        target_share_id=share.id,
        details=details,
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
    )

    return token_schema.RelayTokenResponse(
        relay_url=str(settings.relay_public_url).rstrip("/"),
        token=token,
        expires_at=expires_at,
    )
