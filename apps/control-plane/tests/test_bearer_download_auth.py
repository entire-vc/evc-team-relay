"""Tests for Bearer JWT auth on /files-index and /download endpoints (s1).

These endpoints previously required X-Agent-Key. After s1 they accept either
X-Agent-Key or Authorization: Bearer JWT (owner or share member).

Bearer /files-index returns only sync-artifact items (plugin inbound sync).
Bearer /download returns any file the share contains.
"""

from __future__ import annotations

import hashlib
import secrets

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.db import models


# ── helpers ──────────────────────────────────────────────────────────────────


def login(client: TestClient, email: str, password: str) -> str:
    r = client.post("/auth/login", json={"email": email, "password": password})
    assert r.status_code == 200, r.text
    return r.json()["access_token"]


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def make_folder_share(
    db: Session,
    user: models.User,
    slug: str = "bearer-test-share",
    items: list[dict] | None = None,
) -> models.Share:
    share = models.Share(
        kind=models.ShareKind.FOLDER,
        path="BearerTest/",
        visibility=models.ShareVisibility.PRIVATE,
        owner_user_id=user.id,
        web_published=True,
        web_slug=slug,
        web_folder_items=items or [],
    )
    db.add(share)
    db.commit()
    db.refresh(share)
    return share


def make_agent_key(db: Session, share: models.Share, scopes: str = "read") -> str:
    raw = "tr_agent_" + secrets.token_hex(24)
    key_hash = hashlib.sha256(raw.encode()).hexdigest()
    ak = models.ShareAgentKey(
        share_id=share.id, key_hash=key_hash, label="test-key", scopes=scopes
    )
    db.add(ak)
    db.commit()
    return raw


def add_member(db: Session, share: models.Share, user: models.User) -> None:
    member = models.ShareMember(
        share_id=share.id,
        user_id=user.id,
        role=models.ShareMemberRole.VIEWER,
    )
    db.add(member)
    db.commit()


MIXED_ITEMS = [
    {"path": "artifact.md", "type": "sync-artifact", "content": "artifact content", "mime": "text/markdown"},
    {"path": "vault-doc.md", "type": "doc", "content": "vault doc", "mime": "text/markdown"},
    {"path": "another-artifact.txt", "type": "sync-artifact", "content": "other artifact", "mime": "text/plain"},
]


# ── fixture ───────────────────────────────────────────────────────────────────


@pytest.fixture
def web_enabled(monkeypatch):
    monkeypatch.setenv("WEB_PUBLISH_DOMAIN", "docs.test.com")
    from app.core.config import get_settings
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def second_user(db_session: Session) -> models.User:
    from app.core import security as sec
    user = models.User(
        email="member@example.com",
        password_hash=sec.get_password_hash("test123456"),
        is_admin=False,
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    return user


@pytest.fixture
def stranger_user(db_session: Session) -> models.User:
    from app.core import security as sec
    user = models.User(
        email="stranger@example.com",
        password_hash=sec.get_password_hash("test123456"),
        is_admin=False,
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    return user


# ── /files-index Bearer auth ──────────────────────────────────────────────────


class TestFilesIndexBearerAuth:
    """Bearer JWT auth on GET /v1/web/shares/{id}/files-index."""

    def test_no_auth_returns_401(
        self, client: TestClient, db_session: Session, test_user: models.User, web_enabled
    ):
        share = make_folder_share(db_session, test_user, slug="fi-no-auth", items=MIXED_ITEMS)
        r = client.get(f"/v1/web/shares/{share.web_slug}/files-index")
        assert r.status_code == 401

    def test_bearer_owner_returns_200_and_filters_sync_artifacts(
        self, client: TestClient, db_session: Session, test_user: models.User, web_enabled
    ):
        share = make_folder_share(db_session, test_user, slug="fi-owner", items=MIXED_ITEMS)
        token = login(client, test_user.email, "test123456")
        r = client.get(f"/v1/web/shares/{share.web_slug}/files-index", headers=bearer(token))
        assert r.status_code == 200, r.text
        files = r.json()["files"]
        assert "artifact.md" in files
        assert "another-artifact.txt" in files
        assert "vault-doc.md" not in files, "Bearer path must filter to sync-artifact only"

    def test_bearer_member_returns_200(
        self,
        client: TestClient,
        db_session: Session,
        test_user: models.User,
        second_user: models.User,
        web_enabled,
    ):
        share = make_folder_share(db_session, test_user, slug="fi-member", items=MIXED_ITEMS)
        add_member(db_session, share, second_user)
        token = login(client, second_user.email, "test123456")
        r = client.get(f"/v1/web/shares/{share.web_slug}/files-index", headers=bearer(token))
        assert r.status_code == 200, r.text
        files = r.json()["files"]
        assert "artifact.md" in files

    def test_bearer_stranger_returns_403(
        self,
        client: TestClient,
        db_session: Session,
        test_user: models.User,
        stranger_user: models.User,
        web_enabled,
    ):
        share = make_folder_share(db_session, test_user, slug="fi-stranger", items=MIXED_ITEMS)
        token = login(client, stranger_user.email, "test123456")
        r = client.get(f"/v1/web/shares/{share.web_slug}/files-index", headers=bearer(token))
        assert r.status_code == 403

    def test_agent_key_still_returns_all_items(
        self, client: TestClient, db_session: Session, test_user: models.User, web_enabled
    ):
        """Regression: existing X-Agent-Key auth still works and returns all items."""
        share = make_folder_share(db_session, test_user, slug="fi-agentkey", items=MIXED_ITEMS)
        raw_key = make_agent_key(db_session, share, scopes="read")
        r = client.get(
            f"/v1/web/shares/{share.web_slug}/files-index",
            headers={"X-Agent-Key": raw_key},
        )
        assert r.status_code == 200, r.text
        files = r.json()["files"]
        assert "artifact.md" in files
        assert "vault-doc.md" in files, "Agent-key path must return all items (no filtering)"

    def test_invalid_bearer_returns_401(
        self, client: TestClient, db_session: Session, test_user: models.User, web_enabled
    ):
        share = make_folder_share(db_session, test_user, slug="fi-bad-token")
        r = client.get(
            f"/v1/web/shares/{share.web_slug}/files-index",
            headers={"Authorization": "Bearer totallywrong"},
        )
        assert r.status_code == 401

    def test_share_id_uuid_works_for_bearer(
        self, client: TestClient, db_session: Session, test_user: models.User, web_enabled
    ):
        """Bearer auth also works when addressing share by UUID (not slug)."""
        share = make_folder_share(db_session, test_user, slug="fi-uuid", items=MIXED_ITEMS)
        token = login(client, test_user.email, "test123456")
        r = client.get(f"/v1/web/shares/{share.id}/files-index", headers=bearer(token))
        assert r.status_code == 200, r.text


# ── /download Bearer auth ─────────────────────────────────────────────────────


class TestDownloadBearerAuth:
    """Bearer JWT auth on GET /v1/web/shares/{id}/download."""

    ITEMS = [
        {"path": "file.md", "type": "sync-artifact", "content": "hello world", "mime": "text/markdown"},
    ]

    def test_no_auth_returns_401(
        self, client: TestClient, db_session: Session, test_user: models.User, web_enabled
    ):
        share = make_folder_share(db_session, test_user, slug="dl-no-auth", items=self.ITEMS)
        r = client.get(f"/v1/web/shares/{share.web_slug}/download?path=file.md")
        assert r.status_code == 401

    def test_bearer_owner_returns_file(
        self, client: TestClient, db_session: Session, test_user: models.User, web_enabled
    ):
        share = make_folder_share(db_session, test_user, slug="dl-owner", items=self.ITEMS)
        token = login(client, test_user.email, "test123456")
        r = client.get(
            f"/v1/web/shares/{share.web_slug}/download?path=file.md", headers=bearer(token)
        )
        assert r.status_code == 200, r.text
        assert b"hello world" in r.content

    def test_bearer_member_returns_file(
        self,
        client: TestClient,
        db_session: Session,
        test_user: models.User,
        second_user: models.User,
        web_enabled,
    ):
        share = make_folder_share(db_session, test_user, slug="dl-member", items=self.ITEMS)
        add_member(db_session, share, second_user)
        token = login(client, second_user.email, "test123456")
        r = client.get(
            f"/v1/web/shares/{share.web_slug}/download?path=file.md", headers=bearer(token)
        )
        assert r.status_code == 200, r.text

    def test_bearer_stranger_returns_403(
        self,
        client: TestClient,
        db_session: Session,
        test_user: models.User,
        stranger_user: models.User,
        web_enabled,
    ):
        share = make_folder_share(db_session, test_user, slug="dl-stranger", items=self.ITEMS)
        token = login(client, stranger_user.email, "test123456")
        r = client.get(
            f"/v1/web/shares/{share.web_slug}/download?path=file.md", headers=bearer(token)
        )
        assert r.status_code == 403

    def test_bearer_file_not_found_returns_404(
        self, client: TestClient, db_session: Session, test_user: models.User, web_enabled
    ):
        share = make_folder_share(db_session, test_user, slug="dl-missing", items=self.ITEMS)
        token = login(client, test_user.email, "test123456")
        r = client.get(
            f"/v1/web/shares/{share.web_slug}/download?path=nonexistent.md",
            headers=bearer(token),
        )
        assert r.status_code == 404

    def test_agent_key_still_works_for_download(
        self, client: TestClient, db_session: Session, test_user: models.User, web_enabled
    ):
        """Regression: existing X-Agent-Key download still works."""
        share = make_folder_share(db_session, test_user, slug="dl-agentkey", items=self.ITEMS)
        raw_key = make_agent_key(db_session, share, scopes="read")
        r = client.get(
            f"/v1/web/shares/{share.web_slug}/download?path=file.md",
            headers={"X-Agent-Key": raw_key},
        )
        assert r.status_code == 200, r.text
        assert b"hello world" in r.content

    def test_invalid_bearer_returns_401(
        self, client: TestClient, db_session: Session, test_user: models.User, web_enabled
    ):
        share = make_folder_share(db_session, test_user, slug="dl-bad-token", items=self.ITEMS)
        r = client.get(
            f"/v1/web/shares/{share.web_slug}/download?path=file.md",
            headers={"Authorization": "Bearer bad.token.here"},
        )
        assert r.status_code == 401
