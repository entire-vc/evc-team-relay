"""Admission gate for routes that check out a DB connection per request.

The rate limit counts requests per minute; it cannot bound how many run at the
same moment. 27.10 (POST /tokens/relay, 71 in 20 s) and 01.11 (CAS file-token
and upload-url, 197 QueuePool timeouts) both drained the 5 + 10 pool with one
client inside its rate limit. One gate is shared by those routes so their
combined in-flight count stays below the pool.

Admission is a route dependency: FastAPI resolves it before get_db and
get_optional_user and tears it down after them, so a request takes a slot
before it touches the database and the session is closed before the slot is
released. Waiters queue on the event loop, so a burst holds neither a pool
connection nor a threadpool thread. A waiter still queued after WAIT_SECONDS
gets 429 Retry-After, which the plugin already backs off on. In-process only;
each worker process bounds its own pool.
"""

from __future__ import annotations

import asyncio
import weakref
from typing import AsyncIterator

from fastapi import HTTPException, status


class DbAdmissionGate:
    """Caps DB-bound requests in flight below the DB pool (5 + 10)."""

    MAX_IN_FLIGHT = 8
    WAIT_SECONDS = 10.0
    RETRY_AFTER_SECONDS = 2
    BUSY_DETAIL = "Too many concurrent requests, retry later"

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


db_admission_gate = DbAdmissionGate()


async def db_admission_slot() -> AsyncIterator[None]:
    semaphore = await db_admission_gate.acquire()
    try:
        yield
    finally:
        semaphore.release()
