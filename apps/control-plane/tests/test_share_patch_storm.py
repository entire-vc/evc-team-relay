"""PATCH /shares storm: folder-tree resends must not exhaust the pool or stall the loop.

26.10 incident: the plugin resent `web_folder_items` on every file event, ~2600
PATCHes in 10 min on 6 published folder shares. Each PATCH waited on Billing,
then re-took a pool connection on the event loop; /v1/health hung behind it.
"""

from __future__ import annotations

import asyncio
import threading
import time
import uuid

import httpx
import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import QueuePool

from app.api.routers import shares
from app.core import security
from app.db import models
from app.db.session import get_db
from app.main import build_app

pytestmark = pytest.mark.db_commit

STORM_SIZE = 30
SLOW_BILLING_SECONDS = 6.0


@pytest.fixture
def storm_app(tmp_path, monkeypatch):
    """App on a small file-backed QueuePool, billing enabled with a slow Billing."""
    engine = create_engine(
        f"sqlite+pysqlite:///{tmp_path / 'storm.db'}",
        connect_args={"check_same_thread": False, "timeout": 30},
        poolclass=QueuePool,
        pool_size=2,
        max_overflow=0,
        pool_timeout=1.0,
    )
    models.Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False)
    with factory() as db:
        user = models.User(email="storm@example.com", password_hash="unused", is_active=True)
        db.add(user)
        db.flush()
        share = models.Share(
            owner_user_id=user.id,
            path="notes",
            kind=models.ShareKind.FOLDER,
            visibility=models.ShareVisibility.PRIVATE,
            web_published=True,
            web_slug="notes-storm",
            web_folder_items=[{"path": "notes/a.md", "name": "a.md", "type": "doc"}],
        )
        db.add(share)
        db.commit()
        user_id, share_id = user.id, share.id

    settings = shares.get_settings().model_copy(update={"billing_enabled": True})
    monkeypatch.setattr(shares, "get_settings", lambda: settings)
    billing_calls: list[str] = []

    async def slow_billing(*args):
        billing_calls.append(args[0])
        await asyncio.sleep(SLOW_BILLING_SECONDS)

    monkeypatch.setattr(shares.billing_service, "check_visibility", slow_billing)
    monkeypatch.setattr(shares.billing_service, "check_limit", slow_billing)

    app = build_app()

    def override_get_db():
        db = factory()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    token = security.create_access_token(str(user_id))
    try:
        yield app, engine, share_id, token, billing_calls
    finally:
        engine.dispose()


def _folder_items(n: int) -> list[dict]:
    return [{"path": f"notes/{i}.md", "name": f"{i}.md", "type": "doc"} for i in range(n)]


def test_folder_items_storm_keeps_health_fast_and_skips_billing(storm_app):
    app, engine, share_id, token, billing_calls = storm_app
    headers = {"Authorization": f"Bearer {token}"}

    async def exercise():
        health_latencies: list[float] = []
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://test",
            timeout=60,
        ) as client:
            storm_done = asyncio.Event()

            async def probe_health():
                # Time from one probe to the next: a loop blocked by a pool
                # wait stalls the sleep and the request alike.
                last = time.perf_counter()
                while not storm_done.is_set():
                    resp = await client.get("/v1/health")
                    assert resp.status_code == 200
                    await asyncio.sleep(0.05)
                    now = time.perf_counter()
                    health_latencies.append(now - last)
                    last = now

            async def patch(i: int):
                return await client.patch(
                    f"/v1/shares/{share_id}",
                    json={"web_folder_items": _folder_items(2 + i % 3)},
                    headers=headers,
                )

            prober = asyncio.create_task(probe_health())
            # Let the probe start before the storm hits the loop.
            await asyncio.sleep(0.05)
            responses = await asyncio.gather(*(patch(i) for i in range(STORM_SIZE)))
            storm_done.set()
            await prober
        return responses, health_latencies

    responses, health_latencies = asyncio.run(exercise())

    statuses = [r.status_code for r in responses]
    assert not billing_calls, f"billing called {len(billing_calls)}x for folder-items PATCHes"
    assert set(statuses) <= {200, 429}, statuses
    assert statuses.count(200) >= 1
    assert max(health_latencies) < 2.0, f"health max {max(health_latencies):.2f}s"
    assert engine.pool.checkedout() == 0
    with Session(engine) as db:
        share = db.get(models.Share, share_id)
        assert share.web_folder_items  # one of the storm's trees landed


def test_identical_folder_items_patch_writes_nothing(storm_app):
    app, engine, share_id, token, _ = storm_app
    headers = {"Authorization": f"Bearer {token}"}
    items = _folder_items(3)

    async def exercise():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            first = await client.patch(
                f"/v1/shares/{share_id}", json={"web_folder_items": items}, headers=headers
            )
            assert first.status_code == 200, first.text
            with Session(engine) as db:
                updated_at = db.get(models.Share, share_id).updated_at
                audits = db.scalar(select(func.count()).select_from(models.AuditLog))
            again = await client.patch(
                f"/v1/shares/{share_id}", json={"web_folder_items": items}, headers=headers
            )
            assert again.status_code == 200, again.text
            return updated_at, audits

    updated_at, audits = asyncio.run(exercise())
    with Session(engine) as db:
        assert db.get(models.Share, share_id).updated_at == updated_at
        assert db.scalar(select(func.count()).select_from(models.AuditLog)) == audits


def test_visibility_change_on_published_share_still_checks_billing(storm_app, monkeypatch):
    app, _, share_id, token, billing_calls = storm_app

    async def fast_billing(*args):
        billing_calls.append(args[0])

    monkeypatch.setattr(shares.billing_service, "check_visibility", fast_billing)

    async def exercise():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            return await client.patch(
                f"/v1/shares/{share_id}",
                json={"visibility": "protected", "password": "long-enough-pw"},
                headers={"Authorization": f"Bearer {token}"},
            )

    resp = asyncio.run(exercise())
    assert resp.status_code == 200, resp.text
    assert len(billing_calls) == 1


class TestFolderItemsGate:
    def test_newest_waiter_supersedes_older_one(self):
        gate = shares._FolderItemsGate()
        share_id = uuid.uuid4()
        assert gate.enter(share_id) is shares._GateOutcome.APPLY

        outcomes: dict[str, shares._GateOutcome] = {}

        def wait(name: str):
            outcomes[name] = gate.enter(share_id)
            if outcomes[name] is shares._GateOutcome.APPLY:
                gate.leave(share_id)

        older = threading.Thread(target=wait, args=("older",))
        older.start()
        time.sleep(0.05)
        newer = threading.Thread(target=wait, args=("newer",))
        newer.start()
        older.join(timeout=2)
        assert outcomes["older"] is shares._GateOutcome.SUPERSEDED
        gate.leave(share_id)
        newer.join(timeout=2)
        assert outcomes["newer"] is shares._GateOutcome.APPLY
        assert not gate._latest and not gate._busy and not gate._waiting

    def test_waiter_times_out_behind_a_stuck_update(self, monkeypatch):
        monkeypatch.setattr(shares._FolderItemsGate, "WAIT_SECONDS", 0.1)
        gate = shares._FolderItemsGate()
        share_id = uuid.uuid4()
        assert gate.enter(share_id) is shares._GateOutcome.APPLY
        assert gate.enter(share_id) is shares._GateOutcome.TIMEOUT
        gate.leave(share_id)
        assert gate.enter(share_id) is shares._GateOutcome.APPLY
        gate.leave(share_id)
