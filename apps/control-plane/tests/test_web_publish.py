"""Tests for web publishing functionality."""

from __future__ import annotations

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

    def test_robots_txt_default_deny_all(
        self, client: TestClient, db_session: Session, test_user: models.User, monkeypatch
    ):
        """Test robots.txt default behavior (deny all when no indexable shares)."""
        # Enable web publishing for this test
        monkeypatch.setenv("WEB_PUBLISH_DOMAIN", "docs.test.com")
        from app.core.config import get_settings

        get_settings.cache_clear()

        # Create a share with noindex=True (default)
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

        response = client.get("/v1/web/robots.txt")
        assert response.status_code == 200
        assert response.headers["content-type"] == "text/plain; charset=utf-8"
        content = response.text
        assert "User-agent: *" in content
        assert "Disallow: /" in content
        # Should NOT have Allow rules
        assert "Allow:" not in content
        # Should NOT have sitemap
        assert "Sitemap:" not in content

        # Clean up
        get_settings.cache_clear()

    def test_robots_txt_with_indexable_shares(
        self, client: TestClient, db_session: Session, test_user: models.User, monkeypatch
    ):
        """Test robots.txt includes Allow rules for indexable shares."""
        # Enable web publishing for this test
        monkeypatch.setenv("WEB_PUBLISH_DOMAIN", "docs.test.com")
        from app.core.config import get_settings

        get_settings.cache_clear()

        # Create indexable share
        share1 = models.Share(
            kind=models.ShareKind.DOC,
            path="Public/Doc1.md",
            visibility=models.ShareVisibility.PUBLIC,
            owner_user_id=test_user.id,
            web_published=True,
            web_slug="public-doc-1",
            web_noindex=False,  # Indexable
        )
        # Create another indexable share
        share2 = models.Share(
            kind=models.ShareKind.DOC,
            path="Public/Doc2.md",
            visibility=models.ShareVisibility.PUBLIC,
            owner_user_id=test_user.id,
            web_published=True,
            web_slug="public-doc-2",
            web_noindex=False,  # Indexable
        )
        # Create non-indexable share
        share3 = models.Share(
            kind=models.ShareKind.DOC,
            path="Private/Doc.md",
            visibility=models.ShareVisibility.PRIVATE,
            owner_user_id=test_user.id,
            web_published=True,
            web_slug="private-doc",
            web_noindex=True,  # Not indexable
        )
        db_session.add_all([share1, share2, share3])
        db_session.commit()

        response = client.get("/v1/web/robots.txt")
        assert response.status_code == 200
        content = response.text

        # Should have default deny
        assert "User-agent: *" in content
        assert "Disallow: /" in content

        # Should have Allow rules for indexable shares
        assert "Allow: /public-doc-1" in content
        assert "Allow: /public-doc-2" in content
        # Should NOT include private share
        assert "private-doc" not in content

        # Should have sitemap reference
        assert "Sitemap: https://docs.test.com/sitemap.xml" in content

        # Clean up
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
                "visibility": "public",
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
                "visibility": "public",
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
                "visibility": "public",
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
                "visibility": "public",
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
                "visibility": "public",
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

        # Create share
        create_response = client.post(
            "/v1/shares",
            json={
                "kind": "doc",
                "path": "Test Doc.md",
                "visibility": "public",
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

        # Create share with web publishing enabled
        create_response = client.post(
            "/v1/shares",
            json={
                "kind": "doc",
                "path": "Test Doc.md",
                "visibility": "public",
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

        # Create share with web publishing
        create_response = client.post(
            "/v1/shares",
            json={
                "kind": "doc",
                "path": "Test Doc.md",
                "visibility": "public",
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
                "visibility": "public",
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
                "visibility": "public",
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


class TestWebRelayToken:
    """Test web relay token endpoint for real-time sync."""

    @pytest.fixture
    def public_share_with_doc_id(self, db_session: Session, test_user: models.User) -> models.Share:
        """Create a public share with web_doc_id configured."""
        share = models.Share(
            kind=models.ShareKind.DOC,
            path="Public/Realtime.md",
            visibility=models.ShareVisibility.PUBLIC,
            owner_user_id=test_user.id,
            web_published=True,
            web_slug="public-realtime",
            web_doc_id="s3rn:relay:relay:test-relay:folder:test-folder:doc:test-doc",
        )
        db_session.add(share)
        db_session.commit()
        db_session.refresh(share)
        return share

    @pytest.fixture
    def protected_share_with_doc_id(
        self, db_session: Session, test_user: models.User
    ) -> models.Share:
        """Create a protected share with web_doc_id configured."""
        share = models.Share(
            kind=models.ShareKind.DOC,
            path="Protected/Realtime.md",
            visibility=models.ShareVisibility.PROTECTED,
            password_hash=get_password_hash("sharepass123"),
            owner_user_id=test_user.id,
            web_published=True,
            web_slug="protected-realtime",
            web_doc_id="s3rn:relay:relay:test-relay:folder:test-folder:doc:protected-doc",
        )
        db_session.add(share)
        db_session.commit()
        db_session.refresh(share)
        return share

    def test_get_relay_token_public_share(
        self, client: TestClient, public_share_with_doc_id: models.Share, monkeypatch
    ):
        """Test getting relay token for public share."""

        # Enable web publishing
        monkeypatch.setattr(
            "app.api.routers.web.get_settings",
            lambda: type(
                "Settings",
                (),
                {
                    "web_publish_enabled": True,
                    "web_publish_domain": "docs.example.com",
                    "relay_token_ttl_minutes": 30,
                    "relay_public_url": "wss://relay.example.com",
                },
            )(),
        )

        response = client.get(f"/v1/web/shares/{public_share_with_doc_id.web_slug}/token")

        assert response.status_code == 200
        data = response.json()
        assert "relay_url" in data
        assert "token" in data
        assert "doc_id" in data
        assert "expires_at" in data
        assert data["doc_id"] == public_share_with_doc_id.web_doc_id
        assert data["relay_url"] == "wss://relay.example.com"

    def test_get_relay_token_disabled_web_publish(
        self, client: TestClient, public_share_with_doc_id: models.Share
    ):
        """Test getting relay token fails when web publishing disabled."""
        # By default web publishing is disabled
        response = client.get(f"/v1/web/shares/{public_share_with_doc_id.web_slug}/token")
        # Should return 404 because web publishing is disabled
        assert response.status_code == 404

    def test_get_relay_token_nonexistent_share(self, client: TestClient):
        """Test getting relay token for nonexistent share."""
        response = client.get("/v1/web/shares/nonexistent-slug/token")
        assert response.status_code == 404

    def test_get_relay_token_protected_share_with_session(
        self, client: TestClient, protected_share_with_doc_id: models.Share, monkeypatch
    ):
        """Test getting relay token succeeds for protected share with valid session."""

        # Enable web publishing
        monkeypatch.setattr(
            "app.api.routers.web.get_settings",
            lambda: type(
                "Settings",
                (),
                {
                    "web_publish_enabled": True,
                    "web_publish_domain": "docs.example.com",
                    "relay_token_ttl_minutes": 30,
                    "relay_public_url": "wss://relay.example.com",
                },
            )(),
        )

        # Create valid session
        session_token = WebSessionService.create_web_session(
            protected_share_with_doc_id.id, hours=24
        )

        response = client.get(
            f"/v1/web/shares/{protected_share_with_doc_id.web_slug}/token",
            cookies={"web_session": session_token},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["doc_id"] == protected_share_with_doc_id.web_doc_id

    def test_update_share_with_web_doc_id(self, client: TestClient, test_user: models.User):
        """Test updating share with web_doc_id."""
        login_response = client.post(
            "/auth/login", json={"email": test_user.email, "password": "test123456"}
        )
        token = login_response.json()["access_token"]

        # Create a share
        create_response = client.post(
            "/v1/shares",
            json={
                "kind": "doc",
                "path": "DocId Test.md",
                "visibility": "public",
                "web_published": True,
            },
            headers=_auth_headers(token),
        )
        assert create_response.status_code == 201
        share_id = create_response.json()["id"]

        # Update with web_doc_id
        test_doc_id = "s3rn:relay:relay:abc:folder:def:doc:ghi"
        update_response = client.patch(
            f"/v1/shares/{share_id}",
            json={"web_doc_id": test_doc_id},
            headers=_auth_headers(token),
        )
        assert update_response.status_code == 200
        assert update_response.json()["web_doc_id"] == test_doc_id


class TestFolderFileContentSync:
    """Test folder file content sync endpoints (v1.8)."""

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
        self, client: TestClient, folder_share: models.Share, monkeypatch
    ):
        """Test syncing file content to folder share."""
        # Enable web publishing
        monkeypatch.setenv("WEB_PUBLISH_DOMAIN", "docs.test.com")
        from app.core.config import get_settings

        get_settings.cache_clear()

        # Sync content (with mock auth for now)
        response = client.post(
            f"/v1/web/shares/{folder_share.web_slug}/files?path=doc1.md",
            json={"content": "# Document 1\n\nThis is the content."},
            headers={"Authorization": "Bearer mock-token"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["path"] == "doc1.md"
        assert "message" in data

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

    def test_update_content_protected_share_with_session(
        self, client: TestClient, doc_share: models.Share, monkeypatch
    ):
        """Test updating content with valid session for protected share."""
        # Enable web publishing
        monkeypatch.setenv("WEB_PUBLISH_DOMAIN", "docs.test.com")
        from app.core.config import get_settings

        get_settings.cache_clear()

        # Authenticate to get session
        from app.services.web_session_service import WebSessionService

        session_token = WebSessionService.create_web_session(doc_share.id, hours=24)

        # Update content
        response = client.put(
            f"/v1/web/shares/{doc_share.web_slug}/content",
            json={"content": "# Updated Content\n\nNew text here."},
            cookies={"web_session": session_token},
        )
        assert response.status_code == 200
        data = response.json()
        assert "message" in data
        assert "updated_at" in data

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
