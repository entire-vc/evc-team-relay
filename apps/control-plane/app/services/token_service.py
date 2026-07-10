from __future__ import annotations

import uuid
from datetime import timedelta

from fastapi import Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core import security
from app.core.config import get_settings
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
) -> token_schema.RelayTokenResponse:
    share = share_service.get_share(db, payload.share_id)

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

    # Check permissions
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
    # relay-server CAN cross-check the share→doc binding independently if it
    # supports that claim. Relay-server (System3) enforcement of this claim is
    # tracked separately — see TODO(H6-System3) follow-up task.
    # TODO(H6-System3): verify relay-server enforces CWT_CLAIM_SHARE and scope.
    private_key = request.app.state.relay_private_key
    key_id = request.app.state.relay_key_id
    relay_url = str(settings.relay_public_url).rstrip("/")

    token = security.create_relay_token_cwt(
        private_key=private_key,
        key_id=key_id,
        doc_id=payload.doc_id,
        mode=payload.mode.value,
        expires_minutes=settings.relay_token_ttl_minutes,
        audience=relay_url,
        share_id=str(share.id),
    )

    # Log token issuance with file path for folder shares
    details = {
        "doc_id": payload.doc_id,
        "mode": payload.mode.value,
        "expires_at": expires_at.isoformat(),
    }
    if share.kind == models.ShareKind.FOLDER and payload.file_path:
        details["file_path"] = payload.file_path

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
