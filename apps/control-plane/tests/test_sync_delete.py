"""File-level delete on the sync protocol (#148db1fa).

A note trashed in Obsidian used to stay in files-index and /download forever:
the protocol had no delete, and a PATCH of the nav tree keeps sync-artifacts it
does not list (they may be versions a vault has not pulled yet). The client now
states a delete explicitly with DELETE /shares/{id}/sync-write and proves the
version it removes.

What these tests pin:
  1. a matching If-Match removes the entry: files-index drops it, /download 404,
     web_content_updated_at moves;
  2. a stale If-Match is 412 and leaves the entry, so a delete never erases a
     newer version (AC2);
  3. a missing entry is 404; no If-Match / If-Match: * is 428;
  4. rights equal PUT: owner/editor or an agent key with `write`; a viewer and a
     read-only key are refused;
  5. object storage is never touched: a newer upload racing the delete keeps its
     bytes, and a failed index write leaves the bytes too (review of the first
     head: removing a path-keyed asset lost data in both cases);
  6. the public asset URL stops serving a deleted sync asset: the delete commits
     a tombstone with the index removal (this covers objects written before sync
     writers tagged them), and a tagged object is served only while indexed;
     never-indexed web-publish uploads are served as before, and republishing a
     deleted path through web-publish clears its tombstone;
  7. a PATCH of the tree still keeps a sync-artifact absent from it (AC3), and a
     deleted entry is not resurrected by the next PATCH;
  8. server info advertises `features.sync_delete`.
"""

from __future__ import annotations

import base64
import hashlib
import secrets
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from minio.error import S3Error
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.routers import shares, web
from app.core import security
from app.core.sync_origin import SYNC_ASSET_METADATA
from app.db import models

SHARES = "/v1/shares"


def login(client: TestClient, email: str = "testuser@example.com") -> str:
    r = client.post("/auth/login", json={"email": email, "password": "test123456"})
    assert r.status_code == 200, r.text
    return r.json()["access_token"]


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def make_user(db: Session, email: str) -> models.User:
    user = models.User(
        email=email,
        password_hash=security.get_password_hash("test123456"),
        is_admin=False,
        is_active=True,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def make_folder_share(db: Session, user: models.User, folder_items: list) -> models.Share:
    share = models.Share(
        kind=models.ShareKind.FOLDER,
        path="SyncFolder/",
        visibility=models.ShareVisibility.PRIVATE,
        owner_user_id=user.id,
        web_folder_items=folder_items,
    )
    db.add(share)
    db.commit()
    db.refresh(share)
    return share


def add_member(db: Session, share: models.Share, user: models.User, role: models.ShareMemberRole):
    db.add(models.ShareMember(share_id=share.id, user_id=user.id, role=role))
    db.commit()


def make_agent_key(db: Session, share: models.Share, scopes: str) -> str:
    raw_key = "tr_agent_" + secrets.token_hex(24)
    db.add(
        models.ShareAgentKey(
            share_id=share.id,
            key_hash=hashlib.sha256(raw_key.encode()).hexdigest(),
            label="delete test key",
            scopes=scopes,
            created_by=share.owner_user_id,
        )
    )
    db.commit()
    return raw_key


def sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def text_item(path: str, content: str, origin: str = "crdt") -> dict:
    digest = sha(content)
    return {
        "path": path,
        "name": path.split("/")[-1],
        "type": "doc",
        "source": "sync-artifact",
        "mime": "text/markdown",
        "size": len(content.encode("utf-8")),
        "sha256": digest,
        "modified_at": "2026-11-18T02:51:05+00:00",
        "storage_key": f"sync-uploads/x/{digest}",
        "content": content,
        "origin": origin,
    }


def asset_item(share_id, path: str, digest: str) -> dict:
    return {
        "path": path,
        "name": path.split("/")[-1],
        "type": "asset",
        "source": "sync-artifact",
        "mime": "image/png",
        "size": 4,
        "sha256": digest,
        "modified_at": "2026-11-18T02:51:05+00:00",
        "storage_key": f"web-assets/{share_id}/{path}",
        "content": None,
        "origin": "external",
    }


@pytest.fixture
def minio():
    mock_client = MagicMock()
    mock_client.bucket_exists.return_value = True
    with patch("app.api.routers.shares._get_minio_client", return_value=mock_client):
        yield mock_client


def delete(client: TestClient, share_id, path: str, headers: dict, if_match: str | None):
    extra = {"If-Match": f'"{if_match}"'} if if_match is not None else {}
    return client.delete(
        f"{SHARES}/{share_id}/sync-write", params={"path": path}, headers={**headers, **extra}
    )


def index_paths(client: TestClient, share_id, headers: dict) -> set[str]:
    resp = client.get(f"{SHARES}/{share_id}/files-index", headers=headers)
    assert resp.status_code == 200, resp.text
    return {e["path"] for e in resp.json()}


class TestSyncDelete:
    def test_matching_if_match_removes_entry(
        self, client: TestClient, test_user: models.User, db_session: Session, minio
    ):
        item = text_item("_acceptance/roundtrip.md", "# roundtrip")
        keep = text_item("keep.md", "# keep")
        share = make_folder_share(db_session, test_user, [item, keep])
        token = login(client)
        assert share.web_content_updated_at is None

        resp = delete(client, share.id, item["path"], bearer(token), item["sha256"])

        assert resp.status_code == 204, resp.text
        assert index_paths(client, share.id, bearer(token)) == {"keep.md"}
        dl = client.get(
            f"{SHARES}/{share.id}/download", params={"path": item["path"]}, headers=bearer(token)
        )
        assert dl.status_code == 404, dl.text
        db_session.expire_all()
        refreshed = db_session.get(models.Share, share.id)
        assert refreshed.web_content_updated_at is not None
        # Content-addressed text object stays: another path may use the same bytes.
        minio.remove_object.assert_not_called()

    def test_stale_if_match_is_412_and_keeps_newer_version(
        self, client: TestClient, test_user: models.User, db_session: Session, minio
    ):
        newer = text_item("a.md", "# A, edited after the client last saw it", origin="external")
        share = make_folder_share(db_session, test_user, [newer])
        token = login(client)

        resp = delete(client, share.id, "a.md", bearer(token), sha("# A, the old base"))

        assert resp.status_code == 412, resp.text
        assert index_paths(client, share.id, bearer(token)) == {"a.md"}
        dl = client.get(
            f"{SHARES}/{share.id}/download", params={"path": "a.md"}, headers=bearer(token)
        )
        assert dl.status_code == 200
        assert dl.text == "# A, edited after the client last saw it"
        minio.remove_object.assert_not_called()

    def test_absent_entry_is_404(
        self, client: TestClient, test_user: models.User, db_session: Session, minio
    ):
        share = make_folder_share(db_session, test_user, [text_item("a.md", "# A")])
        token = login(client)

        resp = delete(client, share.id, "gone.md", bearer(token), sha("# whatever"))

        assert resp.status_code == 404, resp.text

    @pytest.mark.parametrize("if_match", [None, "*"])
    def test_unconditional_delete_is_428(
        self, client: TestClient, test_user: models.User, db_session: Session, minio, if_match
    ):
        share = make_folder_share(db_session, test_user, [text_item("a.md", "# A")])
        token = login(client)

        headers = bearer(token)
        if if_match is not None:
            headers = {**headers, "If-Match": if_match}
        resp = client.delete(
            f"{SHARES}/{share.id}/sync-write", params={"path": "a.md"}, headers=headers
        )

        assert resp.status_code == 428, resp.text
        assert index_paths(client, share.id, bearer(token)) == {"a.md"}

    def test_entry_without_sha_cannot_be_deleted(
        self, client: TestClient, test_user: models.User, db_session: Session, minio
    ):
        tree_only = {"path": "nav.md", "name": "nav.md", "type": "doc"}
        share = make_folder_share(db_session, test_user, [tree_only])
        token = login(client)

        resp = delete(client, share.id, "nav.md", bearer(token), sha(""))

        assert resp.status_code == 412, resp.text

    def test_editor_member_can_delete_viewer_cannot(
        self, client: TestClient, test_user: models.User, db_session: Session, minio
    ):
        item = text_item("a.md", "# A")
        share = make_folder_share(db_session, test_user, [item])
        editor = make_user(db_session, "editor@example.com")
        viewer = make_user(db_session, "viewer@example.com")
        add_member(db_session, share, editor, models.ShareMemberRole.EDITOR)
        add_member(db_session, share, viewer, models.ShareMemberRole.VIEWER)

        refused = delete(
            client, share.id, "a.md", bearer(login(client, viewer.email)), item["sha256"]
        )
        assert refused.status_code == 403, refused.text

        allowed = delete(
            client, share.id, "a.md", bearer(login(client, editor.email)), item["sha256"]
        )
        assert allowed.status_code == 204, allowed.text

    def test_agent_key_needs_write_scope(
        self, client: TestClient, test_user: models.User, db_session: Session, minio
    ):
        item = text_item("a.md", "# A")
        share = make_folder_share(db_session, test_user, [item])
        read_key = make_agent_key(db_session, share, "read")
        write_key = make_agent_key(db_session, share, "read,write")

        refused = delete(client, share.id, "a.md", {"X-Agent-Key": read_key}, item["sha256"])
        assert refused.status_code == 403, refused.text

        allowed = delete(client, share.id, "a.md", {"X-Agent-Key": write_key}, item["sha256"])
        assert allowed.status_code == 204, allowed.text
        assert index_paths(client, share.id, {"X-Agent-Key": write_key}) == set()

    def test_no_credentials_is_401(
        self, client: TestClient, test_user: models.User, db_session: Session, minio
    ):
        item = text_item("a.md", "# A")
        share = make_folder_share(db_session, test_user, [item])

        resp = delete(client, share.id, "a.md", {}, item["sha256"])

        assert resp.status_code == 401, resp.text

    def test_traversal_path_rejected(
        self, client: TestClient, test_user: models.User, db_session: Session, minio
    ):
        share = make_folder_share(db_session, test_user, [text_item("a.md", "# A")])
        token = login(client)

        resp = delete(client, share.id, "../a.md", bearer(token), sha("# A"))

        assert resp.status_code in (400, 422), resp.text
        assert index_paths(client, share.id, bearer(token)) == {"a.md"}


@pytest.fixture
def web_enabled(monkeypatch):
    monkeypatch.setenv("WEB_PUBLISH_DOMAIN", "docs.test.com")
    from app.core.config import get_settings

    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


class TestDeleteNeverLosesBytes:
    """The object store is an external boundary: a dict-backed fake stands in for it."""

    @staticmethod
    def fake_store(objects: dict) -> MagicMock:
        store = MagicMock()
        store.bucket_exists.return_value = True
        store.remove_object.side_effect = lambda bucket, key: objects.pop(key, None)

        def put(bucket, key, stream, **kwargs):
            objects[key] = stream.read()

        store.put_object.side_effect = put
        return store

    def test_newer_upload_racing_an_old_delete_keeps_its_bytes(
        self,
        client: TestClient,
        test_user: models.User,
        db_session: Session,
        db_connection,
        monkeypatch,
        web_enabled,
    ):
        path = "img/a.png"
        old, new = b"old image", b"newer image"
        old_sha = hashlib.sha256(old).hexdigest()
        share = make_folder_share(db_session, test_user, [])
        share.web_folder_items = [asset_item(share.id, path, old_sha)]
        db_session.commit()
        share_id = share.id
        key = f"web-assets/{share_id}/{path}"
        raw_key = make_agent_key(db_session, share, "read,write")
        objects = {key: old}
        store = self.fake_store(objects)
        put = store.put_object.side_effect

        def put_then_delete(bucket, object_key, stream, **kwargs):
            # The upload has written its bytes but not yet taken the row lock;
            # a delete of the old version lands in between.
            put(bucket, object_key, stream, **kwargs)
            with Session(bind=db_connection, join_transaction_mode="create_savepoint") as other:
                removed = shares._apply_sync_delete(other, share_id, path, {old_sha})
                assert removed["sha256"] == old_sha

        store.put_object.side_effect = put_then_delete
        monkeypatch.setattr(web, "_get_minio_client", lambda: store)
        monkeypatch.setattr(shares, "_get_minio_client", lambda: store)

        resp = client.post(
            f"/v1/web/shares/{share_id}/sync-upload",
            params={"path": path},
            content=new,
            headers={"X-Agent-Key": raw_key, "Content-Type": "image/png"},
        )

        assert resp.status_code == 200, resp.text
        db_session.expire_all()
        indexed = next(
            i for i in db_session.get(models.Share, share_id).web_folder_items if i["path"] == path
        )
        assert indexed["sha256"] == hashlib.sha256(new).hexdigest()
        assert objects.get(key) == new
        store.remove_object.assert_not_called()

    def test_failed_index_write_keeps_the_bytes(
        self, test_user: models.User, db_session: Session, db_connection, monkeypatch
    ):
        path = "img/a.png"
        old = b"old image"
        digest = hashlib.sha256(old).hexdigest()
        share = make_folder_share(db_session, test_user, [])
        share.web_folder_items = [asset_item(share.id, path, digest)]
        db_session.commit()
        share_id = share.id
        key = f"web-assets/{share_id}/{path}"
        objects = {key: old}
        store = self.fake_store(objects)
        monkeypatch.setattr(shares, "_get_minio_client", lambda: store)
        db_connection.exec_driver_sql(
            "CREATE TEMP TRIGGER reject_index_write BEFORE UPDATE OF web_folder_items ON shares "
            "BEGIN SELECT RAISE(ABORT, 'injected DB write failure'); END"
        )
        try:
            with Session(bind=db_connection, join_transaction_mode="create_savepoint") as other:
                with pytest.raises(IntegrityError):
                    shares._apply_sync_delete(other, share_id, path, {digest})
        finally:
            db_connection.exec_driver_sql("DROP TRIGGER reject_index_write")

        db_session.expire_all()
        items = db_session.get(models.Share, share_id).web_folder_items or []
        assert any(i["path"] == path for i in items)
        assert objects.get(key) == old
        # The tombstone rolls back with the index: the asset stays public.
        assert db_session.get(models.SyncAssetTombstone, (share_id, path)) is None

    def test_successful_delete_leaves_object_storage_alone(
        self, client: TestClient, test_user: models.User, db_session: Session, minio
    ):
        share = make_folder_share(db_session, test_user, [])
        digest = hashlib.sha256(b"\x89PNG").hexdigest()
        share.web_folder_items = [asset_item(share.id, "img/a.png", digest)]
        db_session.commit()
        token = login(client)

        resp = delete(client, share.id, "img/a.png", bearer(token), digest)

        assert resp.status_code == 204, resp.text
        assert index_paths(client, share.id, bearer(token)) == set()
        minio.remove_object.assert_not_called()


class _StoredObject:
    """What get_object returns: bytes plus the stored content type and user metadata."""

    def __init__(self, data: bytes, content_type: str, metadata: dict | None):
        self._data = data
        self.headers = {"Content-Type": content_type}
        for key, value in (metadata or {}).items():
            self.headers[f"x-amz-meta-{key}"] = value

    def read(self) -> bytes:
        return self._data

    def close(self) -> None:
        pass

    def release_conn(self) -> None:
        pass


class TestDeletedAssetNotPublic:
    """Review of eb1a31fa: after DELETE=204 the public asset URL still served the bytes."""

    SLUG = "deleted-asset-share"
    URL = f"/v1/web/shares/{SLUG}/assets"

    @staticmethod
    def store(objects: dict) -> MagicMock:
        # objects: key -> (bytes, content_type, metadata); the object store is the
        # only external boundary, routes and the database are real.
        store = MagicMock()
        store.bucket_exists.return_value = True

        def put(bucket, key, stream, length=None, content_type=None, metadata=None, **kw):
            objects[key] = (stream.read(), content_type, metadata)

        def get(bucket, key):
            if key not in objects:
                raise S3Error("NoSuchKey", "missing", key, "req", "host", None)
            return _StoredObject(*objects[key])

        store.put_object.side_effect = put
        store.get_object.side_effect = get
        store.remove_object.side_effect = lambda bucket, key: objects.pop(key, None)
        return store

    def publish(self, db_session: Session, test_user: models.User) -> models.Share:
        share = make_folder_share(db_session, test_user, [])
        share.web_published = True
        share.web_slug = self.SLUG
        share.visibility = models.ShareVisibility.PUBLIC
        db_session.commit()
        return share

    def test_deleted_sync_asset_is_no_longer_public(
        self,
        client: TestClient,
        test_user: models.User,
        db_session: Session,
        monkeypatch,
        web_enabled,
    ):
        share = self.publish(db_session, test_user)
        share_id = share.id
        raw_key = make_agent_key(db_session, share, "read,write")
        objects: dict = {}
        store = self.store(objects)
        monkeypatch.setattr(web, "_get_minio_client", lambda: store)
        monkeypatch.setattr(shares, "_get_minio_client", lambda: store)
        path, body = "img/deleted.png", b"bytes the owner removed"

        upload = client.post(
            f"/v1/web/shares/{share_id}/sync-upload",
            params={"path": path},
            content=body,
            headers={"X-Agent-Key": raw_key, "Content-Type": "image/png"},
        )
        assert upload.status_code == 200, upload.text
        before = client.get(self.URL, params={"path": path})
        assert before.status_code == 200 and before.content == body

        auth = bearer(login(client))
        removed = delete(client, share_id, path, auth, hashlib.sha256(body).hexdigest())
        assert removed.status_code == 204, removed.text

        after = client.get(self.URL, params={"path": path})
        assert after.status_code == 404
        # The bytes stay in storage (no physical delete): only serving stops.
        assert objects[f"web-assets/{share_id}/{path}"][0] == body
        store.remove_object.assert_not_called()

    def test_deleted_asset_written_before_tagging_is_no_longer_public(
        self,
        client: TestClient,
        test_user: models.User,
        db_session: Session,
        monkeypatch,
        web_enabled,
    ):
        # Review of e2b476b3: a sync asset stored before the tag existed carries
        # no metadata; its deletion must still close the public URL.
        body = b"bytes synced before the tag existed"
        digest = hashlib.sha256(body).hexdigest()
        share = self.publish(db_session, test_user)
        share_id = share.id
        path = "img/legacy.png"
        share.web_folder_items = [asset_item(share_id, path, digest)]
        db_session.commit()
        key = f"web-assets/{share_id}/{path}"
        objects = {key: (body, "image/png", None)}
        store = self.store(objects)
        monkeypatch.setattr(web, "_get_minio_client", lambda: store)
        monkeypatch.setattr(shares, "_get_minio_client", lambda: store)

        before = client.get(self.URL, params={"path": path})
        assert before.status_code == 200 and before.content == body
        auth = bearer(login(client))
        removed = delete(client, share_id, path, auth, digest)
        assert removed.status_code == 204, removed.text

        after = client.get(self.URL, params={"path": path})
        assert after.status_code == 404
        assert objects[key][0] == body
        store.remove_object.assert_not_called()

    def test_web_publish_of_a_deleted_path_is_served_again(
        self,
        client: TestClient,
        test_user: models.User,
        db_session: Session,
        monkeypatch,
        web_enabled,
    ):
        body = b"synced image"
        digest = hashlib.sha256(body).hexdigest()
        share = self.publish(db_session, test_user)
        share_id = share.id
        path = "img/again.png"
        share.web_folder_items = [asset_item(share_id, path, digest)]
        db_session.commit()
        objects = {f"web-assets/{share_id}/{path}": (body, "image/png", None)}
        store = self.store(objects)
        monkeypatch.setattr(web, "_get_minio_client", lambda: store)
        monkeypatch.setattr(shares, "_get_minio_client", lambda: store)
        auth = bearer(login(client))
        assert delete(client, share_id, path, auth, digest).status_code == 204
        assert client.get(self.URL, params={"path": path}).status_code == 404

        republished = client.post(
            self.URL,
            json={
                "path": path,
                "data": base64.b64encode(b"published again").decode(),
                "content_type": "image/png",
            },
            headers=auth,
        )
        assert republished.status_code == 201, republished.text

        resp = client.get(self.URL, params={"path": path})
        assert resp.status_code == 200 and resp.content == b"published again"

    def test_untagged_web_publish_asset_is_still_served(
        self,
        client: TestClient,
        test_user: models.User,
        db_session: Session,
        monkeypatch,
        web_enabled,
    ):
        # Positive control: web-publish and attachment uploads never had an index
        # entry; the gate must not hide them.
        share = self.publish(db_session, test_user)
        objects = {f"web-assets/{share.id}/img/published.png": (b"png", "image/png", None)}
        monkeypatch.setattr(web, "_get_minio_client", lambda: self.store(objects))

        resp = client.get(self.URL, params={"path": "img/published.png"})

        assert resp.status_code == 200 and resp.content == b"png"

    def test_sync_write_tags_assets_only(
        self,
        client: TestClient,
        test_user: models.User,
        db_session: Session,
        monkeypatch,
    ):
        share = make_folder_share(db_session, test_user, [])
        objects: dict = {}
        monkeypatch.setattr(shares, "_get_minio_client", lambda: self.store(objects))
        auth = bearer(login(client))

        for path, body, mime in (
            ("img/a.png", b"png", "image/png"),
            ("notes/a.md", b"# a", "text/markdown"),
        ):
            resp = client.put(
                f"{SHARES}/{share.id}/sync-write",
                params={"path": path},
                content=body,
                headers={"Content-Type": mime, "If-None-Match": "*", **auth},
            )
            assert resp.status_code in (200, 201), resp.text

        asset = objects[f"web-assets/{share.id}/img/a.png"]
        text = objects[f"sync-uploads/{share.id}/{hashlib.sha256(b'# a').hexdigest()}"]
        assert asset[2] == SYNC_ASSET_METADATA
        assert text[2] is None


class TestTreePatchGuaranteeKept:
    """AC3: share_service keeps sync-artifacts absent from a PATCHed tree."""

    def test_unpulled_artifact_survives_tree_patch(
        self, client: TestClient, test_user: models.User, db_session: Session, minio
    ):
        # Written by Mesh, not pulled into the vault yet: the vault's tree lacks it.
        unpulled = text_item("from-mesh.md", "# written by an agent", origin="external")
        share = make_folder_share(db_session, test_user, [unpulled])
        token = login(client)

        resp = client.patch(
            f"{SHARES}/{share.id}",
            json={"web_folder_items": [{"path": "vault.md", "name": "vault.md", "type": "doc"}]},
            headers=bearer(token),
        )

        assert resp.status_code == 200, resp.text
        assert "from-mesh.md" in index_paths(client, share.id, bearer(token))

    def test_deleted_entry_is_not_resurrected_by_tree_patch(
        self, client: TestClient, test_user: models.User, db_session: Session, minio
    ):
        item = text_item("a.md", "# A")
        share = make_folder_share(db_session, test_user, [item, text_item("b.md", "# B")])
        token = login(client)

        assert delete(client, share.id, "a.md", bearer(token), item["sha256"]).status_code == 204
        resp = client.patch(
            f"{SHARES}/{share.id}",
            json={"web_folder_items": [{"path": "b.md", "name": "b.md", "type": "doc"}]},
            headers=bearer(token),
        )

        assert resp.status_code == 200, resp.text
        assert index_paths(client, share.id, bearer(token)) == {"b.md"}


def test_server_info_advertises_sync_delete(client: TestClient):
    resp = client.get("/v1/server/info")

    assert resp.status_code == 200, resp.text
    assert resp.json()["features"]["sync_delete"] is True
