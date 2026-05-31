"""Agent key CRUD for Mesh artifact upload authorization."""

from __future__ import annotations

import hashlib
import logging
import secrets
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, field_validator
from slowapi import Limiter
from slowapi.util import get_remote_address
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core import security
from app.core.config import get_settings
from app.db import models
from app.db.session import get_db

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/web/shares/{share_id}/agent-keys", tags=["web"])
limiter = Limiter(key_func=get_remote_address)


def _require_share_owner_or_admin(
    share_id: str, request: Request, db: Session
) -> tuple[models.Share, uuid.UUID]:
    """Validate JWT and assert caller is global admin or share owner.

    Returns (share, user_id). Viewer-role members are explicitly rejected.
    """
    auth_header = request.headers.get("Authorization")
    if not auth_header or not auth_header.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required"
        )

    token = auth_header.split(" ")[1]
    try:
        payload_jwt = security.decode_access_token(token)
        user_id_str = payload_jwt.get("sub")
        if not user_id_str:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")
        user_id = uuid.UUID(user_id_str)
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired token"
        )

    share_stmt = select(models.Share).where(models.Share.id == uuid.UUID(share_id))
    share = db.execute(share_stmt).scalar_one_or_none()
    if not share:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Share not found")

    user_stmt = select(models.User).where(models.User.id == user_id)
    user = db.execute(user_stmt).scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="User not found")

    # Only global admin or share owner may manage agent keys — not plain members
    if not (user.is_admin or share.owner_user_id == user_id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only the share owner or a global admin can manage agent keys",
        )

    return share, user_id


class AgentKeyCreateRequest(BaseModel):
    label: str | None = None
    expires_at: datetime | None = None

    @field_validator("label")
    @classmethod
    def trim_label(cls, v: str | None) -> str | None:
        if v is not None:
            v = v.strip()
            if len(v) > 255:
                raise ValueError("label must be 255 characters or fewer")
            return v or None
        return v


class AgentKeyCreateResponse(BaseModel):
    id: str
    key: str  # raw key shown once only
    label: str | None
    share_id: str
    scopes: list[str]
    expires_at: datetime | None
    created_at: datetime


class AgentKeyListItem(BaseModel):
    id: str
    label: str | None
    share_id: str
    scopes: list[str]
    created_by: str | None
    last_used_at: datetime | None
    expires_at: datetime | None
    revoked_at: datetime | None
    created_at: datetime
    is_active: bool


@router.post("", status_code=status.HTTP_201_CREATED, response_model=AgentKeyCreateResponse)
@limiter.limit(lambda: f"{get_settings().agent_key_creation_rate_per_hour}/hour")
def create_agent_key(
    request: Request,
    share_id: str,
    payload: AgentKeyCreateRequest,
    db: Session = Depends(get_db),
) -> AgentKeyCreateResponse:
    """Create an agent key for a share. Raw key is shown once and never retrievable again."""
    share, user_id = _require_share_owner_or_admin(share_id, request, db)
    settings = get_settings()

    # Validate expires_at
    if payload.expires_at is not None:
        now = security.utcnow()
        exp = payload.expires_at
        if exp.tzinfo is None:
            exp = exp.replace(tzinfo=timezone.utc)
        if exp <= now:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="expires_at must be in the future",
            )
        if exp > now + timedelta(days=730):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="expires_at must be within 2 years from now",
            )

    # Enforce per-share active key cap
    active_count_stmt = select(func.count()).where(
        models.ShareAgentKey.share_id == share.id,
        models.ShareAgentKey.revoked_at.is_(None),
    )
    active_count = db.execute(active_count_stmt).scalar_one()
    if active_count >= settings.agent_key_max_per_share:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Share has reached the maximum of {settings.agent_key_max_per_share} active agent keys.",
        )

    raw_key = "tr_agent_" + secrets.token_hex(24)
    key_hash = hashlib.sha256(raw_key.encode()).hexdigest()

    agent_key = models.ShareAgentKey(
        share_id=share.id,
        key_hash=key_hash,
        label=payload.label,
        scopes="write",
        created_by=user_id,
        expires_at=exp if payload.expires_at is not None else None,
    )
    db.add(agent_key)
    db.commit()
    db.refresh(agent_key)

    logger.info(
        "agent_key.created",
        extra={
            "event": "agent_key.created",
            "key_id": str(agent_key.id),
            "share_id": str(share.id),
            "created_by": str(user_id),
            "label": payload.label,
            "expires_at": payload.expires_at.isoformat() if payload.expires_at else None,
            "scopes": agent_key.scopes,
            "ip": request.client.host if request.client else None,
        },
    )

    return AgentKeyCreateResponse(
        id=str(agent_key.id),
        key=raw_key,
        label=agent_key.label,
        share_id=str(agent_key.share_id),
        scopes=list(agent_key.scopes.split(",")) if "," in agent_key.scopes else [agent_key.scopes],
        expires_at=agent_key.expires_at,
        created_at=agent_key.created_at,
    )


@router.get("", response_model=list[AgentKeyListItem])
def list_agent_keys(
    share_id: str,
    request: Request,
    db: Session = Depends(get_db),
) -> list[AgentKeyListItem]:
    """List agent keys for a share. Never returns key_hash or raw key."""
    _require_share_owner_or_admin(share_id, request, db)

    stmt = select(models.ShareAgentKey).where(models.ShareAgentKey.share_id == uuid.UUID(share_id))
    keys = db.execute(stmt).scalars().all()

    now = security.utcnow()
    return [
        AgentKeyListItem(
            id=str(k.id),
            label=k.label,
            share_id=str(k.share_id),
            scopes=list(k.scopes.split(",")) if "," in k.scopes else [k.scopes],
            created_by=str(k.created_by) if k.created_by else None,
            last_used_at=k.last_used_at,
            expires_at=k.expires_at,
            revoked_at=k.revoked_at,
            created_at=k.created_at,
            is_active=(
                k.revoked_at is None
                and (
                    k.expires_at is None
                    or (
                        k.expires_at.replace(tzinfo=timezone.utc)
                        if k.expires_at.tzinfo is None
                        else k.expires_at
                    ) > now
                )
            ),
        )
        for k in keys
    ]


@router.delete("/{key_id}", status_code=status.HTTP_200_OK)
def revoke_agent_key(
    share_id: str,
    key_id: str,
    request: Request,
    db: Session = Depends(get_db),
) -> dict:
    """Soft-revoke an agent key by setting revoked_at."""
    share, user_id = _require_share_owner_or_admin(share_id, request, db)

    stmt = select(models.ShareAgentKey).where(
        models.ShareAgentKey.id == uuid.UUID(key_id),
        models.ShareAgentKey.share_id == uuid.UUID(share_id),
    )
    agent_key = db.execute(stmt).scalar_one_or_none()
    if not agent_key:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent key not found")

    if agent_key.revoked_at is None:
        agent_key.revoked_at = security.utcnow()
        db.commit()

        logger.info(
            "agent_key.revoked",
            extra={
                "event": "agent_key.revoked",
                "key_id": key_id,
                "share_id": share_id,
                "revoked_by": str(user_id),
                "ip": request.client.host if request.client else None,
            },
        )

    return {"id": key_id, "revoked_at": agent_key.revoked_at.isoformat()}
