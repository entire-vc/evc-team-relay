"""Per-caller keys for the slowapi rate limiters.

slowapi's stock remote-address key function keys on `request.client.host`. Behind
Caddy that is the proxy's address — the same for every client — so each
"N/minute per IP" limit was really N/minute for the whole instance
(30/min on POST /tokens/relay shared by all of prod, background
pulls of large vaults starved on 429).

Authenticated requests are keyed by who is calling; anonymous ones by the
real client address from `get_client_ip`. A credential only earns its own
bucket once it is proven: a bearer/cookie JWT must verify, otherwise the
request falls back to the address — a forged token cannot mint a fresh
bucket per request. An X-Agent-Key is looked up (one indexed read on
key_hash) and only a live key — present, not revoked, not expired — is keyed
by its row id; any other value falls back to the address, so rotating random
header values cannot dodge a limit (login included).
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import timezone

from fastapi import Request
from jwt.exceptions import InvalidTokenError
from sqlalchemy import select

from app.core import security
from app.core.http import get_client_ip
from app.db import models
from app.db import session as session_module

# Cached on request.state: slowapi calls the key function once to pick a
# dynamic limit and again to count, and an agent key costs a DB read.
_STATE_ATTR = "rate_limit_principal"
_UNSET = object()


def _user_from_jwt(token: str) -> str | None:
    try:
        payload = security.decode_access_token(token)
    except InvalidTokenError:
        return None
    subject = payload.get("sub")
    return f"user:{subject}" if subject else None


def _live_agent_key(raw_key: str) -> str | None:
    key_hash = hashlib.sha256(raw_key.encode()).hexdigest()
    with session_module.get_sessionmaker()() as db:
        row = db.execute(
            select(
                models.ShareAgentKey.id,
                models.ShareAgentKey.revoked_at,
                models.ShareAgentKey.expires_at,
            ).where(models.ShareAgentKey.key_hash == key_hash)
        ).one_or_none()
    if row is None or row.revoked_at is not None:
        return None
    if row.expires_at is not None:
        expires_at = row.expires_at
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        if expires_at < security.utcnow():
            return None
    return f"agentkey:{row.id}"


def _principal_key(request: Request) -> str | None:
    cached = getattr(request.state, _STATE_ATTR, _UNSET)
    if cached is _UNSET:
        cached = _resolve_principal(request)
        setattr(request.state, _STATE_ATTR, cached)
    return cached


def _resolve_principal(request: Request) -> str | None:
    authorization = request.headers.get("authorization")
    if authorization:
        scheme, _, token = authorization.partition(" ")
        if scheme.lower() == "bearer" and token:
            principal = _user_from_jwt(token.strip())
            if principal:
                return principal

    raw_agent_key = request.headers.get("x-agent-key")
    if raw_agent_key:
        principal = _live_agent_key(raw_agent_key)
        if principal:
            return principal

    cookie_token = request.cookies.get("access_token")
    if cookie_token:
        return _user_from_jwt(cookie_token)
    return None


def _ip_key(request: Request) -> str:
    return f"ip:{get_client_ip(request) or 'unknown'}"


def rate_limit_key(request: Request) -> str:
    """slowapi key_func: the authenticated caller, else the real client IP."""
    return _principal_key(request) or _ip_key(request)


def _share_id_from_body(request: Request) -> str | None:
    # By the time slowapi's wrapper runs, FastAPI has already read and cached
    # the body on this same Request to build the payload model, so this is a
    # read of bytes in memory, not a second receive.
    body = getattr(request, "_body", None)
    if not body:
        return None
    try:
        share_id = json.loads(body).get("share_id")
        return str(uuid.UUID(str(share_id)))
    except (ValueError, TypeError, AttributeError):
        return None


def relay_token_rate_limit_key(request: Request) -> str:
    """Key for POST /tokens/relay: caller + share, so one big vault's pull
    does not eat the budget the same user needs for another share."""
    principal = _principal_key(request)
    if principal is None:
        return _ip_key(request)
    share_id = _share_id_from_body(request)
    return f"{principal}:share:{share_id}" if share_id else principal
