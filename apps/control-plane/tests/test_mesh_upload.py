"""Tests for Mesh artifact upload endpoint and agent key CRUD."""

from __future__ import annotations

import hashlib
import secrets
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.db import models


def login(client: TestClient, email: str, password: str) -> str:
    response = client.post("/auth/login", json={"email": email, "password": password})
    assert response.status_code == 200, response.text
    return response.json()["access_token"]


def auth_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def make_folder_share(db: Session, user: models.User, slug: str = "test-share") -> models.Share:
    share = models.Share(
        kind=models.ShareKind.FOLDER,
        path="TestFolder/",
        visibility=models.ShareVisibility.PUBLIC,
        owner_user_id=user.id,
        web_published=True,
        web_slug=slug,
        web_folder_items=[],
    )
    db.add(share)
    db.commit()
    db.refresh(share)
    return share


def make_agent_key(db: Session, share: models.Share) -> tuple[str, models.ShareAgentKey]:
    raw_key = "tr_agent_" + secrets.token_hex(24)
    key_hash = hashlib.sha256(raw_key.encode()).hexdigest()
    ak = models.ShareAgentKey(
        share_id=share.id,
        key_hash=key_hash,
        label="test key",
        scopes="write",
    )
    db.add(ak)
    db.commit()
    db.refresh(ak)
    return raw_key, ak


@pytest.fixture
def web_enabled(monkeypatch):
    monkeypatch.setenv("WEB_PUBLISH_DOMAIN", "docs.test.com")
    from app.core.config import get_settings

    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def minio_patch():
    """Context manager that mocks MinIO so tests don't need a real server."""
    mock_client = MagicMock()
    mock_client.bucket_exists.return_value = True
    mock_client.put_object.return_value = None
    return patch("app.api.routers.web._get_minio_client", return_value=mock_client)


# ── Agent key CRUD ────────────────────────────────────────────────────────────


class TestAgentKeyCRUD:
    def test_create_key_returns_raw_once(
        self, client: TestClient, test_user: models.User, db_session: Session, web_enabled
    ):
        share = make_folder_share(db_session, test_user)
        token = login(client, "bootstrap@example.com", "super-secret")
        resp = client.post(
            f"/v1/web/shares/{share.id}/agent-keys",
            json={"label": "ci-agent"},
            headers=auth_headers(token),
        )
        assert resp.status_code == 201, resp.text
        data = resp.json()
        assert data["key"].startswith("tr_agent_")
        assert data["label"] == "ci-agent"
        assert "key_hash" not in data

    def test_list_keys_never_leaks_hash(
        self, client: TestClient, test_user: models.User, db_session: Session, web_enabled
    ):
        share = make_folder_share(db_session, test_user, slug="list-share")
        _, _ = make_agent_key(db_session, share)
        token = login(client, "bootstrap@example.com", "super-secret")
        resp = client.get(f"/v1/web/shares/{share.id}/agent-keys", headers=auth_headers(token))
        assert resp.status_code == 200, resp.text
        for item in resp.json():
            assert "key_hash" not in item
            assert "key" not in item

    def test_revoke_key(
        self, client: TestClient, test_user: models.User, db_session: Session, web_enabled
    ):
        share = make_folder_share(db_session, test_user, slug="revoke-share")
        _, ak = make_agent_key(db_session, share)
        token = login(client, "bootstrap@example.com", "super-secret")
        resp = client.delete(
            f"/v1/web/shares/{share.id}/agent-keys/{ak.id}",
            headers=auth_headers(token),
        )
        assert resp.status_code == 200, resp.text
        db_session.refresh(ak)
        assert ak.revoked_at is not None

    def test_revoke_idempotent(
        self, client: TestClient, test_user: models.User, db_session: Session, web_enabled
    ):
        share = make_folder_share(db_session, test_user, slug="revoke2-share")
        _, ak = make_agent_key(db_session, share)
        token = login(client, "bootstrap@example.com", "super-secret")
        client.delete(f"/v1/web/shares/{share.id}/agent-keys/{ak.id}", headers=auth_headers(token))
        resp = client.delete(
            f"/v1/web/shares/{share.id}/agent-keys/{ak.id}", headers=auth_headers(token)
        )
        assert resp.status_code == 200


# ── Upload endpoint ───────────────────────────────────────────────────────────


class TestMeshUpload:
    def test_valid_upload_text(
        self, client: TestClient, test_user: models.User, db_session: Session, web_enabled
    ):
        share = make_folder_share(db_session, test_user, slug="upload-ok")
        raw_key, _ = make_agent_key(db_session, share)
        with minio_patch():
            resp = client.post(
                "/v1/web/shares/upload-ok/upload?path=Notes/hello.md",
                content=b"# Hello world",
                headers={
                    "X-Agent-Key": raw_key,
                    "Content-Type": "text/markdown",
                },
            )
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["ok"] is True
        assert data["path"] == "Notes/hello.md"
        assert data["size_bytes"] == len(b"# Hello world")

    def test_valid_upload_item_in_folder_items(
        self, client: TestClient, test_user: models.User, db_session: Session, web_enabled
    ):
        share = make_folder_share(db_session, test_user, slug="upload-index")
        raw_key, _ = make_agent_key(db_session, share)
        with minio_patch():
            client.post(
                "/v1/web/shares/upload-index/upload?path=file.md",
                content=b"content",
                headers={"X-Agent-Key": raw_key, "Content-Type": "text/markdown"},
            )
        db_session.refresh(share)
        items = share.web_folder_items or []
        paths = [i["path"] for i in items]
        assert "file.md" in paths
        item = next(i for i in items if i["path"] == "file.md")
        assert item["source"] == "mesh-artifact"

    def test_invalid_key_returns_401(
        self, client: TestClient, test_user: models.User, db_session: Session, web_enabled
    ):
        share = make_folder_share(db_session, test_user, slug="upload-401")
        resp = client.post(
            "/v1/web/shares/upload-401/upload?path=file.md",
            content=b"data",
            headers={"X-Agent-Key": "tr_agent_invalidkeyvalue", "Content-Type": "text/plain"},
        )
        assert resp.status_code == 401

    def test_key_for_wrong_share_returns_403(
        self, client: TestClient, test_user: models.User, db_session: Session, web_enabled
    ):
        share1 = make_folder_share(db_session, test_user, slug="share-one")
        share2 = make_folder_share(db_session, test_user, slug="share-two")
        raw_key, _ = make_agent_key(db_session, share1)  # key belongs to share1
        with minio_patch():
            resp = client.post(
                "/v1/web/shares/share-two/upload?path=file.md",
                content=b"data",
                headers={"X-Agent-Key": raw_key, "Content-Type": "text/plain"},
            )
        assert resp.status_code == 403

    def test_revoked_key_returns_403(
        self, client: TestClient, test_user: models.User, db_session: Session, web_enabled
    ):
        from datetime import datetime as dt
        from datetime import timezone

        share = make_folder_share(db_session, test_user, slug="upload-revoked")
        raw_key, ak = make_agent_key(db_session, share)
        ak.revoked_at = dt.now(timezone.utc)
        db_session.commit()
        with minio_patch():
            resp = client.post(
                "/v1/web/shares/upload-revoked/upload?path=file.md",
                content=b"data",
                headers={"X-Agent-Key": raw_key, "Content-Type": "text/plain"},
            )
        assert resp.status_code == 403

    def test_expired_key_returns_403(
        self, client: TestClient, test_user: models.User, db_session: Session, web_enabled
    ):
        from datetime import datetime as dt
        from datetime import timedelta, timezone

        share = make_folder_share(db_session, test_user, slug="upload-expired")
        raw_key, ak = make_agent_key(db_session, share)
        ak.expires_at = dt.now(timezone.utc) - timedelta(hours=1)
        db_session.commit()
        with minio_patch():
            resp = client.post(
                "/v1/web/shares/upload-expired/upload?path=file.md",
                content=b"data",
                headers={"X-Agent-Key": raw_key, "Content-Type": "text/plain"},
            )
        assert resp.status_code == 403

    def test_path_traversal_returns_400(
        self, client: TestClient, test_user: models.User, db_session: Session, web_enabled
    ):
        share = make_folder_share(db_session, test_user, slug="upload-traverse")
        raw_key, _ = make_agent_key(db_session, share)
        resp = client.post(
            "/v1/web/shares/upload-traverse/upload?path=../etc/passwd",
            content=b"data",
            headers={"X-Agent-Key": raw_key, "Content-Type": "text/plain"},
        )
        assert resp.status_code == 400

    def test_leading_slash_path_returns_400(
        self, client: TestClient, test_user: models.User, db_session: Session, web_enabled
    ):
        share = make_folder_share(db_session, test_user, slug="upload-slash")
        raw_key, _ = make_agent_key(db_session, share)
        resp = client.post(
            "/v1/web/shares/upload-slash/upload?path=/etc/passwd",
            content=b"data",
            headers={"X-Agent-Key": raw_key, "Content-Type": "text/plain"},
        )
        assert resp.status_code == 400

    def test_idempotent_overwrite(
        self, client: TestClient, test_user: models.User, db_session: Session, web_enabled
    ):
        share = make_folder_share(db_session, test_user, slug="upload-idem")
        raw_key, _ = make_agent_key(db_session, share)
        with minio_patch():
            r1 = client.post(
                "/v1/web/shares/upload-idem/upload?path=note.md",
                content=b"v1",
                headers={"X-Agent-Key": raw_key, "Content-Type": "text/markdown"},
            )
            r2 = client.post(
                "/v1/web/shares/upload-idem/upload?path=note.md",
                content=b"v2 updated",
                headers={"X-Agent-Key": raw_key, "Content-Type": "text/markdown"},
            )
        assert r1.status_code == 200
        assert r2.status_code == 200
        db_session.refresh(share)
        items = share.web_folder_items or []
        matching = [i for i in items if i["path"] == "note.md"]
        assert len(matching) == 1
        assert matching[0]["size"] == len(b"v2 updated")

    def test_large_file_rejected(
        self, client: TestClient, test_user: models.User, db_session: Session, web_enabled
    ):
        share = make_folder_share(db_session, test_user, slug="upload-large")
        raw_key, _ = make_agent_key(db_session, share)
        big_body = b"x" * (26 * 1024 * 1024)  # 26MB > 25MB cap
        resp = client.post(
            "/v1/web/shares/upload-large/upload?path=big.bin",
            content=big_body,
            headers={"X-Agent-Key": raw_key, "Content-Type": "application/octet-stream"},
        )
        assert resp.status_code == 413


# ── Agent key security and new-field tests ────────────────────────────────────


class TestAgentKeyAuthRestrictions:
    """Viewer-role members must not be able to create/list/delete agent keys."""

    def _make_viewer(self, db: Session, share: models.Share, user: models.User) -> models.User:
        from app.core import security as sec

        viewer = models.User(
            email="viewer@example.com",
            password_hash=sec.get_password_hash("viewer123"),
            is_admin=False,
            is_active=True,
        )
        db.add(viewer)
        db.commit()
        db.refresh(viewer)
        member = models.ShareMember(
            share_id=share.id,
            user_id=viewer.id,
            role=models.ShareMemberRole.VIEWER,
        )
        db.add(member)
        db.commit()
        return viewer

    def test_viewer_cannot_create_key(
        self, client: TestClient, test_user: models.User, db_session: Session, web_enabled
    ):
        share = make_folder_share(db_session, test_user, slug="viewer-create")
        viewer = self._make_viewer(db_session, share, test_user)
        token = login(client, "viewer@example.com", "viewer123")
        resp = client.post(
            f"/v1/web/shares/{share.id}/agent-keys",
            json={"label": "bad"},
            headers=auth_headers(token),
        )
        assert resp.status_code == 403

    def test_viewer_cannot_list_keys(
        self, client: TestClient, test_user: models.User, db_session: Session, web_enabled
    ):
        share = make_folder_share(db_session, test_user, slug="viewer-list")
        viewer = self._make_viewer(db_session, share, test_user)
        token = login(client, "viewer@example.com", "viewer123")
        resp = client.get(f"/v1/web/shares/{share.id}/agent-keys", headers=auth_headers(token))
        assert resp.status_code == 403

    def test_viewer_cannot_revoke_key(
        self, client: TestClient, test_user: models.User, db_session: Session, web_enabled
    ):
        share = make_folder_share(db_session, test_user, slug="viewer-revoke")
        _, ak = make_agent_key(db_session, share)
        viewer = self._make_viewer(db_session, share, test_user)
        token = login(client, "viewer@example.com", "viewer123")
        resp = client.delete(
            f"/v1/web/shares/{share.id}/agent-keys/{ak.id}", headers=auth_headers(token)
        )
        assert resp.status_code == 403


class TestAgentKeyNewFields:
    """created_by populated; is_active and share_id present in responses."""

    def test_create_populates_created_by(
        self, client: TestClient, test_user: models.User, db_session: Session, web_enabled
    ):
        share = make_folder_share(db_session, test_user, slug="cb-share")
        token = login(client, "bootstrap@example.com", "super-secret")
        resp = client.post(
            f"/v1/web/shares/{share.id}/agent-keys",
            json={"label": "my-key"},
            headers=auth_headers(token),
        )
        assert resp.status_code == 201, resp.text
        # Verify DB row has created_by set
        from sqlalchemy import select as sa_select

        from app.db.models import ShareAgentKey

        key_id = resp.json()["id"]
        ak = db_session.execute(
            sa_select(ShareAgentKey).where(ShareAgentKey.id == __import__("uuid").UUID(key_id))
        ).scalar_one()
        assert ak.created_by is not None

    def test_create_response_has_share_id_and_scopes(
        self, client: TestClient, test_user: models.User, db_session: Session, web_enabled
    ):
        share = make_folder_share(db_session, test_user, slug="fields-share")
        token = login(client, "bootstrap@example.com", "super-secret")
        resp = client.post(
            f"/v1/web/shares/{share.id}/agent-keys",
            json={"label": "x"},
            headers=auth_headers(token),
        )
        data = resp.json()
        assert "share_id" in data
        assert data["share_id"] == str(share.id)
        assert "scopes" in data
        assert "write" in data["scopes"]

    def test_list_response_has_is_active_and_share_id(
        self, client: TestClient, test_user: models.User, db_session: Session, web_enabled
    ):
        share = make_folder_share(db_session, test_user, slug="list-fields")
        make_agent_key(db_session, share)
        token = login(client, "bootstrap@example.com", "super-secret")
        resp = client.get(f"/v1/web/shares/{share.id}/agent-keys", headers=auth_headers(token))
        assert resp.status_code == 200, resp.text
        item = resp.json()[0]
        assert "is_active" in item
        assert item["is_active"] is True
        assert "share_id" in item
        assert item["share_id"] == str(share.id)

    def test_revoked_key_is_active_false(
        self, client: TestClient, test_user: models.User, db_session: Session, web_enabled
    ):
        share = make_folder_share(db_session, test_user, slug="active-false")
        _, ak = make_agent_key(db_session, share)
        token = login(client, "bootstrap@example.com", "super-secret")
        # Revoke it
        client.delete(f"/v1/web/shares/{share.id}/agent-keys/{ak.id}", headers=auth_headers(token))
        resp = client.get(f"/v1/web/shares/{share.id}/agent-keys", headers=auth_headers(token))
        item = next(i for i in resp.json() if i["id"] == str(ak.id))
        assert item["is_active"] is False

    def test_revoke_returns_revoked_at(
        self, client: TestClient, test_user: models.User, db_session: Session, web_enabled
    ):
        share = make_folder_share(db_session, test_user, slug="revoke-ts")
        _, ak = make_agent_key(db_session, share)
        token = login(client, "bootstrap@example.com", "super-secret")
        resp = client.delete(
            f"/v1/web/shares/{share.id}/agent-keys/{ak.id}", headers=auth_headers(token)
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "revoked_at" in data
        assert data["revoked_at"] is not None


class TestAgentKeyCountCap:
    """Creating more than agent_key_max_per_share active keys returns 409."""

    def test_cap_enforced(
        self,
        client: TestClient,
        test_user: models.User,
        db_session: Session,
        web_enabled,
        monkeypatch,
    ):
        # Set cap to 2 via env
        monkeypatch.setenv("AGENT_KEY_MAX_PER_SHARE", "2")
        from app.core.config import get_settings

        get_settings.cache_clear()

        share = make_folder_share(db_session, test_user, slug="cap-share")
        token = login(client, "bootstrap@example.com", "super-secret")
        headers = auth_headers(token)
        url = f"/v1/web/shares/{share.id}/agent-keys"

        r1 = client.post(url, json={"label": "k1"}, headers=headers)
        r2 = client.post(url, json={"label": "k2"}, headers=headers)
        r3 = client.post(url, json={"label": "k3"}, headers=headers)

        assert r1.status_code == 201
        assert r2.status_code == 201
        assert r3.status_code == 409

        get_settings.cache_clear()


class TestAgentKeyLastUsedAt:
    """Upload via agent key updates last_used_at on the key row."""

    def test_upload_sets_last_used_at(
        self, client: TestClient, test_user: models.User, db_session: Session, web_enabled
    ):
        share = make_folder_share(db_session, test_user, slug="last-used")
        raw_key, ak = make_agent_key(db_session, share)
        assert ak.last_used_at is None
        with minio_patch():
            resp = client.post(
                "/v1/web/shares/last-used/upload?path=note.md",
                content=b"hello",
                headers={"X-Agent-Key": raw_key, "Content-Type": "text/markdown"},
            )
        assert resp.status_code == 200, resp.text
        db_session.refresh(ak)
        assert ak.last_used_at is not None
