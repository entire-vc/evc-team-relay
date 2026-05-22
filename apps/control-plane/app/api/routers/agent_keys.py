"""Agent key CRUD for Mesh artifact upload authorization."""

from __future__ import annotations

import hashlib
import secrets
import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core import security
from app.core.config import get_settings
from app.db import models
from app.db.session import get_db

router = APIRouter(prefix="/v1/web/shares/{share_id}/agent-keys", tags=["web"])


def _require_share_owner_or_admin(
    share_id: str, request: Request, db: Session
) -> models.Share:
    """Validate JWT and assert caller is admin, owner, or editor of the share."""
    auth_header = request.headers.get("Authorization")
    if not auth_header or not auth_header.startswith("Bearer "):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required")

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
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired token")

    share_stmt = select(models.Share).where(models.Share.id == uuid.UUID(share_id))
    share = db.execute(share_stmt).scalar_one_or_none()
    if not share:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Share not found")

    user_stmt = select(models.User).where(models.User.id == user_id)
    user = db.execute(user_stmt).scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="User not found")

    is_authorized = user.is_admin or share.owner_user_id == user_id
    if not is_authorized:
        member_stmt = select(models.ShareMember).where(
            models.ShareMember.share_id == share.id,
            models.ShareMember.user_id == user_id,
        )
        member = db.execute(member_stmt).scalar_one_or_none()
        is_authorized = member is not None

    if not is_authorized:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="You do not have access to this share")

    return share


class AgentKeyCreateRequest(BaseModel):
    label: str | None = None
    expires_at: datetime | None = None


class AgentKeyCreateResponse(BaseModel):
    id: str
    key: str  # raw key shown once
    label: str | None
    expires_at: datetime | None
    created_at: datetime


class AgentKeyListItem(BaseModel):
    id: str
    label: str | None
    last_used_at: datetime | None
    expires_at: datetime | None
    revoked_at: datetime | None
    created_at: datetime


@router.post("", status_code=status.HTTP_201_CREATED, response_model=AgentKeyCreateResponse)
def create_agent_key(
    share_id: str,
    payload: AgentKeyCreateRequest,
    request: Request,
    db: Session = Depends(get_db),
) -> AgentKeyCreateResponse:
    """Create an agent key for a share. Raw key is shown once and never retrievable again."""
    share = _require_share_owner_or_admin(share_id, request, db)

    raw_key = "tr_agent_" + secrets.token_hex(24)
    key_hash = hashlib.sha256(raw_key.encode()).hexdigest()

    agent_key = models.ShareAgentKey(
        share_id=share.id,
        key_hash=key_hash,
        label=payload.label,
        scopes="write",
        expires_at=payload.expires_at,
    )
    db.add(agent_key)
    db.commit()
    db.refresh(agent_key)

    return AgentKeyCreateResponse(
        id=str(agent_key.id),
        key=raw_key,
        label=agent_key.label,
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

    stmt = select(models.ShareAgentKey).where(
        models.ShareAgentKey.share_id == uuid.UUID(share_id)
    )
    keys = db.execute(stmt).scalars().all()

    return [
        AgentKeyListItem(
            id=str(k.id),
            label=k.label,
            last_used_at=k.last_used_at,
            expires_at=k.expires_at,
            revoked_at=k.revoked_at,
            created_at=k.created_at,
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
    _require_share_owner_or_admin(share_id, request, db)

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

    return {"message": "Agent key revoked", "id": key_id}
