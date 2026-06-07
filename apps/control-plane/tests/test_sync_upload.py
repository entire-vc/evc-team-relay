"""Tests for POST /v1/web/shares/{share_id}/sync-upload endpoint."""

from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.db import models


def login(client: TestClient, email: str, password: str) -> str:
    response = client.post("/auth/login", json={"email": email, "password": password})
    assert response.status_code == 200, response.text
    return response.json()["access_token"]


def make_folder_share(
    db: Session,
    user: models.User,
    slug: str = "sync-share",
    published: bool = True,
) -> models.Share:
    share = models.Share(
        kind=models.ShareKind.FOLDER,
        path="SyncFolder/",
        visibility=models.ShareVisibility.PRIVATE,
        owner_user_id=user.id,
        web_published=published,
        web_slug=slug if published else None,
        web_folder_items=[],
    )
    db.add(share)
    db.commit()
    db.refresh(share)
    return share


def make_agent_key(
    db: Session,
    share: models.Share,
    scopes: str = "write",
    expires_at: datetime | None = None,
) -> tuple[str, models.ShareAgentKey]:
    raw_key = "tr_agent_" + secrets.token_hex(24)
    key_hash = hashlib.sha256(raw_key.encode()).hexdigest()
    ak = models.ShareAgentKey(
        share_id=share.id,
        key_hash=key_hash,
        label="sync-test key",
        scopes=scopes,
        expires_at=expires_at,
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
    mock_client = MagicMock()
    mock_client.bucket_exists.return_value = True
    mock_client.put_object.return_value = None
    return patch("app.api.routers.web._get_minio_client", return_value=mock_client)


BASE = "/v1/web/shares"


class TestSyncUploadBasic:
    def test_valid_upload_returns_sync_and_web_url(
        self, client: TestClient, test_user: models.User, db_session: Session, web_enabled
    ):
        share = make_folder_share(db_session, test_user, slug="su-basic")
        raw_key, _ = make_agent_key(db_session, share)
        with minio_patch():
            resp = client.post(
                f"{BASE}/{share.id}/sync-upload?path=notes/hello.md",
                content=b"# Hello",
                headers={"X-Agent-Key": raw_key, "Content-Type": "text/markdown"},
            )
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert "sync_url" in data
        assert str(share.id) in data["sync_url"]
        assert data["web_url"] is not None
        assert "su-basic" in data["web_url"]
        assert data["path"] == "notes/hello.md"

    def test_web_url_none_when_not_published(
        self, client: TestClient, test_user: models.User, db_session: Session, web_enabled
    ):
        share = make_folder_share(db_session, test_user, published=False)
        raw_key, _ = make_agent_key(db_session, share)
        with minio_patch():
            resp = client.post(
                f"{BASE}/{share.id}/sync-upload?path=note.md",
                content=b"content",
                headers={"X-Agent-Key": raw_key, "Content-Type": "text/markdown"},
            )
        assert resp.status_code == 200, resp.text
        assert resp.json()["web_url"] is None

    def test_item_in_folder_items_with_sync_artifact_source(
        self, client: TestClient, test_user: models.User, db_session: Session, web_enabled
    ):
        share = make_folder_share(db_session, test_user, slug="su-index")
        raw_key, _ = make_agent_key(db_session, share)
        with minio_patch():
            client.post(
                f"{BASE}/{share.id}/sync-upload?path=doc.md",
                content=b"# Doc",
                headers={"X-Agent-Key": raw_key, "Content-Type": "text/markdown"},
            )
        db_session.refresh(share)
        items = share.web_folder_items or []
        assert any(i["path"] == "doc.md" for i in items)
        item = next(i for i in items if i["path"] == "doc.md")
        assert item["source"] == "sync-artifact"
        assert "sha256" in item
        assert item["storage_key"].startswith("sync-uploads/")

    def test_sha256_matches_content(
        self, client: TestClient, test_user: models.User, db_session: Session, web_enabled
    ):
        share = make_folder_share(db_session, test_user, slug="su-hash")
        raw_key, _ = make_agent_key(db_session, share)
        body = b"checksum content"
        expected_hash = hashlib.sha256(body).hexdigest()
        with minio_patch():
            client.post(
                f"{BASE}/{share.id}/sync-upload?path=file.md",
                content=body,
                headers={"X-Agent-Key": raw_key, "Content-Type": "text/markdown"},
            )
        db_session.refresh(share)
        item = next(i for i in share.web_folder_items if i["path"] == "file.md")
        assert item["sha256"] == expected_hash

    def test_idempotent_overwrite(
        self, client: TestClient, test_user: models.User, db_session: Session, web_enabled
    ):
        share = make_folder_share(db_session, test_user, slug="su-idem")
        raw_key, _ = make_agent_key(db_session, share)
        with minio_patch():
            client.post(
                f"{BASE}/{share.id}/sync-upload?path=note.md",
                content=b"v1",
                headers={"X-Agent-Key": raw_key, "Content-Type": "text/markdown"},
            )
            client.post(
                f"{BASE}/{share.id}/sync-upload?path=note.md",
                content=b"v2 updated",
                headers={"X-Agent-Key": raw_key, "Content-Type": "text/markdown"},
            )
        db_session.refresh(share)
        matching = [i for i in share.web_folder_items if i["path"] == "note.md"]
        assert len(matching) == 1
        assert matching[0]["size"] == len(b"v2 updated")

    def test_bumps_web_content_updated_at(
        self, client: TestClient, test_user: models.User, db_session: Session, web_enabled
    ):
        share = make_folder_share(db_session, test_user, slug="su-bump")
        raw_key, _ = make_agent_key(db_session, share)
        before = share.web_content_updated_at
        with minio_patch():
            client.post(
                f"{BASE}/{share.id}/sync-upload?path=bump.md",
                content=b"data",
                headers={"X-Agent-Key": raw_key, "Content-Type": "text/markdown"},
            )
        db_session.refresh(share)
        assert share.web_content_updated_at != before

    def test_updates_last_used_at(
        self, client: TestClient, test_user: models.User, db_session: Session, web_enabled
    ):
        share = make_folder_share(db_session, test_user, slug="su-lastused")
        raw_key, ak = make_agent_key(db_session, share)
        assert ak.last_used_at is None
        with minio_patch():
            client.post(
                f"{BASE}/{share.id}/sync-upload?path=note.md",
                content=b"data",
                headers={"X-Agent-Key": raw_key, "Content-Type": "text/markdown"},
            )
        db_session.refresh(ak)
        assert ak.last_used_at is not None

    def test_minio_cas_key_format(
        self, client: TestClient, test_user: models.User, db_session: Session, web_enabled
    ):
        share = make_folder_share(db_session, test_user, slug="su-cas-key")
        raw_key, _ = make_agent_key(db_session, share)
        body = b"cas content"
        sha = hashlib.sha256(body).hexdigest()
        with minio_patch() as mock_get_client:
            client.post(
                f"{BASE}/{share.id}/sync-upload?path=file.md",
                content=body,
                headers={"X-Agent-Key": raw_key, "Content-Type": "text/markdown"},
            )
            mock_client = mock_get_client.return_value
        call_args = mock_client.put_object.call_args
        assert call_args is not None
        object_name = call_args[0][1] if call_args[0] else call_args.kwargs.get("object_name")
        assert object_name == f"sync-uploads/{share.id}/{sha}"


class TestSyncUploadAuth:
    def test_missing_key_returns_401(
        self, client: TestClient, test_user: models.User, db_session: Session, web_enabled
    ):
        share = make_folder_share(db_session, test_user, slug="su-no-key")
        resp = client.post(
            f"{BASE}/{share.id}/sync-upload?path=file.md",
            content=b"data",
            headers={"Content-Type": "text/plain"},
        )
        assert resp.status_code == 401

    def test_invalid_key_returns_401(
        self, client: TestClient, test_user: models.User, db_session: Session, web_enabled
    ):
        share = make_folder_share(db_session, test_user, slug="su-bad-key")
        resp = client.post(
            f"{BASE}/{share.id}/sync-upload?path=file.md",
            content=b"data",
            headers={"X-Agent-Key": "tr_agent_invalid", "Content-Type": "text/plain"},
        )
        assert resp.status_code == 401

    def test_key_for_wrong_share_returns_403(
        self, client: TestClient, test_user: models.User, db_session: Session, web_enabled
    ):
        share1 = make_folder_share(db_session, test_user, slug="su-s1")
        share2 = make_folder_share(db_session, test_user, slug="su-s2")
        raw_key, _ = make_agent_key(db_session, share1)
        with minio_patch():
            resp = client.post(
                f"{BASE}/{share2.id}/sync-upload?path=file.md",
                content=b"data",
                headers={"X-Agent-Key": raw_key, "Content-Type": "text/plain"},
            )
        assert resp.status_code == 403

    def test_revoked_key_returns_403(
        self, client: TestClient, test_user: models.User, db_session: Session, web_enabled
    ):
        share = make_folder_share(db_session, test_user, slug="su-revoked")
        raw_key, ak = make_agent_key(db_session, share)
        ak.revoked_at = datetime.now(timezone.utc)
        db_session.commit()
        with minio_patch():
            resp = client.post(
                f"{BASE}/{share.id}/sync-upload?path=file.md",
                content=b"data",
                headers={"X-Agent-Key": raw_key, "Content-Type": "text/plain"},
            )
        assert resp.status_code == 403

    def test_expired_key_returns_403(
        self, client: TestClient, test_user: models.User, db_session: Session, web_enabled
    ):
        share = make_folder_share(db_session, test_user, slug="su-expired")
        raw_key, ak = make_agent_key(
            db_session, share, expires_at=datetime.now(timezone.utc) - timedelta(hours=1)
        )
        with minio_patch():
            resp = client.post(
                f"{BASE}/{share.id}/sync-upload?path=file.md",
                content=b"data",
                headers={"X-Agent-Key": raw_key, "Content-Type": "text/plain"},
            )
        assert resp.status_code == 403

    def test_read_only_key_returns_403(
        self, client: TestClient, test_user: models.User, db_session: Session, web_enabled
    ):
        share = make_folder_share(db_session, test_user, slug="su-readonly")
        raw_key, _ = make_agent_key(db_session, share, scopes="read")
        with minio_patch():
            resp = client.post(
                f"{BASE}/{share.id}/sync-upload?path=file.md",
                content=b"data",
                headers={"X-Agent-Key": raw_key, "Content-Type": "text/plain"},
            )
        assert resp.status_code == 403


class TestSyncUploadValidation:
    def test_path_traversal_returns_400(
        self, client: TestClient, test_user: models.User, db_session: Session, web_enabled
    ):
        share = make_folder_share(db_session, test_user, slug="su-traverse")
        raw_key, _ = make_agent_key(db_session, share)
        resp = client.post(
            f"{BASE}/{share.id}/sync-upload?path=../etc/passwd",
            content=b"data",
            headers={"X-Agent-Key": raw_key, "Content-Type": "text/plain"},
        )
        assert resp.status_code == 400

    def test_leading_slash_returns_400(
        self, client: TestClient, test_user: models.User, db_session: Session, web_enabled
    ):
        share = make_folder_share(db_session, test_user, slug="su-slash")
        raw_key, _ = make_agent_key(db_session, share)
        resp = client.post(
            f"{BASE}/{share.id}/sync-upload?path=/etc/passwd",
            content=b"data",
            headers={"X-Agent-Key": raw_key, "Content-Type": "text/plain"},
        )
        assert resp.status_code == 400

    def test_large_file_returns_413(
        self, client: TestClient, test_user: models.User, db_session: Session, web_enabled
    ):
        share = make_folder_share(db_session, test_user, slug="su-large")
        raw_key, _ = make_agent_key(db_session, share)
        big_body = b"x" * (26 * 1024 * 1024)
        resp = client.post(
            f"{BASE}/{share.id}/sync-upload?path=big.bin",
            content=big_body,
            headers={"X-Agent-Key": raw_key, "Content-Type": "application/octet-stream"},
        )
        assert resp.status_code == 413

    def test_doc_share_returns_400(
        self, client: TestClient, test_user: models.User, db_session: Session, web_enabled
    ):
        doc_share = models.Share(
            kind=models.ShareKind.DOC,
            path="note.md",
            visibility=models.ShareVisibility.PRIVATE,
            owner_user_id=test_user.id,
            web_published=False,
            web_folder_items=None,
        )
        db_session.add(doc_share)
        db_session.commit()
        raw_key, _ = make_agent_key(db_session, doc_share)
        resp = client.post(
            f"{BASE}/{doc_share.id}/sync-upload?path=note.md",
            content=b"data",
            headers={"X-Agent-Key": raw_key, "Content-Type": "text/plain"},
        )
        assert resp.status_code == 400

    def test_unknown_share_id_returns_404(
        self, client: TestClient, test_user: models.User, db_session: Session, web_enabled
    ):
        import uuid

        resp = client.post(
            f"{BASE}/{uuid.uuid4()}/sync-upload?path=file.md",
            content=b"data",
            headers={"X-Agent-Key": "tr_agent_fake", "Content-Type": "text/plain"},
        )
        assert resp.status_code in (401, 404)

    def test_slug_accepted_as_share_id(
        self, client: TestClient, test_user: models.User, db_session: Session, web_enabled
    ):
        share = make_folder_share(db_session, test_user, slug="su-slug-only")
        raw_key, _ = make_agent_key(db_session, share)
        with minio_patch():
            resp = client.post(
                f"{BASE}/su-slug-only/sync-upload?path=file.md",
                content=b"data",
                headers={"X-Agent-Key": raw_key, "Content-Type": "text/plain"},
            )
        assert resp.status_code == 200

    def test_disabled_web_publish_returns_404(
        self, client: TestClient, test_user: models.User, db_session: Session
    ):
        share = make_folder_share(db_session, test_user, slug="su-disabled")
        raw_key, _ = make_agent_key(db_session, share)
        resp = client.post(
            f"{BASE}/{share.id}/sync-upload?path=file.md",
            content=b"data",
            headers={"X-Agent-Key": raw_key, "Content-Type": "text/plain"},
        )
        assert resp.status_code == 404
