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
    return token_service.issue_relay_token(db, request, payload, current_user)
