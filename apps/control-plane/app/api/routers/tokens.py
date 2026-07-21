from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from slowapi import Limiter
from slowapi.util import get_remote_address
from sqlalchemy.orm import Session

from app.api import deps
from app.db import models
from app.db.session import get_db
from app.schemas import token as token_schema
from app.services import token_service

router = APIRouter(prefix="/tokens", tags=["tokens"])
limiter = Limiter(key_func=get_remote_address)


@router.post("/relay", response_model=token_schema.RelayTokenResponse)
@limiter.limit("30/minute")  # Max 30 token requests per minute per IP
def issue_relay_token(
    request: Request,
    payload: token_schema.RelayTokenRequest,
    db: Session = Depends(get_db),
    current_user: models.User | None = Depends(deps.get_optional_user),
):
    # TR-07 (#cecd6baf): an X-Agent-Key header authenticates the request
    # in its own right, scoped to payload.share_id — see
    # token_service.issue_relay_token for the branch. A share-scoped agent
    # key can now obtain a relay-token without any User account, closing
    # the gap that forced the whole fleet onto the shared admin login.
    raw_agent_key = request.headers.get("X-Agent-Key")
    return token_service.issue_relay_token(db, request, payload, current_user, raw_agent_key)
