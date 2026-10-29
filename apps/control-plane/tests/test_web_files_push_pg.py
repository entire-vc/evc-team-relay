"""POST /v1/web/shares/{slug}/files on real PostgreSQL.

27.10 incident: the route read the whole shares.web_folder_items column
(8.8 MB for a 1224-file share), changed one item and wrote the column back,
on every pushed file. Two clients pushing the same share queued on one row
while holding pool connections, and lost each other's items.

Checks: an unchanged push writes nothing, parallel pushes to different paths
all land, and the no-op push does not slow down with the size of the share.

Needs PG_TEST_DATABASE_URL or FAULT_TEST_DATABASE_URL (a disposable database;
the schema is dropped and recreated). CI runs it in test-python-faults.
"""

from __future__ import annotations

import hashlib
import json
import os
import statistics
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

import httpx
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import QueuePool
from test_fault_injection import LiveServer, _reset_rate_limiters

from app.api.routers import tokens, web
from app.core import security
from app.core.config import get_settings
from app.db import models
from app.db import session as session_module
from app.db.session import InstrumentedQueuePool
from app.main import build_app

PG_URL = os.environ.get("PG_TEST_DATABASE_URL") or os.environ.get("FAULT_TEST_DATABASE_URL")

needs_pg = pytest.mark.skipif(not PG_URL, reason="no PostgreSQL test database configured")

# Roughly the spark share: 8.8 MB over 1224 items.
ITEM_CONTENT = "x" * 7000
LARGE_SHARE_ITEMS = 1500
SMALL_SHARE_ITEMS = 10
# A no-op push may cost this much more on the large share than on the small
# one. Before the fix it parsed and rewrote ~10 MB per request.
NO_OP_SIZE_PENALTY_SECONDS = 0.1


@dataclass
class PushEnv:
    server: LiveServer
    engine: Engine
    token: str
    slugs: dict[str, uuid.UUID]

    def push(self, slug: str, path: str, content: str) -> httpx.Response:
        return httpx.post(
            f"{self.server.base_url}/v1/web/shares/{slug}/files",
            params={"path": path},
            json={"content": content},
            headers={"Authorization": f"Bearer {self.token}"},
            timeout=30,
        )

    def row_version(self, slug: str) -> tuple[str, object]:
        with self.engine.connect() as conn:
            return tuple(
                conn.execute(
                    text("SELECT xmin::text, updated_at FROM shares WHERE id = :id"),
                    {"id": self.slugs[slug]},
                ).one()
            )

    def items(self, slug: str) -> dict[str, dict]:
        with self.engine.connect() as conn:
            items = conn.execute(
                text("SELECT web_folder_items FROM shares WHERE id = :id"),
                {"id": self.slugs[slug]},
            ).scalar_one()
        return {item["path"]: item for item in items or []}


def _seed_items(count: int) -> list[dict]:
    sha = hashlib.sha256(ITEM_CONTENT.encode()).hexdigest()
    return [
        {
            "path": f"seed/{n:05d}.md",
            "name": f"{n:05d}.md",
            "type": "doc",
            "content": ITEM_CONTENT,
            "sha256": sha,
            "size": len(ITEM_CONTENT),
            "modified_at": "2026-10-27T00:00:00+00:00",
            "source": "sync-artifact",
        }
        for n in range(count)
    ]


@pytest.fixture
def push_env(monkeypatch):
    engine = create_engine(
        PG_URL, poolclass=InstrumentedQueuePool, pool_size=10, max_overflow=0, pool_timeout=10
    )
    models.Base.metadata.drop_all(engine)
    models.Base.metadata.create_all(engine)

    factory = sessionmaker(bind=engine, autoflush=False)
    slugs: dict[str, uuid.UUID] = {}
    with factory() as db:
        user = models.User(email="push@example.com", password_hash="unused", is_active=True)
        db.add(user)
        db.flush()
        for slug, count in (("small", SMALL_SHARE_ITEMS), ("large", LARGE_SHARE_ITEMS)):
            share = models.Share(
                owner_user_id=user.id,
                path=slug,
                kind=models.ShareKind.FOLDER,
                visibility=models.ShareVisibility.PRIVATE,
                web_published=True,
                web_slug=slug,
                web_folder_items=_seed_items(count),
            )
            db.add(share)
            db.flush()
            slugs[slug] = share.id
        db.commit()
        user_id = user.id

    monkeypatch.setenv("WEB_PUBLISH_DOMAIN", "docs.test")
    get_settings.cache_clear()
    monkeypatch.setattr(session_module, "_engine", engine)
    monkeypatch.setattr(session_module, "_SessionLocal", factory)
    monkeypatch.setattr(session_module, "_probe_engine", None)
    _reset_rate_limiters()

    server = LiveServer(build_app())
    try:
        yield PushEnv(
            server=server,
            engine=engine,
            token=security.create_access_token(str(user_id)),
            slugs=slugs,
        )
    finally:
        server.close()
        engine.dispose()
        get_settings.cache_clear()


def test_push_gate_and_relay_gate_fit_in_default_pool():
    # QueuePool defaults the control plane runs on: pool_size 5, max_overflow 10.
    pool = QueuePool(lambda: None)
    in_flight = web._WebFilePushGate.MAX_IN_FLIGHT + tokens._RelayTokenGate.MAX_IN_FLIGHT
    assert in_flight < pool.size() + pool._max_overflow


@needs_pg
@pytest.mark.db_commit
@pytest.mark.parametrize("path", ["seed/00003.md", "new/page.md"])
def test_unchanged_push_writes_nothing(push_env, path):
    first = push_env.push("small", path, "# same body\n")
    assert first.status_code == 200, first.text
    after_first = push_env.row_version("small")

    second = push_env.push("small", path, "# same body\n")
    assert second.status_code == 200, second.text
    assert second.json()["message"] == "File content unchanged"
    assert push_env.row_version("small") == after_first, "no-op push must not write the row"

    changed = push_env.push("small", path, "# new body\n")
    assert changed.status_code == 200, changed.text
    assert push_env.row_version("small") != after_first
    item = push_env.items("small")[path]
    assert item["content"] == "# new body\n"
    assert item["sha256"] == hashlib.sha256(b"# new body\n").hexdigest()


@needs_pg
@pytest.mark.db_commit
def test_parallel_pushes_to_different_paths_all_land(push_env):
    paths = [f"parallel/{n:02d}.md" for n in range(24)] + [
        f"seed/{n:05d}.md" for n in range(SMALL_SHARE_ITEMS)
    ]
    with ThreadPoolExecutor(max_workers=12) as pool:
        responses = list(pool.map(lambda p: push_env.push("small", p, f"# {p}\n"), paths))

    assert [r.status_code for r in responses] == [200] * len(paths), [
        r.text for r in responses if r.status_code != 200
    ]
    items = push_env.items("small")
    missing = [p for p in paths if items.get(p, {}).get("content") != f"# {p}\n"]
    assert not missing, f"lost updates: {missing}"
    assert len(items) == len(paths)


def _median_no_op_seconds(env: PushEnv, slug: str, runs: int = 15) -> float:
    path = "seed/00001.md"
    assert env.push(slug, path, ITEM_CONTENT).json()["message"] == "File content unchanged"
    timings = []
    for _ in range(runs):
        started = time.perf_counter()
        response = env.push(slug, path, ITEM_CONTENT)
        timings.append(time.perf_counter() - started)
        assert response.status_code == 200, response.text
    return statistics.median(timings)


@needs_pg
@pytest.mark.db_commit
def test_no_op_push_does_not_scale_with_share_size(push_env):
    small = _median_no_op_seconds(push_env, "small")
    large = _median_no_op_seconds(push_env, "large")

    started = time.perf_counter()
    changed = push_env.push("large", "seed/00002.md", "# changed\n")
    changed_seconds = time.perf_counter() - started
    assert changed.status_code == 200, changed.text

    print(
        f"\nno-op push median: {SMALL_SHARE_ITEMS} items {small * 1000:.1f} ms, "
        f"{LARGE_SHARE_ITEMS} items {large * 1000:.1f} ms; "
        f"changed push on {LARGE_SHARE_ITEMS} items {changed_seconds * 1000:.1f} ms"
    )
    assert large - small < NO_OP_SIZE_PENALTY_SECONDS


@needs_pg
@pytest.mark.db_commit
def test_folder_items_patch_waits_for_a_concurrent_push(push_env):
    """PATCH /shares/{id} merges web_folder_items from a fresh read; that read
    has to wait for an in-flight writer, or the writer's item is dropped."""
    share_id = push_env.slugs["small"]
    racer = {
        "path": "racer.md",
        "name": "racer.md",
        "type": "doc",
        "content": "# racer\n",
        "sha256": hashlib.sha256(b"# racer\n").hexdigest(),
        "source": "sync-artifact",
    }
    result: dict[str, httpx.Response] = {}

    def patch() -> None:
        result["response"] = httpx.patch(
            f"{push_env.server.base_url}/v1/shares/{share_id}",
            json={"web_folder_items": [{"path": "seed/00000.md", "name": "a", "type": "doc"}]},
            headers={"Authorization": f"Bearer {push_env.token}"},
            timeout=30,
        )

    with push_env.engine.connect() as writer:
        tx = writer.begin()
        writer.execute(text("SELECT 1 FROM shares WHERE id = :id FOR UPDATE"), {"id": share_id})
        writer.execute(
            text(
                "UPDATE shares SET web_folder_items = web_folder_items "
                "|| jsonb_build_array(CAST(:item AS jsonb)) WHERE id = :id"
            ),
            {"id": share_id, "item": json.dumps(racer)},
        )
        thread = threading.Thread(target=patch)
        thread.start()
        thread.join(timeout=1.5)
        assert thread.is_alive(), "PATCH must wait for the share row lock"
        tx.commit()
    thread.join(timeout=20)

    assert result["response"].status_code == 200, result["response"].text
    items = push_env.items("small")
    assert items.get("racer.md", {}).get("content") == "# racer\n", "PATCH dropped the push"
    assert items["seed/00000.md"]["name"] == "a"


@needs_pg
@pytest.mark.db_commit
def test_same_hash_push_fills_an_item_without_inline_content(push_env):
    """A sync-write item keeps its body in object storage (`content` absent or
    JSON null); a push with the same hash must still write the inline copy."""
    body = "# stored elsewhere\n"
    sha = hashlib.sha256(body.encode()).hexdigest()
    with push_env.engine.begin() as conn:
        conn.execute(
            text(
                "UPDATE shares SET web_folder_items = web_folder_items "
                "|| CAST(:items AS jsonb) WHERE id = :id"
            ),
            {
                "id": push_env.slugs["small"],
                "items": json.dumps(
                    [
                        {
                            "path": "null.md",
                            "name": "null.md",
                            "type": "doc",
                            "sha256": sha,
                            "content": None,
                            "source": "sync-artifact",
                        },
                        {
                            "path": "absent.md",
                            "name": "absent.md",
                            "type": "doc",
                            "sha256": sha,
                            "source": "sync-artifact",
                        },
                    ]
                ),
            },
        )

    for path in ("null.md", "absent.md"):
        response = push_env.push("small", path, body)
        assert response.status_code == 200, response.text
        assert response.json()["message"] == "File content synced", path
        assert push_env.items("small")[path]["content"] == body
