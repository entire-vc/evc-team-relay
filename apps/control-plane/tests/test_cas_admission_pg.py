"""CAS file-token -> upload-url burst on PostgreSQL stays below the DB pool.

01.11 incident: a desktop client's attachment sync (170 file-token, 24
upload-url) bypassed the relay-token admission gate and produced 197
QueuePool timeouts. Runs against a live server on a real PostgreSQL pool of
3 with a slow share lookup, mixed with relay-token requests.
"""

from __future__ import annotations

import json
import os
import socket
import time
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlparse

import httpx
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from test_fault_injection import LiveServer, ObjectStore, _reset_rate_limiters

from app.api import admission
from app.api.routers import shares
from app.core import security
from app.core.config import get_settings
from app.core.metrics import DB_POOL_TIMEOUTS_TOTAL
from app.db import models
from app.db import session as session_module
from app.db.session import InstrumentedQueuePool
from app.main import build_app

PG_URL = os.environ.get("PG_TEST_DATABASE_URL") or os.environ.get("FAULT_TEST_DATABASE_URL")
pytestmark = [
    pytest.mark.db_commit,
    pytest.mark.skipif(not PG_URL, reason="no PostgreSQL test database configured"),
]

POOL_SIZE = 3
CLIENTS = 60
HOLD_SECONDS = 0.05


@pytest.fixture
def cas_env(monkeypatch):
    engine = create_engine(
        PG_URL,
        poolclass=InstrumentedQueuePool,
        pool_size=POOL_SIZE,
        max_overflow=0,
        pool_timeout=0.5,
    )
    models.Base.metadata.drop_all(engine)
    models.Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False)
    with factory() as db:
        user = models.User(email="cas@example.com", password_hash="unused", is_active=True)
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

    get_share = shares.share_service.get_share
    entered = []

    def slow_get_share(db, sid):
        entered.append(time.monotonic())
        share = get_share(db, sid)
        time.sleep(HOLD_SECONDS)
        return share

    monkeypatch.setattr(shares.share_service, "get_share", slow_get_share)
    monkeypatch.setattr(session_module, "_engine", engine)
    monkeypatch.setattr(session_module, "_SessionLocal", factory)
    monkeypatch.setattr(session_module, "_probe_engine", None)
    _reset_rate_limiters()

    store = ObjectStore()
    store.objects[f"{get_settings().minio_bucket}/web-assets/{share_id}/a.png"] = b"\x89PNG"
    monkeypatch.setenv("MINIO_ENDPOINT", store.endpoint)
    get_settings.cache_clear()

    app = build_app()
    app.state.relay_private_key = Ed25519PrivateKey.generate()
    app.state.relay_key_id = "test-key"
    server = LiveServer(app)
    try:
        yield server, engine, share_id, security.create_access_token(str(user_id)), entered
    finally:
        server.close()
        store.close()
        engine.dispose()
        get_settings.cache_clear()


def _burst(server, share_id, token):
    headers = {"Authorization": f"Bearer {token}"}
    base = server.base_url
    cas_body = {
        "path": "a.png",
        "sha256": "0" * 64,
        "content_type": "image/png",
        "content_length": 4,
    }
    relay_body = {"share_id": str(share_id), "doc_id": str(share_id), "mode": "read"}

    def client(n: int) -> list[int]:
        if n % 4 == 3:
            r = httpx.post(f"{base}/v1/tokens/relay", json=relay_body, headers=headers, timeout=60)
            return [r.status_code]
        minted = httpx.post(
            f"{base}/v1/shares/{share_id}/file-token", json=cas_body, headers=headers, timeout=60
        )
        if minted.status_code != 200:
            return [minted.status_code]
        file_headers = {"Authorization": f"Bearer {minted.json()['token']}"}
        upload = httpx.post(
            f"{base}/v1/shares/{share_id}/files/a.png/upload-url", headers=file_headers, timeout=60
        )
        if upload.status_code == 200:
            query = upload.json()["uploadUrl"].split("?", 1)[1]
            put = httpx.put(
                f"{base}/v1/shares/{share_id}/files/a.png/content?{query}",
                content=b"\x89PNG",
                timeout=60,
            )
            put_status = [put.status_code]
        else:
            put_status = []
        download = httpx.get(
            f"{base}/v1/shares/{share_id}/files/a.png/download-url",
            headers=file_headers,
            timeout=60,
        )
        if download.status_code != 200:
            return [minted.status_code, upload.status_code, *put_status, download.status_code]
        query = download.json()["downloadUrl"].split("?", 1)[1]
        content = httpx.get(f"{base}/v1/shares/{share_id}/files/a.png/content?{query}", timeout=60)
        return [
            minted.status_code,
            upload.status_code,
            *put_status,
            download.status_code,
            content.status_code,
        ]

    before = DB_POOL_TIMEOUTS_TOTAL._value.get()
    with ThreadPoolExecutor(max_workers=CLIENTS) as pool:
        statuses = [s for group in pool.map(client, range(CLIENTS)) for s in group]
    return statuses, DB_POOL_TIMEOUTS_TOTAL._value.get() - before


def test_mixed_cas_burst_has_no_pool_timeouts(cas_env, monkeypatch):
    server, engine, share_id, token, entered = cas_env
    monkeypatch.setattr(admission.DbAdmissionGate, "MAX_IN_FLIGHT", POOL_SIZE - 1)
    monkeypatch.setattr(admission, "db_admission_gate", admission.DbAdmissionGate())

    statuses, pool_timeouts = _burst(server, share_id, token)

    assert pool_timeouts == 0
    assert not [s for s in statuses if s >= 500], sorted(set(statuses))
    assert set(statuses) <= {200, 429}, sorted(set(statuses))
    assert engine.pool.checkedout() == 0


def test_mixed_cas_burst_without_gate_reproduces_pool_timeout(cas_env, monkeypatch):
    """Negative control: with admission opened up the same burst times out."""
    server, engine, share_id, token, entered = cas_env
    monkeypatch.setattr(admission.DbAdmissionGate, "MAX_IN_FLIGHT", 10_000)
    monkeypatch.setattr(admission, "db_admission_gate", admission.DbAdmissionGate())

    statuses, pool_timeouts = _burst(server, share_id, token)

    assert pool_timeouts >= 1
    assert any(s >= 500 for s in statuses), sorted(set(statuses))


def _gate_free_slots(gate):
    return [sem._value for sem in gate._semaphores.values()]


def test_saturated_gate_answers_429_retry_after_within_bound(cas_env, monkeypatch):
    server, engine, share_id, token, entered = cas_env
    monkeypatch.setattr(admission.DbAdmissionGate, "MAX_IN_FLIGHT", 1)
    monkeypatch.setattr(admission.DbAdmissionGate, "WAIT_SECONDS", 0.2)
    gate = admission.DbAdmissionGate()
    monkeypatch.setattr(admission, "db_admission_gate", gate)
    headers = {"Authorization": f"Bearer {token}"}
    body = {"path": "a.png", "sha256": "0" * 64, "content_type": "image/png", "content_length": 4}

    def mint(_):
        started = time.monotonic()
        r = httpx.post(
            f"{server.base_url}/v1/shares/{share_id}/file-token",
            json=body,
            headers=headers,
            timeout=60,
        )
        return r.status_code, r.headers.get("Retry-After"), time.monotonic() - started

    with ThreadPoolExecutor(max_workers=CLIENTS) as pool:
        results = list(pool.map(mint, range(CLIENTS)))

    rejected = [r for r in results if r[0] == 429]
    assert rejected and any(r[0] == 200 for r in results)
    assert {r[1] for r in rejected} == {str(admission.DbAdmissionGate.RETRY_AFTER_SECONDS)}
    wait = admission.DbAdmissionGate.WAIT_SECONDS
    assert max(r[2] for r in rejected) < wait + 2.0, "waiters must be bounded by WAIT_SECONDS"
    assert not [r for r in results if r[0] >= 500]
    assert engine.pool.checkedout() == 0
    assert _gate_free_slots(gate) == [1]


def test_slots_and_sessions_released_after_error_and_client_abort(cas_env, monkeypatch):
    server, engine, share_id, token, entered = cas_env
    monkeypatch.setattr(admission.DbAdmissionGate, "MAX_IN_FLIGHT", POOL_SIZE - 1)
    gate = admission.DbAdmissionGate()
    monkeypatch.setattr(admission, "db_admission_gate", gate)
    headers = {"Authorization": f"Bearer {token}"}
    base = server.base_url
    body = {"path": "a.png", "sha256": "0" * 64, "content_type": "image/png", "content_length": 4}

    def errors(_):
        # Invalid file-token on the content route: raises after the slot and
        # the session are taken.
        return httpx.get(
            f"{base}/v1/shares/{share_id}/files/a.png/content?token=garbage", timeout=60
        ).status_code

    def abort_in_flight():
        # Open raw connections, wait until every handler is inside the slow
        # share lookup (slot and session held), then drop the sockets.
        parsed = urlparse(base)
        payload = json.dumps(body).encode()
        request = (
            f"POST /v1/shares/{share_id}/file-token HTTP/1.1\r\n"
            f"Host: {parsed.netloc}\r\n"
            f"Authorization: Bearer {token}\r\n"
            "Content-Type: application/json\r\n"
            f"Content-Length: {len(payload)}\r\n\r\n"
        ).encode() + payload
        before = len(entered)
        socks = [
            socket.create_connection((parsed.hostname, parsed.port)) for _ in range(POOL_SIZE - 1)
        ]
        for sock in socks:
            sock.sendall(request)
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and len(entered) - before < len(socks):
            time.sleep(0.005)
        in_flight = len(entered) - before
        for sock in socks:
            sock.close()
        return in_flight

    with ThreadPoolExecutor(max_workers=CLIENTS) as pool:
        statuses = list(pool.map(errors, range(CLIENTS)))
    in_flight = abort_in_flight()

    assert in_flight == POOL_SIZE - 1, "aborted requests never reached the handler"
    assert set(statuses) == {401}, sorted(set(statuses))
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline and (
        engine.pool.checkedout() or _gate_free_slots(gate) != [POOL_SIZE - 1]
    ):
        time.sleep(0.05)
    assert engine.pool.checkedout() == 0
    assert _gate_free_slots(gate) == [POOL_SIZE - 1]
    ok = httpx.post(
        f"{base}/v1/shares/{share_id}/file-token", json=body, headers=headers, timeout=60
    )
    assert ok.status_code == 200
