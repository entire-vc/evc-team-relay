"""Origin of a sync-protocol version.

An Obsidian client mirrors its live CRDT text into the sync protocol with
`X-Sync-Origin: crdt`; files-index reports that as `origin=crdt` and every other
client's inbound downloader skips the item (the relay already delivered it).

What these tests pin:
  1. a user JWT asking for crdt gets crdt; without the header it is external;
  2. an agent key NEVER gets crdt, header or not: an agent write marked crdt
     would be skipped by every vault and silently never arrive;
  3. a later external write over a crdt item resets the origin (never inherited),
     through sync-write and through the agent sync-upload alike;
  4. legacy items without the field read as external;
  5. a structure-only PATCH of web_folder_items keeps the stored origin;
  6. server info advertises the capability the plugin gates its writer on.
"""

from __future__ import annotations

import hashlib
import secrets
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.db import models

SHARES = "/v1/shares"
WEB = "/v1/web/shares"


def login(
    client: TestClient, email: str = "testuser@example.com", password: str = "test123456"
) -> str:
    r = client.post("/auth/login", json={"email": email, "password": password})
    assert r.status_code == 200, r.text
    return r.json()["access_token"]


def bearer(token: str, **extra: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}", **extra}


def make_folder_share(
    db: Session,
    user: models.User,
    folder_items: list | None = None,
    slug: str | None = None,
) -> models.Share:
    share = models.Share(
        kind=models.ShareKind.FOLDER,
        path="SyncFolder/",
        visibility=models.ShareVisibility.PRIVATE,
        owner_user_id=user.id,
        web_published=slug is not None,
        web_slug=slug,
        web_folder_items=folder_items or [],
    )
    db.add(share)
    db.commit()
    db.refresh(share)
    return share


def make_agent_key(db: Session, share: models.Share, scopes: str = "read,write") -> str:
    raw_key = "tr_agent_" + secrets.token_hex(24)
    db.add(
        models.ShareAgentKey(
            share_id=share.id,
            key_hash=hashlib.sha256(raw_key.encode()).hexdigest(),
            label="origin test key",
            scopes=scopes,
            created_by=share.owner_user_id,
        )
    )
    db.commit()
    return raw_key


def sync_item(path: str, content: str, origin: str | None = None) -> dict:
    body = content.encode("utf-8")
    item = {
        "path": path,
        "name": path.split("/")[-1],
        "type": "doc",
        "source": "sync-artifact",
        "mime": "text/markdown",
        "size": len(body),
        "sha256": hashlib.sha256(body).hexdigest(),
        "modified_at": "2026-11-15T12:00:00+00:00",
        "storage_key": f"sync-uploads/x/{hashlib.sha256(body).hexdigest()}",
        "content": content,
    }
    if origin is not None:
        item["origin"] = origin
    return item


@pytest.fixture
def minio():
    mock_client = MagicMock()
    mock_client.bucket_exists.return_value = True
    mock_client.put_object.return_value = None
    with (
        patch("app.api.routers.shares._get_minio_client", return_value=mock_client),
        patch("app.api.routers.web._get_minio_client", return_value=mock_client),
    ):
        yield mock_client


@pytest.fixture
def web_enabled(monkeypatch):
    monkeypatch.setenv("WEB_PUBLISH_DOMAIN", "docs.test.com")
    from app.core.config import get_settings

    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def index_origin(client: TestClient, share_id, headers: dict, path: str) -> str:
    resp = client.get(f"{SHARES}/{share_id}/files-index", headers=headers)
    assert resp.status_code == 200, resp.text
    entries = {e["path"]: e for e in resp.json()}
    return entries[path]["origin"]


def sync_write(client, share_id, path, body: bytes, headers: dict, if_match: str | None = None):
    precondition = {"If-Match": f'"{if_match}"'} if if_match else {"If-None-Match": "*"}
    return client.put(
        f"{SHARES}/{share_id}/sync-write",
        params={"path": path},
        content=body,
        headers={"Content-Type": "text/markdown", **precondition, **headers},
    )


class TestSyncWriteOrigin:
    def test_user_jwt_with_header_is_crdt(
        self, client: TestClient, test_user: models.User, db_session: Session, minio
    ):
        share = make_folder_share(db_session, test_user)
        token = login(client)

        resp = sync_write(
            client, share.id, "a.md", b"# A", bearer(token, **{"X-Sync-Origin": "crdt"})
        )

        assert resp.status_code == 200, resp.text
        assert index_origin(client, share.id, bearer(token), "a.md") == "crdt"

    def test_user_jwt_without_header_is_external(
        self, client: TestClient, test_user: models.User, db_session: Session, minio
    ):
        share = make_folder_share(db_session, test_user)
        token = login(client)

        resp = sync_write(client, share.id, "a.md", b"# A", bearer(token))

        assert resp.status_code == 200, resp.text
        assert index_origin(client, share.id, bearer(token), "a.md") == "external"

    def test_agent_key_cannot_obtain_crdt(
        self, client: TestClient, test_user: models.User, db_session: Session, minio
    ):
        share = make_folder_share(db_session, test_user)
        raw_key = make_agent_key(db_session, share)

        resp = sync_write(
            client,
            share.id,
            "a.md",
            b"# A",
            {"X-Agent-Key": raw_key, "X-Sync-Origin": "crdt"},
        )

        assert resp.status_code == 200, resp.text
        assert index_origin(client, share.id, {"X-Agent-Key": raw_key}, "a.md") == "external"

    def test_agent_key_with_user_jwt_alongside_still_external(
        self, client: TestClient, test_user: models.User, db_session: Session, minio
    ):
        # The agent-key branch wins when both credentials are sent, so the
        # write is the key's and must not inherit the user's right to crdt.
        share = make_folder_share(db_session, test_user)
        raw_key = make_agent_key(db_session, share)
        token = login(client)

        resp = sync_write(
            client,
            share.id,
            "a.md",
            b"# A",
            bearer(token, **{"X-Agent-Key": raw_key, "X-Sync-Origin": "crdt"}),
        )

        assert resp.status_code == 200, resp.text
        assert index_origin(client, share.id, bearer(token), "a.md") == "external"

    def test_external_write_over_crdt_item_resets_origin(
        self, client: TestClient, test_user: models.User, db_session: Session, minio
    ):
        item = sync_item("a.md", "# A", origin="crdt")
        share = make_folder_share(db_session, test_user, folder_items=[item])
        raw_key = make_agent_key(db_session, share)

        resp = sync_write(
            client, share.id, "a.md", b"# A from Mesh", {"X-Agent-Key": raw_key}, item["sha256"]
        )

        assert resp.status_code == 200, resp.text
        assert index_origin(client, share.id, {"X-Agent-Key": raw_key}, "a.md") == "external"

    def test_stale_if_match_still_412_and_origin_untouched(
        self, client: TestClient, test_user: models.User, db_session: Session, minio
    ):
        item = sync_item("a.md", "# A", origin="crdt")
        share = make_folder_share(db_session, test_user, folder_items=[item])
        raw_key = make_agent_key(db_session, share)

        resp = sync_write(client, share.id, "a.md", b"# stale", {"X-Agent-Key": raw_key}, "0" * 64)

        assert resp.status_code == 412, resp.text
        assert index_origin(client, share.id, {"X-Agent-Key": raw_key}, "a.md") == "crdt"

    def test_legacy_item_without_origin_reads_external(
        self, client: TestClient, test_user: models.User, db_session: Session
    ):
        share = make_folder_share(db_session, test_user, folder_items=[sync_item("a.md", "# A")])
        token = login(client)

        assert index_origin(client, share.id, bearer(token), "a.md") == "external"


class TestOtherWritersOrigin:
    def test_agent_sync_upload_over_crdt_item_is_external(
        self,
        client: TestClient,
        test_user: models.User,
        db_session: Session,
        minio,
        web_enabled,
    ):
        item = sync_item("a.md", "# A", origin="crdt")
        share = make_folder_share(db_session, test_user, folder_items=[item], slug="origin-su")
        raw_key = make_agent_key(db_session, share)

        resp = client.post(
            f"{WEB}/{share.id}/sync-upload?path=a.md",
            content=b"# A from Mesh",
            headers={"X-Agent-Key": raw_key, "Content-Type": "text/markdown"},
        )

        assert resp.status_code == 200, resp.text
        assert index_origin(client, share.id, {"X-Agent-Key": raw_key}, "a.md") == "external"

    def test_web_file_push_by_user_with_header_is_crdt(
        self, client: TestClient, test_user: models.User, db_session: Session, web_enabled
    ):
        share = make_folder_share(
            db_session, test_user, folder_items=[sync_item("a.md", "# A")], slug="origin-push"
        )
        token = login(client)

        resp = client.post(
            f"{WEB}/origin-push/files",
            params={"path": "a.md"},
            json={"content": "# A, edited in Obsidian"},
            headers=bearer(token, **{"X-Sync-Origin": "crdt"}),
        )

        assert resp.status_code == 200, resp.text
        assert index_origin(client, share.id, bearer(token), "a.md") == "crdt"

    def test_web_file_push_by_agent_key_is_external(
        self, client: TestClient, test_user: models.User, db_session: Session, web_enabled
    ):
        share = make_folder_share(
            db_session,
            test_user,
            folder_items=[sync_item("a.md", "# A", origin="crdt")],
            slug="origin-push-key",
        )
        raw_key = make_agent_key(db_session, share)

        resp = client.post(
            f"{WEB}/origin-push-key/files",
            params={"path": "a.md"},
            json={"content": "# A, written by an agent"},
            headers={"X-Agent-Key": raw_key, "X-Sync-Origin": "crdt"},
        )

        assert resp.status_code == 200, resp.text
        assert index_origin(client, share.id, {"X-Agent-Key": raw_key}, "a.md") == "external"

    def test_structure_patch_keeps_origin(
        self, client: TestClient, test_user: models.User, db_session: Session
    ):
        share = make_folder_share(
            db_session, test_user, folder_items=[sync_item("a.md", "# A", origin="crdt")]
        )
        token = login(client)

        resp = client.patch(
            f"{SHARES}/{share.id}",
            json={"web_folder_items": [{"path": "a.md", "name": "a.md", "type": "doc"}]},
            headers=bearer(token),
        )

        assert resp.status_code == 200, resp.text
        assert index_origin(client, share.id, bearer(token), "a.md") == "crdt"


def test_server_info_advertises_sync_write_origin(client: TestClient):
    resp = client.get("/v1/server/info")

    assert resp.status_code == 200, resp.text
    assert resp.json()["features"]["sync_write_origin"] is True
