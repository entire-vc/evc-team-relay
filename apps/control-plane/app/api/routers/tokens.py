from __future__ import annotations

import asyncio
import weakref
from typing import AsyncIterator

from fastapi import APIRouter, Depends, HTTPException, Request, status
from slowapi import Limiter
from sqlalchemy.orm import Session

from app.api import deps
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


class _RelayTokenGate:
    """Caps relay-token issuances in flight below the DB pool (5 + 10).

    27.10 incident: one desktop client sent 71 POST /tokens/relay in 20 s,
    well inside its 300/min, and the burst took every pool connection until
    one request hit the 5 s pool timeout and got a 503. The rate limit counts
    requests per minute; it cannot bound how many run at the same moment.

    Admission happens before any dependency touches the database: the bearer
    lookup, the agent-key bucket lookup and the issuance itself all run after
    a slot is taken, and the session is closed before the slot is released.
    Waiters queue on the event loop, so a burst holds neither a pool
    connection nor a threadpool thread. A waiter still queued after
    WAIT_SECONDS gets 429 Retry-After, which the plugin already backs off on.
    In-process only; each worker process bounds its own pool.
    """

    MAX_IN_FLIGHT = 8
    WAIT_SECONDS = 10.0
    RETRY_AFTER_SECONDS = 2
    BUSY_DETAIL = "Too many concurrent relay token requests, retry later"

    def __init__(self) -> None:
        # asyncio primitives bind to the loop they first wait on; keep one
        # per loop so a restarted loop (tests, reloads) gets a fresh one.
        self._semaphores: weakref.WeakKeyDictionary[
            asyncio.AbstractEventLoop, asyncio.Semaphore
        ] = weakref.WeakKeyDictionary()

    def _semaphore(self) -> asyncio.Semaphore:
        loop = asyncio.get_running_loop()
        semaphore = self._semaphores.get(loop)
        if semaphore is None:
            semaphore = asyncio.Semaphore(self.MAX_IN_FLIGHT)
            self._semaphores[loop] = semaphore
        return semaphore

    async def acquire(self) -> asyncio.Semaphore:
        semaphore = self._semaphore()
        try:
            await asyncio.wait_for(semaphore.acquire(), timeout=self.WAIT_SECONDS)
        except TimeoutError:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=self.BUSY_DETAIL,
                headers={"Retry-After": str(self.RETRY_AFTER_SECONDS)},
            ) from None
        return semaphore


_relay_token_gate = _RelayTokenGate()


async def relay_token_slot() -> AsyncIterator[None]:
    semaphore = await _relay_token_gate.acquire()
    try:
        yield
    finally:
        semaphore.release()


# The gate is a route dependency so FastAPI resolves it before get_db and
# get_optional_user, and tears it down after them.
@router.post(
    "/relay",
    response_model=token_schema.RelayTokenResponse,
    dependencies=[Depends(relay_token_slot)],
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
