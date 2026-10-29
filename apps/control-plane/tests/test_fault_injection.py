"""Fault injection: slow/down object storage and relay, client aborts, retries.

Runs a real uvicorn server against real PostgreSQL. The object store is a
small S3 stand-in on a local socket that can answer, stall, or be absent, so
the control plane goes through its real MinIO client, timeouts and retries.

For every fault the request must end in a bounded 503/504 instead of hanging,
the request pool must return to its baseline, nothing may stay
`idle in transaction`, and /v1/health/ready and /metrics must keep answering
in under 2 s while the fault lasts.

Needs FAULT_TEST_DATABASE_URL (a disposable PostgreSQL database; the schema
is dropped and recreated). Skipped without it; CI runs it in test-python-faults.
"""

from __future__ import annotations

import hashlib
import os
import secrets
import socket
import threading
import time
import uuid
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

import httpx
import pytest
import uvicorn
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool

from app.api.routers import metrics as metrics_router
from app.api.routers import shares, tokens, web
from app.core import security
from app.core.config import get_settings
from app.db import models
from app.db import session as session_module
from app.db.session import InstrumentedQueuePool
from app.main import build_app

PG_URL = os.environ.get("FAULT_TEST_DATABASE_URL")

pytestmark = [
    pytest.mark.db_commit,
    pytest.mark.skipif(not PG_URL, reason="FAULT_TEST_DATABASE_URL is not set"),
]

# A stalled object store answers after this long — well past any deadline.
STALL_SECONDS = 15.0
# A faulted request must have ended by now (storage timeouts are 2 s connect,
# 8 s read, one connect retry).
REQUEST_DEADLINE_SECONDS = 12.0
# How long the client waits before calling a request hung.
CLIENT_TIMEOUT_SECONDS = 25.0
PROBE_DEADLINE_SECONDS = 2.0
POOL_SIZE = 3

DOC_PATH = "notes/a.md"
DOC_BODY = b"# a\n\noriginal\n"
DOC_SHA = hashlib.sha256(DOC_BODY).hexdigest()


# --- Object store stand-in ---------------------------------------------------


class ObjectStore:
    """Minimal S3 endpoint: bucket probe, PUT and GET of objects.

    `stall` makes every request hang for STALL_SECONDS without answering.
    """

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}
        self.stall = False
        self.requests = 0
        store = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args) -> None:  # silence stderr
                pass

            def _stalled(self) -> bool:
                store.requests += 1
                if not store.stall:
                    return False
                time.sleep(STALL_SECONDS)
                self.close_connection = True
                return True

            def _reply(self, code: int, body: bytes = b"", headers: dict | None = None) -> None:
                self.send_response(code)
                for name, value in (headers or {}).items():
                    self.send_header(name, value)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                if self.command != "HEAD":
                    self.wfile.write(body)

            def _key(self) -> str:
                return urlsplit(self.path).path.lstrip("/")

            def do_HEAD(self) -> None:
                if not self._stalled():
                    self._reply(200)

            def do_GET(self) -> None:
                if self._stalled():
                    return
                if "location" in (urlsplit(self.path).query or ""):
                    self._reply(
                        200,
                        b'<?xml version="1.0" encoding="UTF-8"?>'
                        b"<LocationConstraint>us-east-1</LocationConstraint>",
                        {"Content-Type": "application/xml"},
                    )
                    return
                data = store.objects.get(self._key())
                if data is None:
                    self._reply(
                        404,
                        b'<?xml version="1.0" encoding="UTF-8"?><Error><Code>NoSuchKey</Code>'
                        b"<Message>missing</Message></Error>",
                        {"Content-Type": "application/xml"},
                    )
                    return
                self._reply(200, data, {"Content-Type": "application/octet-stream"})

            def do_PUT(self) -> None:
                length = int(self.headers.get("Content-Length") or 0)
                body = self.rfile.read(length)
                if self._stalled():
                    return
                store.objects[self._key()] = body
                self._reply(200, headers={"ETag": f'"{hashlib.md5(body).hexdigest()}"'})

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._server.daemon_threads = True
        threading.Thread(target=self._server.serve_forever, daemon=True).start()

    @property
    def endpoint(self) -> str:
        return f"127.0.0.1:{self._server.server_address[1]}"

    def close(self) -> None:
        self._server.shutdown()
        self._server.server_close()


def _closed_port() -> int:
    """A local port with nothing listening: connections are refused at once."""
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class ConnectionCounter:
    """Listener that only counts connection attempts and never answers."""

    def __init__(self) -> None:
        self.connections = 0
        self._sock = socket.socket()
        self._sock.bind(("127.0.0.1", 0))
        self._sock.listen(16)
        self._sock.settimeout(0.2)
        self.port = self._sock.getsockname()[1]
        self._stop = threading.Event()
        threading.Thread(target=self._accept, daemon=True).start()

    def _accept(self) -> None:
        while not self._stop.is_set():
            try:
                conn, _ = self._sock.accept()
            except TimeoutError:
                continue
            except OSError:
                return  # closed
            self.connections += 1
            conn.close()

    def close(self) -> None:
        self._stop.set()
        self._sock.close()


# --- Live server --------------------------------------------------------------


class LiveServer:
    def __init__(self, app) -> None:
        sock = socket.socket()
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(("127.0.0.1", 0))
        self.base_url = f"http://127.0.0.1:{sock.getsockname()[1]}"
        config = uvicorn.Config(
            app, log_level="warning", lifespan="off", timeout_graceful_shutdown=1
        )
        self._server = uvicorn.Server(config)
        self._thread = threading.Thread(
            target=self._server.run, kwargs={"sockets": [sock]}, daemon=True
        )
        self._thread.start()
        deadline = time.monotonic() + 10
        while not self._server.started:
            assert time.monotonic() < deadline, "uvicorn did not start"
            time.sleep(0.02)

    def close(self) -> None:
        self._server.should_exit = True
        self._thread.join(timeout=10)


# --- Environment ----------------------------------------------------------------


@dataclass
class FaultEnv:
    server: LiveServer
    store: ObjectStore
    engine: Engine
    monitor: Engine
    share_id: uuid.UUID
    token: str
    agent_key: str
    monkeypatch: pytest.MonkeyPatch
    probe_latencies: list[float] = field(default_factory=list)

    @property
    def auth(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token}"}

    def set_storage(self, endpoint: str) -> None:
        self.monkeypatch.setenv("MINIO_ENDPOINT", endpoint)
        get_settings.cache_clear()

    def storage_down(self) -> None:
        self.set_storage(f"127.0.0.1:{_closed_port()}")

    def storage_up(self) -> None:
        self.store.stall = False
        self.set_storage(self.store.endpoint)

    def storage_stalled(self) -> None:
        self.store.stall = True
        self.set_storage(self.store.endpoint)

    def idle_in_transaction(self) -> int:
        with self.monitor.connect() as conn:
            return conn.execute(
                text(
                    "SELECT count(*) FROM pg_stat_activity WHERE datname = current_database() "
                    "AND state LIKE 'idle in transaction%' AND pid <> pg_backend_pid()"
                )
            ).scalar_one()

    def folder_item(self) -> dict | None:
        with self.monitor.connect() as conn:
            items = conn.execute(
                text("SELECT web_folder_items FROM shares WHERE id = :id"),
                {"id": self.share_id},
            ).scalar_one()
        return next((i for i in items or [] if i.get("path") == DOC_PATH), None)


def _reset_rate_limiters() -> None:
    for limiter in (shares.limiter, web.limiter, tokens.limiter, metrics_router._limiter):
        if limiter._storage:
            limiter._storage.reset()


@pytest.fixture
def fault_env(monkeypatch):
    engine = create_engine(
        PG_URL,
        poolclass=InstrumentedQueuePool,
        pool_size=POOL_SIZE,
        max_overflow=0,
        pool_timeout=3,
        pool_pre_ping=True,
    )
    monitor = create_engine(PG_URL, poolclass=NullPool)
    models.Base.metadata.drop_all(engine)
    models.Base.metadata.create_all(engine)

    factory = sessionmaker(bind=engine, autoflush=False)
    with factory() as db:
        user = models.User(email="faults@example.com", password_hash="unused", is_active=True)
        db.add(user)
        db.flush()
        share_id = uuid.uuid4()
        share = models.Share(
            id=share_id,
            owner_user_id=user.id,
            path="notes",
            kind=models.ShareKind.FOLDER,
            visibility=models.ShareVisibility.PRIVATE,
            web_published=True,
            web_slug="notes-faults",
            web_folder_items=[
                {
                    "path": DOC_PATH,
                    "name": "a.md",
                    "type": "doc",
                    "source": "sync-artifact",
                    "mime": "text/markdown",
                    "size": len(DOC_BODY),
                    "sha256": DOC_SHA,
                    "modified_at": "2026-10-26T00:00:00+00:00",
                    # No inline copy: reads have to go to object storage.
                    "storage_key": f"sync-uploads/{share_id}/{DOC_SHA}",
                }
            ],
        )
        db.add(share)
        raw_key = "tr_agent_" + secrets.token_hex(24)
        db.add(
            models.ShareAgentKey(
                share_id=share_id,
                key_hash=hashlib.sha256(raw_key.encode()).hexdigest(),
                label="fault-injection key",
                scopes="read,write",
                created_by=user.id,
            )
        )
        db.commit()
        user_id = user.id

    store = ObjectStore()
    store.objects[f"{get_settings().minio_bucket}/sync-uploads/{share_id}/{DOC_SHA}"] = DOC_BODY

    monkeypatch.setenv("WEB_PUBLISH_DOMAIN", "docs.test")
    monkeypatch.setenv("MINIO_ENDPOINT", store.endpoint)
    get_settings.cache_clear()
    monkeypatch.setattr(session_module, "_engine", engine)
    monkeypatch.setattr(session_module, "_SessionLocal", factory)
    monkeypatch.setattr(session_module, "_probe_engine", None)
    _reset_rate_limiters()

    app = build_app()
    # Lifespan is off (no background workers); load the signing keys it would.
    keys = security.load_or_generate_relay_keypair(get_settings())
    app.state.relay_private_key, app.state.relay_public_key, app.state.relay_key_id = keys
    server = LiveServer(app)
    env = FaultEnv(
        server=server,
        store=store,
        engine=engine,
        monitor=monitor,
        share_id=share_id,
        token=security.create_access_token(str(user_id)),
        agent_key=raw_key,
        monkeypatch=monkeypatch,
    )
    try:
        yield env
    finally:
        server.close()
        store.close()
        if session_module._probe_engine is not None:
            session_module._probe_engine.dispose()
        engine.dispose()
        monitor.dispose()
        get_settings.cache_clear()


# --- Measurement ------------------------------------------------------------------


@dataclass
class Outcome:
    status: int | None  # None: the client gave up — the request hung
    elapsed: float
    probe_max: float
    idle_in_tx_during: int
    checked_out_after: int
    idle_in_tx_after: int


def _wait_pool_settled(env: FaultEnv, baseline: int, timeout: float = 5.0) -> int:
    deadline = time.monotonic() + timeout
    while env.engine.pool.checkedout() != baseline and time.monotonic() < deadline:
        time.sleep(0.05)
    return env.engine.pool.checkedout()


def probe_ready_and_metrics(env: FaultEnv) -> float:
    """Slowest of /v1/health/ready and /metrics; inf if one did not answer."""
    probes: list[float] = []
    with httpx.Client(base_url=env.server.base_url, timeout=10) as probe:
        for path in ("/v1/health/ready", "/metrics"):
            started = time.perf_counter()
            try:
                resp = probe.get(path)
            except httpx.TimeoutException:
                probes.append(float("inf"))  # the event loop is stuck
                continue
            probes.append(time.perf_counter() - started)
            assert resp.status_code == 200, (path, resp.status_code, resp.text[:200])
    return max(probes)


def run_faulted(env: FaultEnv, send) -> Outcome:
    """Run `send(client)` while probing readiness and metrics alongside it."""
    baseline = env.engine.pool.checkedout()
    result: dict = {}

    def worker() -> None:
        started = time.perf_counter()
        try:
            with httpx.Client(base_url=env.server.base_url, timeout=CLIENT_TIMEOUT_SECONDS) as c:
                result["status"] = send(c).status_code
        except httpx.TimeoutException:
            result["status"] = None
        result["elapsed"] = time.perf_counter() - started

    thread = threading.Thread(target=worker)
    thread.start()
    time.sleep(1.0)  # the request is now inside the fault

    idle_during = env.idle_in_transaction()
    probe_max = probe_ready_and_metrics(env)

    thread.join(CLIENT_TIMEOUT_SECONDS + 5)
    return Outcome(
        status=result.get("status"),
        elapsed=result.get("elapsed", float("inf")),
        probe_max=probe_max,
        idle_in_tx_during=idle_during,
        checked_out_after=_wait_pool_settled(env, baseline),
        idle_in_tx_after=env.idle_in_transaction(),
    )


def assert_contained(outcome: Outcome, fault: str, *, idle_during_allowed: bool = False) -> None:
    assert outcome.status is not None, f"request hung past {CLIENT_TIMEOUT_SECONDS}s"
    # A stalled store times out (504); a store that refuses connections is down (503).
    assert outcome.status == EXPECTED_STATUS[fault], outcome
    assert outcome.elapsed < REQUEST_DEADLINE_SECONDS, outcome
    assert outcome.probe_max < PROBE_DEADLINE_SECONDS, outcome
    assert outcome.checked_out_after == 0, outcome
    assert outcome.idle_in_tx_after == 0, outcome
    assert outcome.idle_in_tx_during == 0, outcome


EXPECTED_STATUS = {"stalled": 504, "down": 503}


def _download(env: FaultEnv):
    return lambda c: c.get(
        f"/v1/shares/{env.share_id}/download", params={"path": DOC_PATH}, headers=env.auth
    )


def _sync_upload(env: FaultEnv, body: bytes = b"# uploaded\n"):
    return lambda c: c.post(
        f"/v1/web/shares/{env.share_id}/sync-upload",
        params={"path": "notes/uploaded.md"},
        content=body,
        headers={"X-Agent-Key": env.agent_key, "Content-Type": "text/markdown"},
    )


def _sync_write(env: FaultEnv, body: bytes, if_match: str = DOC_SHA):
    return lambda c: c.put(
        f"/v1/shares/{env.share_id}/sync-write",
        params={"path": DOC_PATH},
        content=body,
        headers={**env.auth, "If-Match": f'"{if_match}"', "Content-Type": "text/markdown"},
    )


# --- Object storage slow / down -------------------------------------------------


@pytest.mark.parametrize("fault", ["stalled", "down"])
def test_download_fails_fast_when_storage_faults(fault_env, fault):
    getattr(fault_env, f"storage_{fault}")()
    assert_contained(run_faulted(fault_env, _download(fault_env)), fault)


@pytest.mark.parametrize("fault", ["stalled", "down"])
def test_sync_upload_fails_fast_when_storage_faults(fault_env, fault):
    getattr(fault_env, f"storage_{fault}")()
    assert_contained(run_faulted(fault_env, _sync_upload(fault_env)), fault)
    assert fault_env.folder_item() is not None  # index untouched
    assert not any(i.get("path") == "notes/uploaded.md" for i in _items(fault_env))


@pytest.mark.parametrize("fault", ["stalled", "down"])
def test_conditional_write_fails_fast_and_leaves_index_when_storage_faults(fault_env, fault):
    getattr(fault_env, f"storage_{fault}")()
    # Text is content-addressed, so the object is written with no row lock
    # or open transaction held across the storage call.
    assert_contained(run_faulted(fault_env, _sync_write(fault_env, b"# a\n\nedited\n")), fault)
    assert fault_env.folder_item()["sha256"] == DOC_SHA


def test_stale_conditional_write_is_rejected_without_touching_storage(fault_env):
    fault_env.storage_stalled()
    with httpx.Client(base_url=fault_env.server.base_url, timeout=CLIENT_TIMEOUT_SECONDS) as c:
        started = time.perf_counter()
        resp = _sync_write(fault_env, b"# a\n\nstale\n", if_match="0" * 64)(c)
        elapsed = time.perf_counter() - started
    assert resp.status_code == 412, resp.text
    assert elapsed < PROBE_DEADLINE_SECONDS
    assert _wait_pool_settled(fault_env, 0) == 0
    assert fault_env.idle_in_transaction() == 0


def _items(env: FaultEnv) -> list[dict]:
    with env.monitor.connect() as conn:
        return (
            conn.execute(
                text("SELECT web_folder_items FROM shares WHERE id = :id"), {"id": env.share_id}
            ).scalar_one()
            or []
        )


# --- Healthy storage baseline (positive control for the harness) ---------------


def test_storage_stand_in_serves_the_happy_path(fault_env):
    with httpx.Client(base_url=fault_env.server.base_url, timeout=10) as c:
        resp = _download(fault_env)(c)
        assert resp.status_code == 200, resp.text
        assert resp.content == DOC_BODY
        assert _sync_upload(fault_env)(c).status_code == 200
    assert _wait_pool_settled(fault_env, 0) == 0


# --- Relay server unavailable ------------------------------------------------------


def test_relay_token_and_server_info_do_not_depend_on_relay(fault_env):
    """Both are answered locally: a dead relay-server is never contacted."""
    relay = ConnectionCounter()
    relay.close()  # nothing listening there any more
    fault_env.monkeypatch.setenv("RELAY_PUBLIC_URL", f"ws://127.0.0.1:{relay.port}")
    get_settings.cache_clear()

    with httpx.Client(base_url=fault_env.server.base_url, timeout=10) as c:
        started = time.perf_counter()
        info = c.get("/v1/server/info")
        token = c.post(
            "/v1/tokens/relay",
            json={"share_id": str(fault_env.share_id), "doc_id": "doc-1", "file_path": DOC_PATH},
            headers=fault_env.auth,
        )
        elapsed = time.perf_counter() - started
        probes = {}
        for path in ("/v1/health/ready", "/metrics"):
            probe_started = time.perf_counter()
            probes[path] = (c.get(path).status_code, time.perf_counter() - probe_started)

    for path, (code, took) in probes.items():
        assert code == 200, path
        assert took < PROBE_DEADLINE_SECONDS, (path, took)
    assert info.status_code == 200, info.text
    assert info.json()["relay_url"] == f"ws://127.0.0.1:{relay.port}"
    assert token.status_code == 200, token.text
    assert token.json()["token"]
    assert elapsed < PROBE_DEADLINE_SECONDS
    assert relay.connections == 0
    assert _wait_pool_settled(fault_env, 0) == 0
    assert fault_env.idle_in_transaction() == 0


# --- Client abort and reconnect ------------------------------------------------------


ABORT_AFTER_SECONDS = 1.0


def test_client_abort_mid_write_then_reconnect_applies_once(fault_env):
    fault_env.storage_stalled()
    baseline = fault_env.engine.pool.checkedout()
    edited = b"# a\n\nedited after reconnect\n"
    edited_sha = hashlib.sha256(edited).hexdigest()
    target = f"/v1/shares/{fault_env.share_id}/sync-write"
    headers = {**fault_env.auth, "If-Match": f'"{DOC_SHA}"', "Content-Type": "text/markdown"}

    with httpx.Client(base_url=fault_env.server.base_url, timeout=10) as client:
        # The client gives up while the write waits on storage and drops the
        # connection, as the plugin does on its own request timeout.
        with pytest.raises(httpx.ReadTimeout):
            client.put(
                target,
                params={"path": DOC_PATH},
                content=edited,
                headers=headers,
                timeout=ABORT_AFTER_SECONDS,
            )
        # Storage is still stalled and the abandoned request is still winding down.
        assert probe_ready_and_metrics(fault_env) < PROBE_DEADLINE_SECONDS

        # The abandoned request must wind down on its own within the deadline.
        assert _wait_pool_settled(fault_env, baseline, REQUEST_DEADLINE_SECONDS) == baseline
        assert fault_env.idle_in_transaction() == 0
        assert fault_env.folder_item()["sha256"] == DOC_SHA  # nothing half-applied

        # Reconnect: storage is back, the same client repeats the same write.
        fault_env.storage_up()
        first = client.put(target, params={"path": DOC_PATH}, content=edited, headers=headers)
        replay = client.put(target, params={"path": DOC_PATH}, content=edited, headers=headers)

    assert first.status_code == 200, first.text
    assert first.headers["ETag"] == f'"{edited_sha}"'
    # The replay carries the old tag: refused, never a second blind apply.
    assert replay.status_code == 412, replay.text
    assert fault_env.folder_item()["sha256"] == edited_sha
    stored = fault_env.store.objects[
        f"{get_settings().minio_bucket}/sync-uploads/{fault_env.share_id}/{edited_sha}"
    ]
    assert stored == edited
    assert _wait_pool_settled(fault_env, 0) == 0
    assert fault_env.idle_in_transaction() == 0


def test_client_abort_mid_download_then_reconnect(fault_env):
    fault_env.storage_stalled()
    baseline = fault_env.engine.pool.checkedout()
    target = f"/v1/shares/{fault_env.share_id}/download"

    with httpx.Client(base_url=fault_env.server.base_url, timeout=10) as client:
        with pytest.raises(httpx.ReadTimeout):
            client.get(
                target,
                params={"path": DOC_PATH},
                headers=fault_env.auth,
                timeout=ABORT_AFTER_SECONDS,
            )
        assert probe_ready_and_metrics(fault_env) < PROBE_DEADLINE_SECONDS
        assert _wait_pool_settled(fault_env, baseline, REQUEST_DEADLINE_SECONDS) == baseline
        assert fault_env.idle_in_transaction() == 0

        fault_env.storage_up()
        resp = client.get(target, params={"path": DOC_PATH}, headers=fault_env.auth)

    assert resp.status_code == 200, resp.text
    assert resp.content == DOC_BODY


# --- If-Match atomicity under concurrency ------------------------------------------


def test_concurrent_conditional_writes_have_exactly_one_winner(fault_env):
    barrier = threading.Barrier(2)
    statuses: list[int] = []

    def write(body: bytes) -> None:
        with httpx.Client(base_url=fault_env.server.base_url, timeout=15) as c:
            barrier.wait()
            statuses.append(_sync_write(fault_env, body)(c).status_code)

    bodies = [b"# a\n\nwriter one\n", b"# a\n\nwriter two\n"]
    threads = [threading.Thread(target=write, args=(b,)) for b in bodies]
    for t in threads:
        t.start()
    for t in threads:
        t.join(20)

    assert sorted(statuses) == [200, 412], statuses
    winner = fault_env.folder_item()["sha256"]
    assert winner in {hashlib.sha256(b).hexdigest() for b in bodies}
    assert _wait_pool_settled(fault_env, 0) == 0
    assert fault_env.idle_in_transaction() == 0
