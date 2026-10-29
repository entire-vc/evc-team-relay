"""POST /tokens/relay burst: concurrent issuances must stay below the DB pool.

27.10 incident: one desktop client sent 71 relay-token requests in 20 s, inside
its 300/min rate limit, and the burst drained the 5 + 10 pool until a request
hit the pool timeout and got a 503.
"""

from __future__ import annotations

import asyncio
import time

import httpx
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import QueuePool

from app.api.routers import tokens
from app.core import security
from app.core.metrics import DB_POOL_TIMEOUTS_TOTAL
from app.db import models
from app.db.session import InstrumentedQueuePool, get_db
from app.main import build_app
from app.services import token_service

pytestmark = pytest.mark.db_commit

POOL_SIZE = 3
BURST = 100
# Time each issuance holds its connection: long enough that a burst without
# admission queues on the pool past its timeout.
HOLD_SECONDS = 0.05


@pytest.fixture
def burst_app(tmp_path, monkeypatch):
    """App on a small instrumented QueuePool with a slow issuance."""
    engine = create_engine(
        f"sqlite+pysqlite:///{tmp_path / 'burst.db'}",
        connect_args={"check_same_thread": False, "timeout": 30},
        poolclass=InstrumentedQueuePool,
        pool_size=POOL_SIZE,
        max_overflow=0,
        pool_timeout=0.3,
    )
    models.Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False)
    with factory() as db:
        user = models.User(email="burst@example.com", password_hash="unused", is_active=True)
        db.add(user)
        db.flush()
        share = models.Share(
            owner_user_id=user.id,
            path="notes",
            kind=models.ShareKind.FOLDER,
            visibility=models.ShareVisibility.PRIVATE,
        )
        db.add(share)
        db.commit()
        user_id, share_id = user.id, share.id

    issue = token_service.issue_relay_token

    def slow_issue(db, *args, **kwargs):
        # The bearer lookup already checked a connection out; hold it.
        time.sleep(HOLD_SECONDS)
        return issue(db, *args, **kwargs)

    monkeypatch.setattr(token_service, "issue_relay_token", slow_issue)

    app = build_app()
    app.state.relay_private_key = Ed25519PrivateKey.generate()
    app.state.relay_key_id = "test-key"

    def override_get_db():
        db = factory()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    tokens.limiter.reset()
    token = security.create_access_token(str(user_id))
    try:
        yield app, engine, share_id, token
    finally:
        engine.dispose()


def _run_burst(app, share_id, token):
    headers = {"Authorization": f"Bearer {token}"}
    body = {"share_id": str(share_id), "doc_id": str(share_id), "mode": "read"}

    async def exercise():
        health_latencies: list[float] = []
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test", timeout=60
        ) as client:
            burst_done = asyncio.Event()

            async def probe_health():
                while not burst_done.is_set():
                    started = time.perf_counter()
                    resp = await client.get("/v1/health")
                    assert resp.status_code == 200
                    health_latencies.append(time.perf_counter() - started)
                    await asyncio.sleep(0.05)

            prober = asyncio.create_task(probe_health())
            await asyncio.sleep(0.05)
            responses = await asyncio.gather(
                *(client.post("/v1/tokens/relay", json=body, headers=headers) for _ in range(BURST))
            )
            burst_done.set()
            await prober
        return responses, health_latencies

    timeouts_before = DB_POOL_TIMEOUTS_TOTAL._value.get()
    responses, health_latencies = asyncio.run(exercise())
    return (
        [r.status_code for r in responses],
        DB_POOL_TIMEOUTS_TOTAL._value.get() - timeouts_before,
        health_latencies,
    )


def test_gate_stays_below_default_pool():
    # QueuePool defaults the control plane runs on: pool_size 5, max_overflow 10.
    pool = QueuePool(lambda: None)
    assert tokens._RelayTokenGate.MAX_IN_FLIGHT < pool.size() + pool._max_overflow


def test_burst_from_one_caller_never_times_out_on_pool(burst_app, monkeypatch):
    app, engine, share_id, token = burst_app
    monkeypatch.setattr(tokens._RelayTokenGate, "MAX_IN_FLIGHT", POOL_SIZE - 1)
    monkeypatch.setattr(tokens, "_relay_token_gate", tokens._RelayTokenGate())

    statuses, pool_timeouts, health_latencies = _run_burst(app, share_id, token)

    assert statuses == [200] * BURST, sorted(set(statuses))
    assert pool_timeouts == 0
    assert max(health_latencies) < 2.0, f"health max {max(health_latencies):.2f}s"
    assert engine.pool.checkedout() == 0


def test_burst_without_gate_drains_pool(burst_app, monkeypatch):
    """Negative control: the same burst with admission opened up hits the pool timeout."""
    app, engine, share_id, token = burst_app
    monkeypatch.setattr(tokens._RelayTokenGate, "MAX_IN_FLIGHT", BURST)
    monkeypatch.setattr(tokens, "_relay_token_gate", tokens._RelayTokenGate())

    statuses, pool_timeouts, _ = _run_burst(app, share_id, token)

    assert pool_timeouts >= 1
    assert any(s >= 500 for s in statuses), sorted(set(statuses))


def test_waiter_past_deadline_gets_429_retry_after(burst_app, monkeypatch):
    app, _, share_id, token = burst_app
    monkeypatch.setattr(tokens._RelayTokenGate, "MAX_IN_FLIGHT", 1)
    monkeypatch.setattr(tokens._RelayTokenGate, "WAIT_SECONDS", 0.01)
    monkeypatch.setattr(tokens, "_relay_token_gate", tokens._RelayTokenGate())

    statuses, pool_timeouts, _ = _run_burst(app, share_id, token)

    assert set(statuses) == {200, 429}, sorted(set(statuses))
    assert pool_timeouts == 0
