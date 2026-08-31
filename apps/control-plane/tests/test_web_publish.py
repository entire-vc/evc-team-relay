"""Tests for web publishing functionality."""

from __future__ import annotations

import hashlib
import secrets

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core.security import get_password_hash
from app.db import models
from app.services.web_session_service import WebSessionService
from app.utils.slug import generate_unique_slug, is_slug_available, slugify


class TestSlugGeneration:
    """Test slug generation utilities."""

    def test_slugify_simple_path(self):
        """Test basic slug generation from path."""
        assert slugify("Projects/My Document.md") == "projects-my-document"
        assert slugify("Test File.md") == "test-file"

    def test_slugify_cyrillic(self):
        """Test Cyrillic transliteration."""
        assert slugify("Мой Проект/Документ.md") == "moj-proekt-dokument"
        assert slugify("Тестовый файл.md") == "testovyj-fajl"

    def test_slugify_special_chars(self):
        """Test special character handling."""
        assert slugify("Test___File  .md") == "test-file"
        assert slugify("A@#B$%C.md") == "a-b-c"
        assert slugify("File (1).md") == "file-1"

    def test_slugify_reserved_chars(self):
        """Test path separator handling."""
        assert slugify("Projects/Subdir/File.md") == "projects-subdir-file"
        assert slugify("Projects\\Subdir\\File.md") == "projects-subdir-file"

    def test_slugify_truncation(self):
        """Test slug is truncated to 100 chars."""
        long_name = "a" * 150 + ".md"
        slug = slugify(long_name)
        assert len(slug) <= 100

    def test_slugify_empty_result(self):
        """Test slug generation from path with only special chars."""
        # Emojis and special chars should result in empty slug
        assert slugify("...") == ""
        assert slugify("@@@.md") == ""

    def test_is_slug_available_reserved(self, db_session: Session):
        """Test reserved slugs are not available."""
        assert not is_slug_available(db_session, "login")
        assert not is_slug_available(db_session, "api")
        assert not is_slug_available(db_session, "robots.txt")

    def test_is_slug_available_unique(self, db_session: Session):
        """Test unique slug is available."""
        assert is_slug_available(db_session, "my-unique-slug-12345")

    def test_generate_unique_slug_no_collision(self, db_session: Session):
        """Test unique slug generation without collision."""
        slug = generate_unique_slug(db_session, "Projects/Document.md")
        assert slug == "projects-document"

    def test_generate_unique_slug_with_collision(self, db_session: Session, test_user: models.User):
        """Test unique slug generation with collision."""
        # Create first share with slug
        share1 = models.Share(
            kind=models.ShareKind.DOC,
            path="Projects/Doc.md",
            visibility=models.ShareVisibility.PUBLIC,
            owner_user_id=test_user.id,
            web_published=True,
            web_slug="projects-doc",
        )
        db_session.add(share1)
        db_session.commit()

        # Generate slug for same path
        slug = generate_unique_slug(db_session, "Projects/Doc.md")
        assert slug == "projects-doc-2"

    def test_generate_unique_slug_empty_fallback(self, db_session: Session):
        """Test fallback to UUID-based slug for empty paths."""
        slug = generate_unique_slug(db_session, "...")
        assert slug.startswith("share-")
        assert len(slug) > 6  # "share-" + uuid prefix

    def test_generate_unique_slug_reserved_rejection(self, db_session: Session):
        """Test that reserved slugs are rejected and alternatives are generated."""
        # Try to generate slug for path that would result in reserved slug
        slug = generate_unique_slug(db_session, "api.md")
        # Should generate alternative since "api" is reserved
        assert slug != "api"
        assert "api" in slug  # Should contain original but be modified

        slug2 = generate_unique_slug(db_session, "login.md")
        assert slug2 != "login"
        assert "login" in slug2


class TestWebPublishEndpoints:
    """Test web publishing API endpoints."""

    def test_get_share_by_slug_not_enabled(self, client: TestClient):
        """Test that web endpoints return 404 when web publishing is disabled."""
        response = client.get("/v1/web/shares/test-slug")
        assert response.status_code == 404
        body = response.json()
        # Error middleware wraps response in error object
        if "error" in body:
            message = body["error"]["message"]
        else:
            message = body.get("detail", "")
        assert "not enabled" in message.lower() or "not published" in message.lower()

    def test_robots_txt_not_enabled(self, client: TestClient):
        """Test robots.txt returns 404 when web publishing is disabled."""
        response = client.get("/v1/web/robots.txt")
        assert response.status_code == 404

    def test_robots_txt_no_indexable_shares_still_allows_crawl(
        self, client: TestClient, db_session: Session, test_user: models.User, monkeypatch
    ):
        """robots.txt is crawl control, not index control (#a38092aa/#ffbe9108):
        `Allow: /` + the two service-path Disallows are unconditional -- they
        do NOT depend on any share being indexable. This is the negative
        control §0x requires: with zero indexable shares, `Sitemap:` and the
        old `# Indexable shares` block must be ABSENT, while `Allow: /` is
        still PRESENT. A bare `assert "Disallow: /" in content` would pass
        against `Disallow: /api/` too, so assert on the exact line set."""
        # Enable web publishing for this test
        monkeypatch.setenv("WEB_PUBLISH_DOMAIN", "docs.test.com")
        from app.core.config import get_settings

        get_settings.cache_clear()

        # Create a share with noindex=True (default) -- not indexable.
        share = models.Share(
            kind=models.ShareKind.DOC,
            path="Test.md",
            visibility=models.ShareVisibility.PUBLIC,
            owner_user_id=test_user.id,
            web_published=True,
            web_slug="test-doc",
            web_noindex=True,
        )
        db_session.add(share)
        db_session.commit()

        response = client.get("/v1/web/robots.txt")
        assert response.status_code == 200
        assert response.headers["content-type"] == "text/plain; charset=utf-8"
        lines = response.text.splitlines()
        assert "User-agent: *" in lines
        assert "Allow: /" in lines
        assert "Disallow: /api/" in lines
        assert "Disallow: /login" in lines
        # Crawling is unconditional now -- no bare "Disallow: /" line either.
        assert "Disallow: /" not in lines
        # Nothing to index -> no positive signal, and the old per-slug
        # enumeration block is gone entirely.
        assert not any(line.startswith("Sitemap:") for line in lines)
        assert "# Indexable shares" not in response.text
        assert "test-doc" not in response.text

        get_settings.cache_clear()

    def test_robots_txt_sitemap_line_present_when_indexable_shares_exist(
        self, client: TestClient, db_session: Session, test_user: models.User, monkeypatch
    ):
        """Positive control for the above: an indexable share adds the
        `Sitemap:` pointer, but robots.txt no longer enumerates any
        per-share `Allow: /{slug}` line -- that population moved entirely
        to sitemap.xml, which already carries its own coverage below."""
        monkeypatch.setenv("WEB_PUBLISH_DOMAIN", "docs.test.com")
        from app.core.config import get_settings

        get_settings.cache_clear()

        share1 = models.Share(
            kind=models.ShareKind.DOC,
            path="Public/Doc1.md",
            visibility=models.ShareVisibility.PUBLIC,
            owner_user_id=test_user.id,
            web_published=True,
            web_slug="public-doc-1",
            web_noindex=False,  # Indexable
        )
        share2 = models.Share(
            kind=models.ShareKind.DOC,
            path="Private/Doc.md",
            visibility=models.ShareVisibility.PRIVATE,
            owner_user_id=test_user.id,
            web_published=True,
            web_slug="private-doc",
            web_noindex=True,  # Not indexable
        )
        db_session.add_all([share1, share2])
        db_session.commit()

        response = client.get("/v1/web/robots.txt")
        assert response.status_code == 200
        lines = response.text.splitlines()

        assert "User-agent: *" in lines
        assert "Allow: /" in lines
        assert "Disallow: /api/" in lines
        assert "Disallow: /login" in lines
        assert "Sitemap: https://docs.test.com/sitemap.xml" in lines

        # No slug of any share -- indexable or not -- is ever listed anymore.
        assert "public-doc-1" not in response.text
        assert "private-doc" not in response.text
        assert not any(line.startswith("Allow: /") and line != "Allow: /" for line in lines)

        get_settings.cache_clear()

    def test_robots_txt_never_lists_share_slugs_regardless_of_visibility(
        self, client: TestClient, db_session: Session, test_user: models.User, monkeypatch
    ):
        """TR-44's leak this test used to guard against (a private/protected
        published+indexable share's slug appearing in robots.txt) can no
        longer occur by construction -- robots.txt doesn't enumerate share
        slugs at all after #ffbe9108. Kept as a regression guard: mixed
        visibility shares must still produce zero slug leakage, whatever the
        mechanism. The equivalent visibility filter for sitemap.xml (which
        DOES still enumerate shares) is separately covered by
        test_sitemap_xml_excludes_non_public_visibility below -- unweakened."""
        monkeypatch.setenv("WEB_PUBLISH_DOMAIN", "docs.test.com")
        from app.core.config import get_settings

        get_settings.cache_clear()

        leaky_private = models.Share(
            kind=models.ShareKind.DOC,
            path="Private/Leaky.md",
            visibility=models.ShareVisibility.PRIVATE,
            owner_user_id=test_user.id,
            web_published=True,
            web_slug="leaky-private-doc",
            web_noindex=False,
        )
        leaky_protected = models.Share(
            kind=models.ShareKind.DOC,
            path="Protected/Leaky.md",
            visibility=models.ShareVisibility.PROTECTED,
            owner_user_id=test_user.id,
            web_published=True,
            web_slug="leaky-protected-doc",
            web_noindex=False,
            password_hash="x",
        )
        genuinely_public = models.Share(
            kind=models.ShareKind.DOC,
            path="Public/Real.md",
            visibility=models.ShareVisibility.PUBLIC,
            owner_user_id=test_user.id,
            web_published=True,
            web_slug="genuinely-public-doc",
            web_noindex=False,
        )
        db_session.add_all([leaky_private, leaky_protected, genuinely_public])
        db_session.commit()

        response = client.get("/v1/web/robots.txt")
        assert response.status_code == 200
        content = response.text

        assert "leaky-private-doc" not in content
        assert "leaky-protected-doc" not in content
        assert "genuinely-public-doc" not in content
        assert "Allow: /" in content.splitlines()

        get_settings.cache_clear()

    def test_sitemap_xml_not_enabled(self, client: TestClient):
        """Test sitemap.xml returns 404 when web publishing is disabled."""
        response = client.get("/v1/web/sitemap.xml")
        assert response.status_code == 404

    def test_sitemap_xml_empty_when_no_indexable_shares(
        self, client: TestClient, db_session: Session, test_user: models.User, monkeypatch
    ):
        """Test sitemap.xml is a valid empty urlset when nothing is indexable."""
        monkeypatch.setenv("WEB_PUBLISH_DOMAIN", "docs.test.com")
        from app.core.config import get_settings

        get_settings.cache_clear()

        share = models.Share(
            kind=models.ShareKind.DOC,
            path="Test.md",
            visibility=models.ShareVisibility.PUBLIC,
            owner_user_id=test_user.id,
            web_published=True,
            web_slug="test-doc",
            web_noindex=True,  # Not indexable
        )
        db_session.add(share)
        db_session.commit()

        response = client.get("/v1/web/sitemap.xml")
        assert response.status_code == 200
        assert response.headers["content-type"] == "application/xml"
        content = response.text
        assert '<?xml version="1.0" encoding="UTF-8"?>' in content
        assert '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">' in content
        assert "<url>" not in content

        get_settings.cache_clear()

    def test_sitemap_xml_includes_indexable_public_shares(
        self, client: TestClient, db_session: Session, test_user: models.User, monkeypatch
    ):
        """Test sitemap.xml lists public+published+indexable shares with lastmod."""
        monkeypatch.setenv("WEB_PUBLISH_DOMAIN", "docs.test.com")
        from datetime import datetime, timezone

        from app.core.config import get_settings

        get_settings.cache_clear()

        updated_at = datetime(2026, 7, 15, 12, 0, 0, tzinfo=timezone.utc)
        share1 = models.Share(
            kind=models.ShareKind.DOC,
            path="Public/Doc1.md",
            visibility=models.ShareVisibility.PUBLIC,
            owner_user_id=test_user.id,
            web_published=True,
            web_slug="public-doc-1",
            web_noindex=False,
            web_content_updated_at=updated_at,
        )
        share2 = models.Share(
            kind=models.ShareKind.DOC,
            path="Public/Doc2.md",
            visibility=models.ShareVisibility.PUBLIC,
            owner_user_id=test_user.id,
            web_published=True,
            web_slug="public-doc-2",
            web_noindex=False,
        )
        db_session.add_all([share1, share2])
        db_session.commit()

        response = client.get("/v1/web/sitemap.xml")
        assert response.status_code == 200
        content = response.text

        assert "<loc>https://docs.test.com/public-doc-1</loc>" in content
        assert "<lastmod>2026-07-15</lastmod>" in content
        assert "<loc>https://docs.test.com/public-doc-2</loc>" in content

        get_settings.cache_clear()

    def test_sitemap_xml_excludes_non_public_visibility(
        self, client: TestClient, db_session: Session, test_user: models.User, monkeypatch
    ):
        """TR-44/TR-61: sitemap.xml must apply the same visibility=public filter
        as robots.txt — a private/protected published+indexable share must not
        leak its URL into the sitemap either."""
        monkeypatch.setenv("WEB_PUBLISH_DOMAIN", "docs.test.com")
        from app.core.config import get_settings

        get_settings.cache_clear()

        leaky_private = models.Share(
            kind=models.ShareKind.DOC,
            path="Private/Leaky.md",
            visibility=models.ShareVisibility.PRIVATE,
            owner_user_id=test_user.id,
            web_published=True,
            web_slug="leaky-private-doc",
            web_noindex=False,
        )
        genuinely_public = models.Share(
            kind=models.ShareKind.DOC,
            path="Public/Real.md",
            visibility=models.ShareVisibility.PUBLIC,
            owner_user_id=test_user.id,
            web_published=True,
            web_slug="genuinely-public-doc",
            web_noindex=False,
        )
        db_session.add_all([leaky_private, genuinely_public])
        db_session.commit()

        response = client.get("/v1/web/sitemap.xml")
        assert response.status_code == 200
        content = response.text

        assert "leaky-private-doc" not in content
        assert "<loc>https://docs.test.com/genuinely-public-doc</loc>" in content

        get_settings.cache_clear()


def _auth_headers(token: str) -> dict[str, str]:
    """Helper to create auth headers from token."""
    return {"Authorization": f"Bearer {token}"}


class TestShareWebFields:
    """Test share model web publishing fields."""

    def test_create_share_without_web_publish(self, client: TestClient, test_user: models.User):
        """Test creating share without web publishing."""
        # Login to get token
        login_response = client.post(
            "/auth/login", json={"email": test_user.email, "password": "test123456"}
        )
        token = login_response.json()["access_token"]

        response = client.post(
            "/v1/shares",
            json={
                "kind": "doc",
                "path": "Test.md",
                "visibility": "public",
            },
            headers=_auth_headers(token),
        )
        assert response.status_code == 201
        data = response.json()
        assert data["web_published"] is False
        assert data["web_slug"] is None
        assert data["web_noindex"] is True
        assert data["web_url"] is None

    def test_create_share_with_web_publish(self, client: TestClient, test_user: models.User):
        """Test creating share with web publishing enabled."""
        login_response = client.post(
            "/auth/login", json={"email": test_user.email, "password": "test123456"}
        )
        token = login_response.json()["access_token"]

        response = client.post(
            "/v1/shares",
            json={
                "kind": "doc",
                "path": "Public Doc.md",
                # TR-39 guard: public+published+no-content is rejected — this test is
                # about slug/publish mechanics, not visibility, so create private.
                "visibility": "private",
                "web_published": True,
            },
            headers=_auth_headers(token),
        )
        assert response.status_code == 201
        data = response.json()
        assert data["web_published"] is True
        assert data["web_slug"] == "public-doc"
        assert data["web_noindex"] is True  # default

    def test_create_share_with_custom_slug(self, client: TestClient, test_user: models.User):
        """Test creating share with custom slug."""
        login_response = client.post(
            "/auth/login", json={"email": test_user.email, "password": "test123456"}
        )
        token = login_response.json()["access_token"]

        response = client.post(
            "/v1/shares",
            json={
                "kind": "doc",
                "path": "Some Doc.md",
                # TR-39 guard: this test is about slug generation, not visibility.
                "visibility": "private",
                "web_published": True,
                "web_slug": "my-custom-slug",
            },
            headers=_auth_headers(token),
        )
        assert response.status_code == 201
        data = response.json()
        assert data["web_slug"] == "my-custom-slug"

    def test_create_share_reserved_slug_rejected(self, client: TestClient, test_user: models.User):
        """Test that reserved slugs are rejected."""
        login_response = client.post(
            "/auth/login", json={"email": test_user.email, "password": "test123456"}
        )
        token = login_response.json()["access_token"]

        # Try to create share with reserved slug
        response = client.post(
            "/v1/shares",
            json={
                "kind": "doc",
                "path": "API Docs.md",
                # TR-39 guard: isolate the reserved-slug check being tested here from
                # the (unrelated) public+no-content guard.
                "visibility": "private",
                "web_published": True,
                "web_slug": "api",  # Reserved
            },
            headers=_auth_headers(token),
        )
        assert response.status_code == 400  # Bad request (from share_service)
        body = response.json()
        # Error middleware wraps response
        detail = body.get("detail") or body.get("error", {}).get("message", "")
        assert "reserved" in detail.lower() or "taken" in detail.lower()

    def test_create_share_duplicate_slug_rejected(self, client: TestClient, test_user: models.User):
        """Test that duplicate slugs are rejected."""
        login_response = client.post(
            "/auth/login", json={"email": test_user.email, "password": "test123456"}
        )
        token = login_response.json()["access_token"]
        headers = _auth_headers(token)

        # Create first share
        response1 = client.post(
            "/v1/shares",
            json={
                "kind": "doc",
                "path": "Doc1.md",
                # TR-39 guard: isolate the duplicate-slug check from the (unrelated)
                # public+no-content guard.
                "visibility": "private",
                "web_published": True,
                "web_slug": "my-slug",
            },
            headers=headers,
        )
        assert response1.status_code == 201

        # Try to create second share with same slug
        response2 = client.post(
            "/v1/shares",
            json={
                "kind": "doc",
                "path": "Doc2.md",
                "visibility": "private",
                "web_published": True,
                "web_slug": "my-slug",  # Duplicate
            },
            headers=headers,
        )
        assert response2.status_code == 400  # Bad request (from share_service)
        body = response2.json()
        # Error middleware wraps response
        detail = body.get("detail") or body.get("error", {}).get("message", "")
        assert "taken" in detail.lower() or "already" in detail.lower()

    def test_update_share_enable_web_publish(self, client: TestClient, test_user: models.User):
        """Test enabling web publishing on existing share."""
        login_response = client.post(
            "/auth/login", json={"email": test_user.email, "password": "test123456"}
        )
        token = login_response.json()["access_token"]
        headers = _auth_headers(token)

        # Create share (TR-39: private — this test is about the enable-publish
        # transition, not visibility; enabling publish while public+no-content
        # would hit the new guard on the PATCH below)
        create_response = client.post(
            "/v1/shares",
            json={
                "kind": "doc",
                "path": "Test Doc.md",
                "visibility": "private",
            },
            headers=headers,
        )
        assert create_response.status_code == 201
        share_id = create_response.json()["id"]

        # Enable web publishing
        update_response = client.patch(
            f"/v1/shares/{share_id}",
            json={"web_published": True},
            headers=headers,
        )
        assert update_response.status_code == 200
        data = update_response.json()
        assert data["web_published"] is True
        assert data["web_slug"] == "test-doc"

    def test_update_share_disable_web_publish(self, client: TestClient, test_user: models.User):
        """Test disabling web publishing on existing share."""
        login_response = client.post(
            "/auth/login", json={"email": test_user.email, "password": "test123456"}
        )
        token = login_response.json()["access_token"]
        headers = _auth_headers(token)

        # Create share with web publishing enabled (TR-39: private — this test is
        # about disabling publish, not visibility)
        create_response = client.post(
            "/v1/shares",
            json={
                "kind": "doc",
                "path": "Test Doc.md",
                "visibility": "private",
                "web_published": True,
            },
            headers=headers,
        )
        assert create_response.status_code == 201
        share_id = create_response.json()["id"]
        assert create_response.json()["web_published"] is True

        # Disable web publishing
        update_response = client.patch(
            f"/v1/shares/{share_id}",
            json={"web_published": False},
            headers=headers,
        )
        assert update_response.status_code == 200
        data = update_response.json()
        assert data["web_published"] is False
        assert data["web_url"] is None

    def test_update_share_custom_slug(self, client: TestClient, test_user: models.User):
        """Test updating share with custom slug."""
        login_response = client.post(
            "/auth/login", json={"email": test_user.email, "password": "test123456"}
        )
        token = login_response.json()["access_token"]
        headers = _auth_headers(token)

        # Create share with web publishing (TR-39: private — this test is about
        # slug updates, not visibility)
        create_response = client.post(
            "/v1/shares",
            json={
                "kind": "doc",
                "path": "Test Doc.md",
                "visibility": "private",
                "web_published": True,
            },
            headers=headers,
        )
        assert create_response.status_code == 201
        share_id = create_response.json()["id"]

        # Update with custom slug
        update_response = client.patch(
            f"/v1/shares/{share_id}",
            json={"web_slug": "my-custom-url"},
            headers=headers,
        )
        assert update_response.status_code == 200
        data = update_response.json()
        assert data["web_slug"] == "my-custom-url"

    def test_server_info_includes_web_publish_features(self, client: TestClient):
        """Test that /server/info includes web publishing feature flags."""
        response = client.get("/v1/server/info")
        assert response.status_code == 200
        data = response.json()
        assert "features" in data
        assert "web_publish_enabled" in data["features"]
        assert "web_publish_domain" in data["features"]
        # By default should be disabled (no WEB_PUBLISH_DOMAIN env var)
        assert data["features"]["web_publish_enabled"] is False
        assert data["features"]["web_publish_domain"] is None

    def test_update_share_with_web_content(self, client: TestClient, test_user: models.User):
        """Test updating share with web_content."""
        login_response = client.post(
            "/auth/login", json={"email": test_user.email, "password": "test123456"}
        )
        token = login_response.json()["access_token"]

        # Create a share
        create_response = client.post(
            "/v1/shares",
            json={
                "kind": "doc",
                "path": "Content Test.md",
                # TR-39 guard: create private, then the PATCH below sets content
                # while the share stays private — this test is about the content
                # field itself, not the public-visibility interaction (covered
                # separately in test_public_content_guard.py).
                "visibility": "private",
                "web_published": True,
            },
            headers=_auth_headers(token),
        )
        assert create_response.status_code == 201
        share_id = create_response.json()["id"]

        # Update with web_content
        test_content = "# Hello World\n\nThis is a test document."
        update_response = client.patch(
            f"/v1/shares/{share_id}",
            json={"web_content": test_content},
            headers=_auth_headers(token),
        )
        assert update_response.status_code == 200

    def test_update_share_clear_web_content(self, client: TestClient, test_user: models.User):
        """Test clearing web_content by setting empty string."""
        login_response = client.post(
            "/auth/login", json={"email": test_user.email, "password": "test123456"}
        )
        token = login_response.json()["access_token"]

        # Create a share
        create_response = client.post(
            "/v1/shares",
            json={
                "kind": "doc",
                "path": "Clear Content Test.md",
                # TR-39 guard: private — this test's interaction with the public
                # guard specifically is covered in test_public_content_guard.py.
                "visibility": "private",
                "web_published": True,
            },
            headers=_auth_headers(token),
        )
        assert create_response.status_code == 201
        share_id = create_response.json()["id"]

        # Set content first
        client.patch(
            f"/v1/shares/{share_id}",
            json={"web_content": "Some content"},
            headers=_auth_headers(token),
        )

        # Clear content
        update_response = client.patch(
            f"/v1/shares/{share_id}",
            json={"web_content": ""},
            headers=_auth_headers(token),
        )
        assert update_response.status_code == 200


class TestWebSessionService:
    """Test web session token management."""

    def test_create_web_session(self):
        """Test creating a web session token."""
        share_id = 123
        token = WebSessionService.create_web_session(share_id, hours=24)

        assert token is not None
        assert isinstance(token, str)
        assert len(token) > 0

    def test_validate_web_session_valid(self):
        """Test validating a valid web session token."""
        share_id = 123
        token = WebSessionService.create_web_session(share_id, hours=24)

        is_valid = WebSessionService.validate_web_session(token, share_id)
        assert is_valid is True

    def test_validate_web_session_wrong_share_id(self):
        """Test validating token for wrong share ID."""
        share_id = 123
        token = WebSessionService.create_web_session(share_id, hours=24)

        is_valid = WebSessionService.validate_web_session(token, share_id=456)
        assert is_valid is False

    def test_validate_web_session_invalid_token(self):
        """Test validating invalid token."""
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc_info:
            WebSessionService.validate_web_session("invalid.token.here", share_id=123)

        assert exc_info.value.status_code == 401

    def test_decode_web_session(self):
        """Test decoding a web session token."""
        share_id = 123
        token = WebSessionService.create_web_session(share_id, hours=24)

        payload = WebSessionService.decode_web_session(token)

        assert payload["sub"] == str(share_id)
        assert payload["type"] == "web_session"
        assert "iat" in payload
        assert "exp" in payload
        assert "jti" in payload


class TestProtectedShareAuth:
    """Test protected share authentication flow."""

    @pytest.fixture
    def protected_share(self, db_session: Session, test_user: models.User) -> models.Share:
        """Create a protected share with password."""
        share = models.Share(
            kind=models.ShareKind.DOC,
            path="Protected/Doc.md",
            visibility=models.ShareVisibility.PROTECTED,
            owner_user_id=test_user.id,
            web_published=True,
            web_slug="protected-doc",
            password_hash=get_password_hash("test123"),
        )
        db_session.add(share)
        db_session.commit()
        db_session.refresh(share)
        return share

    def test_get_protected_share_without_auth(
        self, client: TestClient, protected_share: models.Share
    ):
        """Test that protected share metadata can be fetched without auth."""
        # This endpoint only returns metadata, not content
        # The frontend will then prompt for password
        response = client.get(f"/v1/web/shares/{protected_share.web_slug}")

        # Should return 404 because web publishing is disabled by default
        assert response.status_code == 404

    def test_authenticate_protected_share_success(
        self, client: TestClient, protected_share: models.Share
    ):
        """Test successful authentication for protected share."""
        response = client.post(
            f"/v1/web/shares/{protected_share.web_slug}/auth",
            json={"password": "test123"},
        )

        # Should return 404 because web publishing is disabled by default
        # In actual deployment with WEB_PUBLISH_DOMAIN set, this would be 200
        assert response.status_code == 404

    def test_authenticate_protected_share_wrong_password(
        self, client: TestClient, protected_share: models.Share
    ):
        """Test authentication with wrong password."""
        response = client.post(
            f"/v1/web/shares/{protected_share.web_slug}/auth",
            json={"password": "wrongpassword"},
        )

        # Should return 404 (web disabled) or 401 (wrong password)
        assert response.status_code in [401, 404]

    def test_authenticate_protected_share_missing_password(
        self, client: TestClient, protected_share: models.Share
    ):
        """Test authentication with missing password."""
        response = client.post(
            f"/v1/web/shares/{protected_share.web_slug}/auth",
            json={},
        )

        # Should return 422 (validation error) or 404 (web disabled)
        assert response.status_code in [422, 404]

    def test_validate_share_session_with_cookie(
        self, client: TestClient, protected_share: models.Share
    ):
        """Test session validation with valid cookie."""
        # Create a valid session token
        token = WebSessionService.create_web_session(protected_share.id, hours=24)

        response = client.get(
            f"/v1/web/shares/{protected_share.web_slug}/validate",
            cookies={"web_session": token},
        )

        # Should return 404 because web publishing is disabled
        # In production with web enabled, would return 200 with valid=true
        assert response.status_code == 404

    def test_validate_share_session_without_cookie(
        self, client: TestClient, protected_share: models.Share
    ):
        """Test session validation without cookie."""
        response = client.get(f"/v1/web/shares/{protected_share.web_slug}/validate")

        # Should return 404 because web publishing is disabled
        assert response.status_code == 404

    def test_protected_share_password_rate_limiting(
        self, client: TestClient, db_session: Session, test_user: models.User, monkeypatch
    ):
        """Test that password attempts are rate limited (5 per minute)."""
        # Enable web publishing for this test
        monkeypatch.setenv("WEB_PUBLISH_DOMAIN", "docs.test.com")
        from app.core.config import get_settings

        get_settings.cache_clear()

        # Create protected share
        share = models.Share(
            kind=models.ShareKind.DOC,
            path="Protected/Doc.md",
            visibility=models.ShareVisibility.PROTECTED,
            owner_user_id=test_user.id,
            web_published=True,
            web_slug="protected-rate-limit",
            password_hash=get_password_hash("correct123"),
        )
        db_session.add(share)
        db_session.commit()

        # Make 5 failed attempts (should all go through)
        for i in range(5):
            response = client.post(
                f"/v1/web/shares/{share.web_slug}/auth",
                json={"password": f"wrong{i}"},
            )
            # Should get 401 unauthorized (wrong password)
            assert response.status_code == 401

        # 6th attempt should be rate limited
        response = client.post(
            f"/v1/web/shares/{share.web_slug}/auth",
            json={"password": "wrong6"},
        )
        assert response.status_code == 429  # Too Many Requests

        # Clean up
        get_settings.cache_clear()


class TestPrivateShareAuth:
    """Test private share authentication requirements."""

    @pytest.fixture
    def private_share(self, db_session: Session, test_user: models.User) -> models.Share:
        """Create a private share."""
        share = models.Share(
            kind=models.ShareKind.DOC,
            path="Private/Doc.md",
            visibility=models.ShareVisibility.PRIVATE,
            owner_user_id=test_user.id,
            web_published=True,
            web_slug="private-doc",
        )
        db_session.add(share)
        db_session.commit()
        db_session.refresh(share)
        return share

    def test_get_private_share_metadata(self, client: TestClient, private_share: models.Share):
        """Test that private share metadata can be fetched (for showing login prompt)."""
        response = client.get(f"/v1/web/shares/{private_share.web_slug}")

        # Should return 404 because web publishing is disabled by default
        # With web enabled, would return 200 with visibility=private
        # The frontend then shows "login required" message
        assert response.status_code == 404

    @pytest.fixture
    def private_folder_share(self, db_session: Session, test_user: models.User) -> models.Share:
        share = models.Share(
            kind=models.ShareKind.FOLDER,
            path="Private/Folder/",
            visibility=models.ShareVisibility.PRIVATE,
            owner_user_id=test_user.id,
            web_published=True,
            web_slug="private-folder-auth",
            web_folder_items=[{"path": "note.md", "name": "note.md", "type": "doc"}],
        )
        db_session.add(share)
        db_session.commit()
        db_session.refresh(share)
        return share

    def _make_agent_key(self, db, share, scopes="read"):
        import hashlib as _hl
        import secrets as _sec

        raw = "tr_agent_" + _sec.token_hex(24)
        key_hash = _hl.sha256(raw.encode()).hexdigest()
        ak = models.ShareAgentKey(
            share_id=share.id,
            key_hash=key_hash,
            label="test-key",
            scopes=scopes,
            # TR-03 (#ae52ba05): creator must still be owner/member to auth.
            created_by=share.owner_user_id,
        )
        db.add(ak)
        db.commit()
        db.refresh(ak)
        return raw, ak

    def _web_settings(self):
        return type(
            "S",
            (),
            {
                "web_publish_enabled": True,
                "web_publish_domain": "docs.test.com",
                "web_frame_ancestors": None,
                "agent_key_lenient_read_grace": True,
            },
        )()

    def test_get_private_share_no_credentials_returns_200_stripped(
        self, client: TestClient, private_folder_share: models.Share, monkeypatch
    ):
        """No credentials on PRIVATE share → 200 stripped (SPA shows login prompt)."""
        monkeypatch.setattr("app.api.routers.web.get_settings", lambda: self._web_settings())
        r = client.get(f"/v1/web/shares/{private_folder_share.web_slug}")
        assert r.status_code == 200
        data = r.json()
        assert data["visibility"] == "private"
        assert data["web_folder_items"] is None

    def test_get_private_share_invalid_agent_key_header_returns_401(
        self, client: TestClient, private_folder_share: models.Share, monkeypatch
    ):
        """Invalid X-Agent-Key header on PRIVATE share → 401 (not 200 with stripped content)."""
        monkeypatch.setattr("app.api.routers.web.get_settings", lambda: self._web_settings())
        r = client.get(
            f"/v1/web/shares/{private_folder_share.web_slug}",
            headers={"X-Agent-Key": "invalid_garbage_key"},
        )
        assert r.status_code == 401

    def test_get_private_share_agent_key_query_param_is_ignored(
        self,
        client: TestClient,
        db_session: Session,
        private_folder_share: models.Share,
        monkeypatch,
    ):
        """?agent_key= query param is no longer read at all (TR-14) — even a VALID key
        sent this way must NOT grant access. It's treated as no credential at all
        (200 stripped), not as an invalid-credential 401, because the endpoint can't
        tell "wrong channel" apart from "nothing provided" once it stops reading the
        query string."""
        monkeypatch.setattr("app.api.routers.web.get_settings", lambda: self._web_settings())
        raw_key, _ = self._make_agent_key(db_session, private_folder_share, scopes="read")
        r = client.get(f"/v1/web/shares/{private_folder_share.web_slug}?agent_key={raw_key}")
        assert r.status_code == 200
        data = r.json()
        assert data["web_folder_items"] is None

    def test_get_private_share_invalid_bearer_returns_401(
        self, client: TestClient, private_folder_share: models.Share, monkeypatch
    ):
        """Invalid Bearer token on PRIVATE share → 401."""
        monkeypatch.setattr("app.api.routers.web.get_settings", lambda: self._web_settings())
        r = client.get(
            f"/v1/web/shares/{private_folder_share.web_slug}",
            headers={"Authorization": "Bearer TOTALLY_FAKE_TOKEN"},
        )
        assert r.status_code == 401

    def test_get_private_share_valid_agent_key_returns_200_with_content(
        self,
        client: TestClient,
        db_session: Session,
        private_folder_share: models.Share,
        monkeypatch,
    ):
        """Valid agent_key (X-Agent-Key header) on PRIVATE share → 200 with folder items."""
        monkeypatch.setattr("app.api.routers.web.get_settings", lambda: self._web_settings())
        raw_key, _ = self._make_agent_key(db_session, private_folder_share, scopes="read")
        r = client.get(
            f"/v1/web/shares/{private_folder_share.web_slug}",
            headers={"X-Agent-Key": raw_key},
        )
        assert r.status_code == 200
        data = r.json()
        assert data["web_folder_items"] is not None
        assert len(data["web_folder_items"]) == 1


class TestWebRelayTokenEndpointRemoved:
    """#edfd1dd3 AC#5: GET /v1/web/shares/{slug}/token no longer exists.

    It used to issue a relay token in a format relay-server can't parse
    (JWT vs the CWT/legacy-bincode it actually understands), for a
    live-sync feature that never worked for any real share (web_doc_id
    was unset on 100% of them) and had two further downstream defects
    even where it was reachable. Removed rather than fixed — see the
    task thread for the full decision.
    """

    def test_token_endpoint_returns_404_for_published_share(
        self, client: TestClient, test_user: models.User, monkeypatch
    ):
        monkeypatch.setattr("app.api.routers.web.get_settings", lambda: _web_enabled_settings())
        r = client.post("/auth/login", json={"email": test_user.email, "password": "test123456"})
        token = r.json()["access_token"]
        create = client.post(
            "/v1/shares",
            json={
                "kind": "doc",
                "path": "Removed Token Endpoint.md",
                # TR-39 guard: private avoids the public+published+no-content
                # rejection — visibility isn't what this test is about.
                "visibility": "private",
                "web_published": True,
            },
            headers=_auth_headers(token),
        )
        assert create.status_code == 201
        slug = create.json()["web_slug"]

        response = client.get(f"/v1/web/shares/{slug}/token")
        assert response.status_code == 404

    def test_token_path_absent_from_openapi_schema(self, client: TestClient):
        schema = client.get("/openapi.json").json()
        assert "/v1/web/shares/{slug}/token" not in schema["paths"]


class TestFolderFileContentSync:
    """Test folder file content sync endpoints (v1.8)."""

    def _make_agent_key(self, db, share, scopes="write"):
        import hashlib as _hl
        import secrets as _sec

        raw = "tr_agent_" + _sec.token_hex(24)
        key_hash = _hl.sha256(raw.encode()).hexdigest()
        ak = models.ShareAgentKey(
            share_id=share.id,
            key_hash=key_hash,
            label="test-key",
            scopes=scopes,
            # TR-03 (#ae52ba05): creator must still be owner/member to auth.
            created_by=share.owner_user_id,
        )
        db.add(ak)
        db.commit()
        db.refresh(ak)
        return raw, ak

    @pytest.fixture
    def folder_share(self, db_session: Session, test_user: models.User) -> models.Share:
        """Create a folder share with some items."""
        share = models.Share(
            kind=models.ShareKind.FOLDER,
            path="My Folder/",
            visibility=models.ShareVisibility.PUBLIC,
            owner_user_id=test_user.id,
            web_published=True,
            web_slug="my-folder",
            web_folder_items=[
                {"path": "doc1.md", "name": "doc1.md", "type": "doc"},
                {"path": "doc2.md", "name": "doc2.md", "type": "doc"},
            ],
        )
        db_session.add(share)
        db_session.commit()
        db_session.refresh(share)
        return share

    def test_sync_folder_file_content_disabled(
        self, client: TestClient, folder_share: models.Share
    ):
        """Test that sync endpoint returns 404 when web publishing is disabled."""
        response = client.post(
            f"/v1/web/shares/{folder_share.web_slug}/files?path=doc1.md",
            json={"content": "# Test Content"},
        )
        assert response.status_code == 404

    def test_sync_folder_file_content_enabled(
        self, client: TestClient, db_session: Session, folder_share: models.Share, monkeypatch
    ):
        """Test syncing file content to folder share via valid X-Agent-Key."""
        monkeypatch.setenv("WEB_PUBLISH_DOMAIN", "docs.test.com")
        from app.core.config import get_settings

        get_settings.cache_clear()

        raw_key, _ = self._make_agent_key(db_session, folder_share, scopes="write")
        response = client.post(
            f"/v1/web/shares/{folder_share.web_slug}/files?path=doc1.md",
            json={"content": "# Document 1\n\nThis is the content."},
            headers={"X-Agent-Key": raw_key},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["path"] == "doc1.md"
        assert "message" in data

        get_settings.cache_clear()

    def test_sync_folder_file_content_stamps_hash_and_becomes_sync_visible(
        self, client: TestClient, db_session: Session, folder_share: models.Share, monkeypatch
    ):
        """#d4c851af finding 1/2: this is the plugin's actual content-push path
        (the /shares/{id} PATCH web_folder_items list never carries content).
        Before the fix the pushed item had content but no sha256/source, so it
        was invisible to GET /v1/shares/{id}/files-index (source=="sync-artifact"
        AND non-empty sha256) and unwritable through PUT /sync-write."""
        monkeypatch.setenv("WEB_PUBLISH_DOMAIN", "docs.test.com")
        from app.core.config import get_settings

        get_settings.cache_clear()

        raw_key, _ = self._make_agent_key(db_session, folder_share, scopes="write")
        content = "# Document 1\n\nThis is the content."
        response = client.post(
            f"/v1/web/shares/{folder_share.web_slug}/files?path=doc1.md",
            json={"content": content},
            headers={"X-Agent-Key": raw_key},
        )
        assert response.status_code == 200, response.text

        db_session.refresh(folder_share)
        items = folder_share.web_folder_items or []
        entry = next(i for i in items if i.get("path") == "doc1.md")
        expected_sha256 = hashlib.sha256(content.encode("utf-8")).hexdigest()
        assert entry["sha256"] == expected_sha256
        assert entry["size"] == len(content.encode("utf-8"))
        assert entry["source"] == "sync-artifact"
        assert entry["modified_at"]

        read_key, _ = self._make_agent_key(db_session, folder_share, scopes="read")
        index_resp = client.get(
            f"/v1/shares/{folder_share.id}/files-index", headers={"X-Agent-Key": read_key}
        )
        assert index_resp.status_code == 200, index_resp.text
        indexed = {item["path"]: item for item in index_resp.json()}
        assert "doc1.md" in indexed, "pushed content must be visible through the sync protocol"
        assert indexed["doc1.md"]["sha256"] == expected_sha256
        assert indexed["doc1.md"]["updated_at"]

        get_settings.cache_clear()

    def test_sync_folder_file_content_fake_bearer_returns_401(
        self, client: TestClient, folder_share: models.Share, monkeypatch
    ):
        """Regression: POST /files with fake Bearer token must return 401 (not 200)."""
        monkeypatch.setenv("WEB_PUBLISH_DOMAIN", "docs.test.com")
        from app.core.config import get_settings

        get_settings.cache_clear()

        response = client.post(
            f"/v1/web/shares/{folder_share.web_slug}/files?path=doc1.md",
            json={"content": "injected"},
            headers={"Authorization": "Bearer FAKE_TOKEN_INVALID"},
        )
        assert response.status_code == 401

        get_settings.cache_clear()

    def test_get_folder_file_content(
        self, client: TestClient, db_session: Session, test_user: models.User, monkeypatch
    ):
        """Test getting file content from folder share."""
        # Enable web publishing
        monkeypatch.setenv("WEB_PUBLISH_DOMAIN", "docs.test.com")
        from app.core.config import get_settings

        get_settings.cache_clear()

        # Create folder share with content
        share = models.Share(
            kind=models.ShareKind.FOLDER,
            path="Content Folder/",
            visibility=models.ShareVisibility.PUBLIC,
            owner_user_id=test_user.id,
            web_published=True,
            web_slug="content-folder",
            web_folder_items=[
                {
                    "path": "test.md",
                    "name": "test.md",
                    "type": "doc",
                    "content": "# Test\n\nContent here",
                },
            ],
        )
        db_session.add(share)
        db_session.commit()

        # Get file content
        response = client.get(f"/v1/web/shares/{share.web_slug}/files?path=test.md")
        assert response.status_code == 200
        data = response.json()
        assert data["path"] == "test.md"
        assert data["content"] == "# Test\n\nContent here"

        get_settings.cache_clear()

    def test_get_folder_file_content_not_found(
        self, client: TestClient, folder_share: models.Share, monkeypatch
    ):
        """Test getting non-existent file returns 404."""
        monkeypatch.setenv("WEB_PUBLISH_DOMAIN", "docs.test.com")
        from app.core.config import get_settings

        get_settings.cache_clear()

        response = client.get(f"/v1/web/shares/{folder_share.web_slug}/files?path=nonexistent.md")
        assert response.status_code == 404

        get_settings.cache_clear()


class TestWebContentEditing:
    """Test web content editing endpoints (v1.8)."""

    @pytest.fixture
    def doc_share(self, db_session: Session, test_user: models.User) -> models.Share:
        """Create a document share."""
        share = models.Share(
            kind=models.ShareKind.DOC,
            path="Editable Doc.md",
            visibility=models.ShareVisibility.PROTECTED,
            owner_user_id=test_user.id,
            web_published=True,
            web_slug="editable-doc",
            web_content="# Original Content",
            password_hash=get_password_hash("edit123"),
        )
        db_session.add(share)
        db_session.commit()
        db_session.refresh(share)
        return share

    def test_update_content_disabled(self, client: TestClient, doc_share: models.Share):
        """Test that edit endpoint returns 404 when web publishing is disabled."""
        response = client.put(
            f"/v1/web/shares/{doc_share.web_slug}/content",
            json={"content": "# Updated Content"},
        )
        assert response.status_code == 404

    def test_update_content_protected_share_session_alone_is_rejected(
        self, client: TestClient, doc_share: models.Share, monkeypatch
    ):
        """TR-13 (#7d32a104): a valid web_session cookie proves only that the
        caller knows the share's VIEW password — it must never be sufficient
        for write on its own. Regression test for the exact vulnerability:
        this used to return 200."""
        monkeypatch.setenv("WEB_PUBLISH_DOMAIN", "docs.test.com")
        from app.core.config import get_settings

        get_settings.cache_clear()

        from app.services.web_session_service import WebSessionService

        session_token = WebSessionService.create_web_session(doc_share.id, hours=24)

        response = client.put(
            f"/v1/web/shares/{doc_share.web_slug}/content",
            json={"content": "# Attacker-controlled content"},
            cookies={"web_session": session_token},
        )
        assert response.status_code == 403
        body = response.json()
        # Error may be wrapped by middleware (see test_update_content_folder_share_rejected).
        detail = body.get("detail") or body.get("error", {}).get("message", "")
        assert detail == "Editor role required to edit this share"

        get_settings.cache_clear()

    def test_update_content_protected_share_owner_with_jwt_still_works(
        self, client: TestClient, db_session: Session, doc_share: models.Share, monkeypatch
    ):
        """The legitimate case must keep working: the share owner, real JWT
        session, can still edit their own protected share — with or without
        also presenting the web_session cookie."""
        monkeypatch.setenv("WEB_PUBLISH_DOMAIN", "docs.test.com")
        from app.core.config import get_settings

        get_settings.cache_clear()

        owner = db_session.get(models.User, doc_share.owner_user_id)
        login_response = client.post(
            "/auth/login", json={"email": owner.email, "password": "test123456"}
        )
        token = login_response.json()["access_token"]

        response = client.put(
            f"/v1/web/shares/{doc_share.web_slug}/content",
            json={"content": "# Updated Content\n\nNew text here."},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 200
        data = response.json()
        assert "message" in data
        assert "updated_at" in data

        get_settings.cache_clear()

    def test_update_content_protected_share_non_editor_jwt_rejected(
        self, client: TestClient, db_session: Session, doc_share: models.Share, monkeypatch
    ):
        """A real logged-in user who is neither owner nor an editor member
        must still be rejected, even with a valid JWT."""
        monkeypatch.setenv("WEB_PUBLISH_DOMAIN", "docs.test.com")
        from app.core.config import get_settings

        get_settings.cache_clear()

        stranger = models.User(
            email="stranger-tr13@example.com",
            password_hash=get_password_hash("test123456"),
            is_active=True,
        )
        db_session.add(stranger)
        db_session.commit()

        login_response = client.post(
            "/auth/login", json={"email": stranger.email, "password": "test123456"}
        )
        token = login_response.json()["access_token"]

        response = client.put(
            f"/v1/web/shares/{doc_share.web_slug}/content",
            json={"content": "# Should be rejected"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 403
        body = response.json()
        detail = body.get("detail") or body.get("error", {}).get("message", "")
        assert detail == "Editor role required to edit this share"

        get_settings.cache_clear()

    def test_update_content_without_auth(
        self, client: TestClient, doc_share: models.Share, monkeypatch
    ):
        """Test that updating content without auth fails."""
        monkeypatch.setenv("WEB_PUBLISH_DOMAIN", "docs.test.com")
        from app.core.config import get_settings

        get_settings.cache_clear()

        response = client.put(
            f"/v1/web/shares/{doc_share.web_slug}/content",
            json={"content": "# Unauthorized Edit"},
        )
        assert response.status_code == 401

        get_settings.cache_clear()

    def test_update_content_folder_share_rejected(
        self, client: TestClient, db_session: Session, test_user: models.User, monkeypatch
    ):
        """Test that folder shares cannot be edited."""
        monkeypatch.setenv("WEB_PUBLISH_DOMAIN", "docs.test.com")
        from app.core.config import get_settings

        get_settings.cache_clear()

        # Create folder share
        share = models.Share(
            kind=models.ShareKind.FOLDER,
            path="Test Folder/",
            visibility=models.ShareVisibility.PROTECTED,
            owner_user_id=test_user.id,
            web_published=True,
            web_slug="test-folder-edit",
            password_hash=get_password_hash("folder123"),
        )
        db_session.add(share)
        db_session.commit()

        # Try to update content
        from app.services.web_session_service import WebSessionService

        session_token = WebSessionService.create_web_session(share.id, hours=24)

        response = client.put(
            f"/v1/web/shares/{share.web_slug}/content",
            json={"content": "Should fail"},
            cookies={"web_session": session_token},
        )
        assert response.status_code == 400
        body = response.json()
        # Error might be wrapped by middleware
        detail = body.get("detail", "")
        if not detail and "error" in body:
            detail = body["error"].get("message", "")
        assert "document" in detail.lower()

        get_settings.cache_clear()

    def test_update_content_private_share_with_jwt(
        self, client: TestClient, db_session: Session, test_user: models.User, monkeypatch
    ):
        """Test updating private share content with JWT token."""
        monkeypatch.setenv("WEB_PUBLISH_DOMAIN", "docs.test.com")
        from app.core.config import get_settings

        get_settings.cache_clear()

        # Create private document share
        share = models.Share(
            kind=models.ShareKind.DOC,
            path="Private Doc.md",
            visibility=models.ShareVisibility.PRIVATE,
            owner_user_id=test_user.id,
            web_published=True,
            web_slug="private-editable-doc",
            web_content="# Original Private Content",
        )
        db_session.add(share)
        db_session.commit()

        # Login to get JWT token
        login_response = client.post(
            "/auth/login", json={"email": test_user.email, "password": "test123456"}
        )
        token = login_response.json()["access_token"]

        # Update content with JWT
        response = client.put(
            f"/v1/web/shares/{share.web_slug}/content",
            json={"content": "# Updated Private Content"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 200

        get_settings.cache_clear()


def _make_agent_key(
    db: Session, share: models.Share, scopes: str = "write"
) -> tuple[str, models.ShareAgentKey]:
    raw_key = "tr_agent_" + secrets.token_hex(24)
    key_hash = hashlib.sha256(raw_key.encode()).hexdigest()
    ak = models.ShareAgentKey(
        share_id=share.id,
        key_hash=key_hash,
        label="test key",
        scopes=scopes,
        # TR-03 (#ae52ba05): creator must still be owner/member to auth.
        created_by=share.owner_user_id,
    )
    db.add(ak)
    db.commit()
    db.refresh(ak)
    return raw_key, ak


def _web_enabled_settings(extra: dict | None = None):
    base = {
        "web_publish_enabled": True,
        "web_publish_domain": "docs.example.com",
        "relay_token_ttl_minutes": 30,
        "relay_public_url": "wss://relay.example.com",
        "web_frame_ancestors": None,
        "agent_key_lenient_read_grace": True,
    }
    if extra:
        base.update(extra)
    return type("Settings", (), base)()


class TestPrivateShareWebAuthMetadata:
    """PRIVATE share web access: metadata content gating."""

    @pytest.fixture
    def private_share_with_doc(self, db_session: Session, test_user: models.User) -> models.Share:
        share = models.Share(
            kind=models.ShareKind.DOC,
            path="Private/Doc.md",
            visibility=models.ShareVisibility.PRIVATE,
            owner_user_id=test_user.id,
            web_published=True,
            web_slug="private-relay-doc",
            web_content="# Secret content",
        )
        db_session.add(share)
        db_session.commit()
        db_session.refresh(share)
        return share

    def test_metadata_private_no_auth_strips_content(
        self,
        client: TestClient,
        private_share_with_doc: models.Share,
        monkeypatch,
    ):
        monkeypatch.setattr("app.api.routers.web.get_settings", lambda: _web_enabled_settings())
        response = client.get(f"/v1/web/shares/{private_share_with_doc.web_slug}")
        assert response.status_code == 200
        data = response.json()
        assert data["visibility"] == "private"
        assert data["web_content"] is None

    def test_metadata_private_with_agent_key_returns_full_content(
        self,
        client: TestClient,
        private_share_with_doc: models.Share,
        db_session: Session,
        monkeypatch,
    ):
        monkeypatch.setattr("app.api.routers.web.get_settings", lambda: _web_enabled_settings())
        raw_key, _ = _make_agent_key(db_session, private_share_with_doc)
        response = client.get(
            f"/v1/web/shares/{private_share_with_doc.web_slug}",
            headers={"X-Agent-Key": raw_key},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["web_content"] == "# Secret content"

    def test_metadata_private_agent_key_query_param_is_ignored(
        self,
        client: TestClient,
        private_share_with_doc: models.Share,
        db_session: Session,
        monkeypatch,
    ):
        """?agent_key= query param no longer works (TR-14) — falls back to the
        no-credentials masking path (200 stripped), not full content."""
        monkeypatch.setattr("app.api.routers.web.get_settings", lambda: _web_enabled_settings())
        raw_key, _ = _make_agent_key(db_session, private_share_with_doc)
        response = client.get(
            f"/v1/web/shares/{private_share_with_doc.web_slug}?agent_key={raw_key}",
        )
        assert response.status_code == 200
        data = response.json()
        assert data["web_content"] is None

    def test_metadata_private_with_auth_adds_frame_ancestors_header(
        self,
        client: TestClient,
        private_share_with_doc: models.Share,
        db_session: Session,
        monkeypatch,
    ):
        """Retargeted from the removed /token endpoint (#edfd1dd3) — the metadata
        endpoint runs the same _require_private_web_auth + _private_embed_headers
        path on successful PRIVATE auth, so this is equivalent coverage."""
        monkeypatch.setattr(
            "app.api.routers.web.get_settings",
            lambda: _web_enabled_settings(
                {"web_frame_ancestors": "https://mesh.entire.host https://dev.mesh.entire.host"}
            ),
        )
        raw_key, _ = _make_agent_key(db_session, private_share_with_doc)
        response = client.get(
            f"/v1/web/shares/{private_share_with_doc.web_slug}",
            headers={"X-Agent-Key": raw_key},
        )
        assert response.status_code == 200
        csp = response.headers.get("content-security-policy", "")
        assert "frame-ancestors" in csp
        assert "https://mesh.entire.host" in csp

    def test_metadata_private_no_frame_ancestors_when_not_configured(
        self,
        client: TestClient,
        private_share_with_doc: models.Share,
        db_session: Session,
        monkeypatch,
    ):
        """Retargeted from the removed /token endpoint (#edfd1dd3) — see the
        sibling test above for why the metadata endpoint is equivalent coverage."""
        monkeypatch.setattr("app.api.routers.web.get_settings", lambda: _web_enabled_settings())
        raw_key, _ = _make_agent_key(db_session, private_share_with_doc)
        response = client.get(
            f"/v1/web/shares/{private_share_with_doc.web_slug}",
            headers={"X-Agent-Key": raw_key},
        )
        assert response.status_code == 200
        assert "content-security-policy" not in response.headers


class TestPrivateShareWebAuth:
    """Tests for PRIVATE share web access auth gate (s2).

    Covers:
      - /v1/web/shares/{slug}/files  (folder file content)
    Must:
      - Return 401 when PRIVATE share + no auth
      - Accept Bearer JWT (owner or member)
      - Accept X-Agent-Key with read or write scope

    (Formerly also covered /v1/web/shares/{slug}/token — removed along with
    that endpoint, #edfd1dd3. The CSP frame-ancestors coverage that used to
    live here was retargeted to the metadata endpoint in
    TestPrivateShareWebAuthMetadata, which runs the same auth+header code
    path.)
    """

    # ── shared helpers ──────────────────────────────────────────────────────

    def _mock_settings(self, extra: dict | None = None):
        fields = {
            "web_publish_enabled": True,
            "web_publish_domain": "docs.test.com",
            "relay_token_ttl_minutes": 30,
            "relay_public_url": "wss://relay.test",
            "web_frame_ancestors": None,
            "agent_key_lenient_read_grace": True,
        }
        if extra:
            fields.update(extra)
        return type("S", (), fields)()

    def _login(self, client, user):
        r = client.post("/auth/login", json={"email": user.email, "password": "test123456"})
        assert r.status_code == 200, r.text
        return r.json()["access_token"]

    def _make_agent_key(self, db, share, scopes="write"):
        import hashlib as _hl
        import secrets as _sec

        raw = "tr_agent_" + _sec.token_hex(24)
        key_hash = _hl.sha256(raw.encode()).hexdigest()
        ak = models.ShareAgentKey(
            share_id=share.id,
            key_hash=key_hash,
            label="test-key",
            scopes=scopes,
            # TR-03 (#ae52ba05): creator must still be owner/member to auth.
            created_by=share.owner_user_id,
        )
        db.add(ak)
        db.commit()
        db.refresh(ak)
        return raw, ak

    # ── fixtures ────────────────────────────────────────────────────────────

    @pytest.fixture
    def private_folder_share(self, db_session: Session, test_user: models.User) -> models.Share:
        share = models.Share(
            kind=models.ShareKind.FOLDER,
            path="Private/Folder/",
            visibility=models.ShareVisibility.PRIVATE,
            owner_user_id=test_user.id,
            web_published=True,
            web_slug="private-folder",
            web_folder_items=[
                {"path": "note.md", "name": "note.md", "type": "doc", "content": "hello"},
            ],
        )
        db_session.add(share)
        db_session.commit()
        db_session.refresh(share)
        return share

    # ── /files endpoint tests ───────────────────────────────────────────────

    def test_private_files_no_auth_returns_401(
        self,
        client: TestClient,
        private_folder_share: models.Share,
        monkeypatch,
    ):
        """PRIVATE folder + no credentials → 401 on /files endpoint."""
        monkeypatch.setattr("app.api.routers.web.get_settings", lambda: self._mock_settings())
        r = client.get(
            f"/v1/web/shares/{private_folder_share.web_slug}/files",
            params={"path": "note.md"},
        )
        assert r.status_code == 401

    def test_private_files_with_bearer_owner_returns_200(
        self,
        client: TestClient,
        private_folder_share: models.Share,
        test_user: models.User,
        monkeypatch,
    ):
        """PRIVATE folder + valid Bearer JWT → 200 with file content."""
        monkeypatch.setattr("app.api.routers.web.get_settings", lambda: self._mock_settings())
        token = self._login(client, test_user)
        r = client.get(
            f"/v1/web/shares/{private_folder_share.web_slug}/files",
            params={"path": "note.md"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 200
        assert r.json()["content"] == "hello"

    def test_private_files_with_agent_key_returns_200(
        self,
        client: TestClient,
        db_session: Session,
        private_folder_share: models.Share,
        monkeypatch,
    ):
        """PRIVATE folder + valid X-Agent-Key → 200."""
        monkeypatch.setattr("app.api.routers.web.get_settings", lambda: self._mock_settings())
        raw_key, _ = self._make_agent_key(db_session, private_folder_share, scopes="read")
        r = client.get(
            f"/v1/web/shares/{private_folder_share.web_slug}/files",
            params={"path": "note.md"},
            headers={"X-Agent-Key": raw_key},
        )
        assert r.status_code == 200


class TestFolderItemsContentMerge:
    """PATCH /v1/shares/{id} with web_folder_items must MERGE, not REPLACE (AC1–AC3)."""

    def _login(self, client: TestClient, user: models.User) -> str:
        r = client.post("/auth/login", json={"email": user.email, "password": "test123456"})
        assert r.status_code == 200
        return r.json()["access_token"]

    @pytest.fixture
    def folder_share(self, db_session: Session, test_user: models.User) -> models.Share:
        share = models.Share(
            kind=models.ShareKind.FOLDER,
            path="Vault/",
            # TR-39 guard: PRIVATE — this fixture is about web_folder_items merge
            # mechanics (AC1-AC3 below), not visibility. test_patch_new_path_has_no_content
            # deliberately leaves the share with zero content items after its PATCH,
            # which the public-content guard would otherwise (correctly) reject.
            visibility=models.ShareVisibility.PRIVATE,
            owner_user_id=test_user.id,
            web_published=True,
            web_slug="content-merge-share",
            web_folder_items=[
                {
                    "path": "keep.md",
                    "name": "keep.md",
                    "type": "doc",
                    "content": "# Original",
                    "storage_key": "web-assets/keep-key",
                    "sha256": "abc123",
                    "size": 10,
                    "modified_at": "2026-08-19T12:00:00+00:00",
                    "source": "sync-artifact",
                },
                {
                    "path": "remove.md",
                    "name": "remove.md",
                    "type": "doc",
                    "content": "# Goes away",
                },
            ],
        )
        db_session.add(share)
        db_session.commit()
        db_session.refresh(share)
        return share

    def test_patch_preserves_content_on_existing_path(
        self,
        client: TestClient,
        db_session: Session,
        folder_share: models.Share,
        test_user: models.User,
    ):
        """AC1: PATCH with web_folder_items does not erase content/storage_key/sha256/
        size/modified_at/source. The last two were dropped by this merge (#d4c851af
        finding 2) until sync_folder_file_content started stamping them: a routine
        nav-tree-only PATCH — WebSyncManager fires one on every create/rename/delete —
        would otherwise silently re-empty files-index's `updated_at` on the very next
        sync, without touching sha256, so the symptom would reappear a moment after
        being fixed."""
        token = self._login(client, test_user)

        r = client.patch(
            f"/v1/shares/{folder_share.id}",
            json={
                "web_folder_items": [
                    {"path": "keep.md", "name": "keep.md", "type": "doc"},
                ]
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 200

        db_session.refresh(folder_share)
        items = {i["path"]: i for i in (folder_share.web_folder_items or [])}

        assert "keep.md" in items
        assert items["keep.md"].get("content") == "# Original"
        assert items["keep.md"].get("storage_key") == "web-assets/keep-key"
        assert items["keep.md"].get("sha256") == "abc123"
        assert items["keep.md"].get("size") == 10
        assert items["keep.md"].get("modified_at") == "2026-08-19T12:00:00+00:00"
        assert items["keep.md"].get("source") == "sync-artifact"

    def test_patch_new_path_has_no_content(
        self,
        client: TestClient,
        db_session: Session,
        folder_share: models.Share,
        test_user: models.User,
    ):
        """AC2: New paths added via PATCH arrive without content."""
        token = self._login(client, test_user)

        r = client.patch(
            f"/v1/shares/{folder_share.id}",
            json={
                "web_folder_items": [
                    {"path": "new.md", "name": "new.md", "type": "doc"},
                ]
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 200

        db_session.refresh(folder_share)
        items = {i["path"]: i for i in (folder_share.web_folder_items or [])}

        assert "new.md" in items
        assert "content" not in items["new.md"]

    def test_patch_omitted_path_is_removed(
        self,
        client: TestClient,
        db_session: Session,
        folder_share: models.Share,
        test_user: models.User,
    ):
        """AC3: Paths absent from the new payload are removed from web_folder_items."""
        token = self._login(client, test_user)

        r = client.patch(
            f"/v1/shares/{folder_share.id}",
            json={
                "web_folder_items": [
                    {"path": "keep.md", "name": "keep.md", "type": "doc"},
                ]
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 200

        db_session.refresh(folder_share)
        paths = {i["path"] for i in (folder_share.web_folder_items or [])}

        assert "remove.md" not in paths
        assert "keep.md" in paths
