from __future__ import annotations

from datetime import timedelta

from fastapi import Request
from sqlalchemy.orm import Session

from app.core import security
from app.core.config import get_settings
from app.db import models
from app.schemas import token as token_schema
from app.services import audit_service, share_service


def issue_relay_token(
    db: Session,
    request: Request,
    payload: token_schema.RelayTokenRequest,
    user: models.User | None,
) -> token_schema.RelayTokenResponse:
    share = share_service.get_share(db, payload.share_id)

    # For folder shares, membership check (ensure_write_access/ensure_read_access below)
    # is sufficient for authorization. File path validation is skipped because:
    # 1. doc_id for individual files is a UUID, not a filesystem path
    # 2. Local folder names can differ between devices (user picks any folder)
    # 3. The relay server scopes tokens to specific doc_ids regardless

    # Check permissions
    if payload.mode == token_schema.TokenMode.WRITE:
        share_service.ensure_write_access(db, share, user)
    else:
        share_service.ensure_read_access(db, share, user, password=payload.password)

    settings = get_settings()
    expires_in = timedelta(minutes=settings.relay_token_ttl_minutes)
    expires_at = security.utcnow() + expires_in

    # Generate Ed25519-signed CWT token (y-sweet expects CWT, not JWT)
    private_key = request.app.state.relay_private_key
    key_id = request.app.state.relay_key_id

    # Include relay URL as audience for relay-server validation
    relay_url = str(settings.relay_public_url).rstrip("/")

    token = security.create_relay_token_cwt(
        private_key=private_key,
        key_id=key_id,
        doc_id=payload.doc_id,
        mode=payload.mode.value,
        expires_minutes=settings.relay_token_ttl_minutes,
        audience=relay_url,
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
