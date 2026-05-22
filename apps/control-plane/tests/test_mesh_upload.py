"""Tests for Mesh artifact upload endpoint and agent key CRUD."""

from __future__ import annotations

import hashlib
import secrets
import uuid
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
    def test_create_key_returns_raw_once(self, client: TestClient, test_user: models.User, db_session: Session, web_enabled):
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

    def test_list_keys_never_leaks_hash(self, client: TestClient, test_user: models.User, db_session: Session, web_enabled):
        share = make_folder_share(db_session, test_user, slug="list-share")
        _, _ = make_agent_key(db_session, share)
        token = login(client, "bootstrap@example.com", "super-secret")
        resp = client.get(f"/v1/web/shares/{share.id}/agent-keys", headers=auth_headers(token))
        assert resp.status_code == 200, resp.text
        for item in resp.json():
            assert "key_hash" not in item
            assert "key" not in item

    def test_revoke_key(self, client: TestClient, test_user: models.User, db_session: Session, web_enabled):
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

    def test_revoke_idempotent(self, client: TestClient, test_user: models.User, db_session: Session, web_enabled):
        share = make_folder_share(db_session, test_user, slug="revoke2-share")
        _, ak = make_agent_key(db_session, share)
        token = login(client, "bootstrap@example.com", "super-secret")
        client.delete(f"/v1/web/shares/{share.id}/agent-keys/{ak.id}", headers=auth_headers(token))
        resp = client.delete(f"/v1/web/shares/{share.id}/agent-keys/{ak.id}", headers=auth_headers(token))
        assert resp.status_code == 200


# ── Upload endpoint ───────────────────────────────────────────────────────────

class TestMeshUpload:
    def test_valid_upload_text(self, client: TestClient, test_user: models.User, db_session: Session, web_enabled):
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

    def test_valid_upload_item_in_folder_items(self, client: TestClient, test_user: models.User, db_session: Session, web_enabled):
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

    def test_invalid_key_returns_401(self, client: TestClient, test_user: models.User, db_session: Session, web_enabled):
        share = make_folder_share(db_session, test_user, slug="upload-401")
        resp = client.post(
            "/v1/web/shares/upload-401/upload?path=file.md",
            content=b"data",
            headers={"X-Agent-Key": "tr_agent_invalidkeyvalue", "Content-Type": "text/plain"},
        )
        assert resp.status_code == 401

    def test_key_for_wrong_share_returns_403(self, client: TestClient, test_user: models.User, db_session: Session, web_enabled):
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

    def test_revoked_key_returns_403(self, client: TestClient, test_user: models.User, db_session: Session, web_enabled):
        from datetime import timezone
        from datetime import datetime as dt
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

    def test_expired_key_returns_403(self, client: TestClient, test_user: models.User, db_session: Session, web_enabled):
        from datetime import timezone, timedelta
        from datetime import datetime as dt
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

    def test_path_traversal_returns_400(self, client: TestClient, test_user: models.User, db_session: Session, web_enabled):
        share = make_folder_share(db_session, test_user, slug="upload-traverse")
        raw_key, _ = make_agent_key(db_session, share)
        resp = client.post(
            "/v1/web/shares/upload-traverse/upload?path=../etc/passwd",
            content=b"data",
            headers={"X-Agent-Key": raw_key, "Content-Type": "text/plain"},
        )
        assert resp.status_code == 400

    def test_leading_slash_path_returns_400(self, client: TestClient, test_user: models.User, db_session: Session, web_enabled):
        share = make_folder_share(db_session, test_user, slug="upload-slash")
        raw_key, _ = make_agent_key(db_session, share)
        resp = client.post(
            "/v1/web/shares/upload-slash/upload?path=/etc/passwd",
            content=b"data",
            headers={"X-Agent-Key": raw_key, "Content-Type": "text/plain"},
        )
        assert resp.status_code == 400

    def test_idempotent_overwrite(self, client: TestClient, test_user: models.User, db_session: Session, web_enabled):
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

    def test_large_file_rejected(self, client: TestClient, test_user: models.User, db_session: Session, web_enabled):
        share = make_folder_share(db_session, test_user, slug="upload-large")
        raw_key, _ = make_agent_key(db_session, share)
        big_body = b"x" * (26 * 1024 * 1024)  # 26MB > 25MB cap
        resp = client.post(
            "/v1/web/shares/upload-large/upload?path=big.bin",
            content=big_body,
            headers={"X-Agent-Key": raw_key, "Content-Type": "application/octet-stream"},
        )
        assert resp.status_code == 413
