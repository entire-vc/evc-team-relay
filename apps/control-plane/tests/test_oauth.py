"""Tests for OAuth/OIDC authentication."""

from __future__ import annotations

import uuid
from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.db import models
from app.services import oauth_service


class TestOAuthService:
    """Tests for OAuth service functions."""

    def test_generate_code_verifier(self):
        """Test PKCE code verifier generation."""
        verifier = oauth_service.generate_code_verifier()
        assert len(verifier) >= 43
        assert len(verifier) <= 128
        # Should be URL-safe base64
        assert all(c.isalnum() or c in "-_" for c in verifier)

    def test_generate_code_challenge(self):
        """Test PKCE code challenge generation."""
        verifier = "test_verifier_123"
        challenge = oauth_service.generate_code_challenge(verifier)
        assert len(challenge) > 0
        # Should be URL-safe base64
        assert all(c.isalnum() or c in "-_" for c in challenge)

    def test_encode_decode_state(self):
        """Test state encoding and decoding."""
        from app.schemas.oauth import OAuthStateData

        state_data = OAuthStateData(
            code_verifier="test_verifier",
            redirect_uri="https://example.com/callback",
        )
        encoded = oauth_service.encode_state(state_data)
        decoded = oauth_service.decode_state(encoded)

        assert decoded.code_verifier == state_data.code_verifier
        assert decoded.redirect_uri == state_data.redirect_uri

    def test_decode_invalid_state(self):
        """Test decoding invalid state raises exception."""
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc_info:
            oauth_service.decode_state("invalid_base64")

        assert exc_info.value.status_code == 400
        assert "Invalid state parameter" in exc_info.value.detail

    def test_get_oauth_providers_from_env(self, db_session: Session):
        """Test getting OAuth provider from environment variables."""
        with patch("app.services.oauth_service.get_settings") as mock_settings:
            mock_settings.return_value = MagicMock(
                oauth_enabled=True,
                oauth_provider_name="casdoor",
                oauth_issuer_url="https://casdoor.example.com",
                oauth_client_id="test_client_id",
                oauth_client_secret="test_client_secret",
                oauth_auto_register=True,
            )

            providers = oauth_service.get_oauth_providers(db_session)
            assert len(providers) == 1
            assert providers[0].name == "casdoor"
            assert providers[0].issuer_url == "https://casdoor.example.com"

    def test_get_oauth_provider_not_found(self, db_session: Session):
        """Test getting non-existent OAuth provider raises exception."""
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc_info:
            oauth_service.get_oauth_provider(db_session, "nonexistent")

        assert exc_info.value.status_code == 404
        assert "not found or not enabled" in exc_info.value.detail

    def test_generate_authorize_url(self, db_session: Session):
        """Test OAuth authorize URL generation."""
        provider = models.OAuthProvider(
            id=uuid.uuid4(),
            name="casdoor",
            provider_type=models.OAuthProviderType.OIDC,
            issuer_url="https://casdoor.example.com",
            client_id="test_client_id",
            client_secret_encrypted="test_secret",
            enabled=True,
            auto_register=True,
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
        )

        redirect_uri = "https://cp.example.com/callback"
        authorize_url, state_token = oauth_service.generate_authorize_url(provider, redirect_uri)

        assert "https://casdoor.example.com/login/oauth/authorize" in authorize_url
        assert "client_id=test_client_id" in authorize_url
        assert "redirect_uri=" in authorize_url
        assert "code_challenge=" in authorize_url
        assert "code_challenge_method=S256" in authorize_url
        assert state_token

    def test_find_user_by_oauth(self, db_session: Session):
        """Test finding user by OAuth account."""
        # Create user
        user = models.User(
            id=uuid.uuid4(),
            email="test@example.com",
            password_hash="hash",
            is_admin=False,
            is_active=True,
        )
        db_session.add(user)

        # Create OAuth provider
        provider = models.OAuthProvider(
            id=uuid.uuid4(),
            name="casdoor",
            provider_type=models.OAuthProviderType.OIDC,
            issuer_url="https://casdoor.example.com",
            client_id="test_client",
            client_secret_encrypted="secret",
            enabled=True,
            auto_register=True,
        )
        db_session.add(provider)
        db_session.flush()

        # Create OAuth account
        oauth_account = models.UserOAuthAccount(
            id=uuid.uuid4(),
            user_id=user.id,
            provider_id=provider.id,
            provider_user_id="oauth_user_123",
            email="test@example.com",
        )
        db_session.add(oauth_account)
        db_session.commit()

        # Find user by OAuth
        found_user = oauth_service.find_user_by_oauth(db_session, provider.id, "oauth_user_123")
        assert found_user is not None
        assert found_user.id == user.id

    def test_create_user_from_oauth(self, db_session: Session):
        """Test creating user from OAuth profile."""
        # Create OAuth provider
        provider = models.OAuthProvider(
            id=uuid.uuid4(),
            name="casdoor",
            provider_type=models.OAuthProviderType.OIDC,
            issuer_url="https://casdoor.example.com",
            client_id="test_client",
            client_secret_encrypted="secret",
            enabled=True,
            auto_register=True,
        )
        db_session.add(provider)
        db_session.commit()

        # Create user from OAuth
        user = oauth_service.create_user_from_oauth(
            db_session,
            email="newuser@example.com",
            name="New User",
            provider_id=provider.id,
            provider_user_id="oauth_user_456",
            picture_url="https://example.com/pic.jpg",
        )

        assert user.email == "newuser@example.com"
        assert user.is_active is True
        assert user.password_hash == ""  # No password for OAuth-only accounts

        # Verify OAuth account was created
        oauth_account = oauth_service.find_user_by_oauth(db_session, provider.id, "oauth_user_456")
        assert oauth_account is not None

    def test_link_oauth_account(self, db_session: Session):
        """Test linking OAuth account to existing user."""
        # Create user
        user = models.User(
            id=uuid.uuid4(),
            email="existing@example.com",
            password_hash="hash",
            is_admin=False,
            is_active=True,
        )
        db_session.add(user)

        # Create OAuth provider
        provider = models.OAuthProvider(
            id=uuid.uuid4(),
            name="casdoor",
            provider_type=models.OAuthProviderType.OIDC,
            issuer_url="https://casdoor.example.com",
            client_id="test_client",
            client_secret_encrypted="secret",
            enabled=True,
            auto_register=True,
        )
        db_session.add(provider)
        db_session.commit()

        # Link OAuth account
        oauth_account = oauth_service.link_oauth_account(
            db_session,
            user_id=user.id,
            provider_id=provider.id,
            provider_user_id="oauth_user_789",
            email="existing@example.com",
            name="Existing User",
        )

        assert oauth_account.user_id == user.id
        assert oauth_account.provider_id == provider.id
        assert oauth_account.provider_user_id == "oauth_user_789"

    def test_link_oauth_account_already_linked_to_different_user(self, db_session: Session):
        """Test linking OAuth account that's already linked to another user raises exception."""
        from fastapi import HTTPException

        # Create two users
        user1 = models.User(
            id=uuid.uuid4(),
            email="user1@example.com",
            password_hash="hash",
            is_admin=False,
            is_active=True,
        )
        user2 = models.User(
            id=uuid.uuid4(),
            email="user2@example.com",
            password_hash="hash",
            is_admin=False,
            is_active=True,
        )
        db_session.add_all([user1, user2])

        # Create OAuth provider
        provider = models.OAuthProvider(
            id=uuid.uuid4(),
            name="casdoor",
            provider_type=models.OAuthProviderType.OIDC,
            issuer_url="https://casdoor.example.com",
            client_id="test_client",
            client_secret_encrypted="secret",
            enabled=True,
            auto_register=True,
        )
        db_session.add(provider)
        db_session.flush()

        # Link OAuth account to user1
        oauth_account = models.UserOAuthAccount(
            id=uuid.uuid4(),
            user_id=user1.id,
            provider_id=provider.id,
            provider_user_id="oauth_user_999",
            email="user1@example.com",
        )
        db_session.add(oauth_account)
        db_session.commit()

        # Try to link same OAuth account to user2
        with pytest.raises(HTTPException) as exc_info:
            oauth_service.link_oauth_account(
                db_session,
                user_id=user2.id,
                provider_id=provider.id,
                provider_user_id="oauth_user_999",
                email="user2@example.com",
            )

        assert exc_info.value.status_code == 409
        assert "already linked to another user" in exc_info.value.detail


class TestOAuthEndpoints:
    """Tests for OAuth API endpoints."""

    def test_list_providers_when_none_configured(self, client: TestClient):
        """Test listing OAuth providers when none are configured."""
        response = client.get("/v1/auth/oauth/providers")
        assert response.status_code == 200
        assert response.json() == []

    def test_list_providers_with_env_config(self, client: TestClient):
        """Test listing OAuth providers configured via environment."""
        with patch("app.services.oauth_service.get_settings") as mock_settings:
            mock_settings.return_value = MagicMock(
                oauth_enabled=True,
                oauth_provider_name="casdoor",
                oauth_issuer_url="https://casdoor.example.com",
                oauth_client_id="test_client_id",
                oauth_client_secret="test_secret",
                oauth_auto_register=True,
                relay_public_url="https://relay.example.com",
            )

            response = client.get("/v1/auth/oauth/providers")
            assert response.status_code == 200
            data = response.json()
            assert len(data) == 1
            assert data[0]["name"] == "casdoor"
            assert data[0]["display_name"] == "Casdoor"

    def test_authorize_redirect(self, client: TestClient, db_session: Session):
        """Test OAuth authorize endpoint redirects to provider."""
        # Create OAuth provider
        provider = models.OAuthProvider(
            id=uuid.uuid4(),
            name="casdoor",
            provider_type=models.OAuthProviderType.OIDC,
            issuer_url="https://casdoor.example.com",
            client_id="test_client_id",
            client_secret_encrypted="test_secret",
            enabled=True,
            auto_register=True,
        )
        db_session.add(provider)
        db_session.commit()

        response = client.get(
            "/v1/auth/oauth/casdoor/authorize",
            params={"redirect_uri": "https://cp.example.com/callback"},
            follow_redirects=False,
        )

        assert response.status_code == 302
        location = response.headers["location"]
        assert "casdoor.example.com/login/oauth/authorize" in location
        assert "client_id=test_client_id" in location
        assert "code_challenge=" in location

    def test_authorize_provider_not_found(self, client: TestClient):
        """Test OAuth authorize with non-existent provider returns 404."""
        response = client.get(
            "/v1/auth/oauth/nonexistent/authorize",
            params={"redirect_uri": "https://cp.example.com/callback"},
        )

        assert response.status_code == 404
        response_data = response.json()
        # Check if detail exists in response (can be nested in 'message' or 'detail')
        assert "not found or not enabled" in (
            response_data.get("detail", "")
            or response_data.get("message", "")
            or str(response_data)
        )

    def test_callback_with_invalid_state(self, client: TestClient):
        """Test OAuth callback with invalid state parameter."""
        response = client.get(
            "/v1/auth/oauth/casdoor/callback",
            params={
                "code": "test_code",
                "state": "invalid_state",
            },
        )

        assert response.status_code == 400
        response_data = response.json()
        # Check if detail exists in response (can be nested in 'message' or 'detail')
        assert "Invalid state parameter" in (
            response_data.get("detail", "")
            or response_data.get("message", "")
            or str(response_data)
        )

    def test_authorize_json_response(self, client: TestClient, db_session: Session):
        """Test OAuth authorize endpoint returns JSON when Accept header is set."""
        # Create OAuth provider
        provider = models.OAuthProvider(
            id=uuid.uuid4(),
            name="casdoor",
            provider_type=models.OAuthProviderType.OIDC,
            issuer_url="https://casdoor.example.com",
            client_id="test_client_id",
            client_secret_encrypted="test_secret",
            enabled=True,
            auto_register=True,
        )
        db_session.add(provider)
        db_session.commit()

        response = client.get(
            "/v1/auth/oauth/casdoor/authorize",
            params={"redirect_uri": "https://cp.example.com/callback"},
            headers={"Accept": "application/json"},
        )

        assert response.status_code == 200
        data = response.json()
        assert "authorize_url" in data
        assert "state" in data
        assert "casdoor.example.com/login/oauth/authorize" in data["authorize_url"]

    def test_authorize_localhost_stays_http(self, client: TestClient, db_session):
        """Test that localhost/127.0.0.1 redirect URIs are not converted to HTTPS."""
        # Create OAuth provider
        provider = models.OAuthProvider(
            id=uuid.uuid4(),
            name="casdoor",
            provider_type=models.OAuthProviderType.OIDC,
            issuer_url="https://casdoor.example.com",
            client_id="test_client_id",
            client_secret_encrypted="test_secret",
            enabled=True,
            auto_register=True,
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
        )
        db_session.add(provider)
        db_session.commit()

        # Test with 127.0.0.1
        response = client.get(
            "/v1/auth/oauth/casdoor/authorize",
            params={"redirect_uri": "http://127.0.0.1:58548/callback"},
            headers={
                "Accept": "application/json",
                "X-Forwarded-Proto": "https",  # Simulate HTTPS proxy
            },
        )
        assert response.status_code == 200
        data = response.json()
        # State should contain the original HTTP redirect_uri
        state_data = oauth_service.decode_state(data["state"])
        assert state_data.redirect_uri == "http://127.0.0.1:58548/callback"

        # Test with localhost
        response = client.get(
            "/v1/auth/oauth/casdoor/authorize",
            params={"redirect_uri": "http://localhost:58548/callback"},
            headers={
                "Accept": "application/json",
                "X-Forwarded-Proto": "https",  # Simulate HTTPS proxy
            },
        )
        assert response.status_code == 200
        data = response.json()
        state_data = oauth_service.decode_state(data["state"])
        assert state_data.redirect_uri == "http://localhost:58548/callback"


class TestOAuthGroupMapping:
    """Tests for OAuth group mapping and user sync."""

    def test_should_be_admin_with_matching_group(self):
        """Test user is admin when in configured admin group."""
        with patch("app.services.oauth_service.get_settings") as mock_settings:
            mock_settings.return_value = MagicMock(
                oauth_admin_groups="admins,superusers",
            )

            # User in admins group should be admin
            assert oauth_service.should_be_admin(["admins"]) is True
            assert oauth_service.should_be_admin(["superusers"]) is True
            assert oauth_service.should_be_admin(["Admins"]) is True  # Case insensitive
            assert oauth_service.should_be_admin(["users", "admins"]) is True

    def test_should_be_admin_without_matching_group(self):
        """Test user is not admin when not in configured admin groups."""
        with patch("app.services.oauth_service.get_settings") as mock_settings:
            mock_settings.return_value = MagicMock(
                oauth_admin_groups="admins,superusers",
            )

            assert oauth_service.should_be_admin(["users"]) is False
            assert oauth_service.should_be_admin(["developers"]) is False
            assert oauth_service.should_be_admin([]) is False

    def test_should_be_admin_when_no_groups_configured(self):
        """Test user is not admin when no admin groups are configured."""
        with patch("app.services.oauth_service.get_settings") as mock_settings:
            mock_settings.return_value = MagicMock(
                oauth_admin_groups=None,
            )

            assert oauth_service.should_be_admin(["admins"]) is False
            assert oauth_service.should_be_admin([]) is False

    def test_get_default_admin_status_user(self):
        """Test default role is user."""
        with patch("app.services.oauth_service.get_settings") as mock_settings:
            mock_settings.return_value = MagicMock(
                oauth_default_role="user",
            )

            assert oauth_service.get_default_admin_status() is False

    def test_get_default_admin_status_admin(self):
        """Test default role is admin."""
        with patch("app.services.oauth_service.get_settings") as mock_settings:
            mock_settings.return_value = MagicMock(
                oauth_default_role="admin",
            )

            assert oauth_service.get_default_admin_status() is True

    def test_create_user_with_admin_group(self, db_session: Session):
        """Test creating user from OAuth with admin group assigns admin role."""
        # Create OAuth provider
        provider = models.OAuthProvider(
            id=uuid.uuid4(),
            name="casdoor",
            provider_type=models.OAuthProviderType.OIDC,
            issuer_url="https://casdoor.example.com",
            client_id="test_client",
            client_secret_encrypted="secret",
            enabled=True,
            auto_register=True,
        )
        db_session.add(provider)
        db_session.commit()

        with patch("app.services.oauth_service.get_settings") as mock_settings:
            mock_settings.return_value = MagicMock(
                oauth_admin_groups="admins,superusers",
                oauth_default_role="user",
            )

            user = oauth_service.create_user_from_oauth(
                db_session,
                email="admin@example.com",
                name="Admin User",
                provider_id=provider.id,
                provider_user_id="oauth_admin_1",
                groups=["admins"],
            )

            assert user.is_admin is True

    def test_create_user_without_admin_group(self, db_session: Session):
        """Test creating user from OAuth without admin group assigns user role."""
        # Create OAuth provider
        provider = models.OAuthProvider(
            id=uuid.uuid4(),
            name="casdoor",
            provider_type=models.OAuthProviderType.OIDC,
            issuer_url="https://casdoor.example.com",
            client_id="test_client",
            client_secret_encrypted="secret",
            enabled=True,
            auto_register=True,
        )
        db_session.add(provider)
        db_session.commit()

        with patch("app.services.oauth_service.get_settings") as mock_settings:
            mock_settings.return_value = MagicMock(
                oauth_admin_groups="admins,superusers",
                oauth_default_role="user",
            )

            user = oauth_service.create_user_from_oauth(
                db_session,
                email="user@example.com",
                name="Regular User",
                provider_id=provider.id,
                provider_user_id="oauth_user_1",
                groups=["users"],
            )

            assert user.is_admin is False

    def test_create_user_with_default_admin_role(self, db_session: Session):
        """Test creating user from OAuth with default admin role."""
        # Create OAuth provider
        provider = models.OAuthProvider(
            id=uuid.uuid4(),
            name="casdoor",
            provider_type=models.OAuthProviderType.OIDC,
            issuer_url="https://casdoor.example.com",
            client_id="test_client",
            client_secret_encrypted="secret",
            enabled=True,
            auto_register=True,
        )
        db_session.add(provider)
        db_session.commit()

        with patch("app.services.oauth_service.get_settings") as mock_settings:
            mock_settings.return_value = MagicMock(
                oauth_admin_groups=None,  # No group mapping
                oauth_default_role="admin",  # Default to admin
            )

            user = oauth_service.create_user_from_oauth(
                db_session,
                email="default@example.com",
                name="Default Admin",
                provider_id=provider.id,
                provider_user_id="oauth_default_1",
                groups=[],
            )

            assert user.is_admin is True

    def test_sync_user_info_updates_admin_status(self, db_session: Session):
        """Test syncing user info updates admin status based on groups."""
        from app.schemas.oauth import OAuthUserInfo

        # Create user as non-admin
        user = models.User(
            id=uuid.uuid4(),
            email="sync@example.com",
            password_hash="hash",
            is_admin=False,
            is_active=True,
        )
        db_session.add(user)
        db_session.commit()

        userinfo = OAuthUserInfo(
            sub="oauth_sync_1",
            email="sync@example.com",
            name="Sync User",
            groups=["admins"],  # User is now in admins group
        )

        with patch("app.services.oauth_service.get_settings") as mock_settings:
            mock_settings.return_value = MagicMock(
                oauth_sync_user_info=True,
                oauth_admin_groups="admins",
            )

            updated = oauth_service.sync_user_info(db_session, user, userinfo)

            assert updated is True
            assert user.is_admin is True

    def test_sync_user_info_disabled(self, db_session: Session):
        """Test syncing user info is skipped when disabled."""
        from app.schemas.oauth import OAuthUserInfo

        # Create user as non-admin
        user = models.User(
            id=uuid.uuid4(),
            email="nosync@example.com",
            password_hash="hash",
            is_admin=False,
            is_active=True,
        )
        db_session.add(user)
        db_session.commit()

        userinfo = OAuthUserInfo(
            sub="oauth_nosync_1",
            email="nosync@example.com",
            name="No Sync User",
            groups=["admins"],
        )

        with patch("app.services.oauth_service.get_settings") as mock_settings:
            mock_settings.return_value = MagicMock(
                oauth_sync_user_info=False,  # Sync disabled
                oauth_admin_groups="admins",
            )

            updated = oauth_service.sync_user_info(db_session, user, userinfo)

            assert updated is False
            assert user.is_admin is False  # Unchanged
