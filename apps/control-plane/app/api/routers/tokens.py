from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from slowapi import Limiter
from sqlalchemy.orm import Session

from app.api import deps
from app.api.admission import db_admission_slot
from app.core.ratelimit import rate_limit_key, relay_token_rate_limit_key
from app.db import models
from app.db.session import get_db
from app.schemas import token as token_schema
from app.services import token_service

router = APIRouter(prefix="/tokens", tags=["tokens"])
limiter = Limiter(key_func=rate_limit_key)

# Keyed per caller + share, no longer per proxy address. The
# plugin asks for one token per document, so a first pull of a large vault is
# hundreds of requests: 30/min made an 800-doc pull take half an hour even
# with the whole budget to itself. Issuing a token is a share-permission
# check plus a signature, so 300/min per user+share stays cheap. Anonymous
# callers (public shares, keyed by client IP) keep the old 30/min.
RELAY_TOKEN_LIMIT_PER_CALLER = 300
RELAY_TOKEN_LIMIT_ANONYMOUS = 30


def _relay_token_limit(key: str) -> str:
    per_minute = (
        RELAY_TOKEN_LIMIT_ANONYMOUS if key.startswith("ip:") else RELAY_TOKEN_LIMIT_PER_CALLER
    )
    return f"{per_minute}/minute"


# Shares its admission gate with the CAS routes (app/api/admission.py): the
# slot is taken before get_db and get_optional_user and released after them.
@router.post(
    "/relay",
    response_model=token_schema.RelayTokenResponse,
    dependencies=[Depends(db_admission_slot)],
)
@limiter.limit(_relay_token_limit, key_func=relay_token_rate_limit_key)
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
