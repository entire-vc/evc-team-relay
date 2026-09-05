"""Tests for OAuth/OIDC authentication."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import jwt as pyjwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
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

    def test_create_user_from_oauth_registers_argus_with_casdoor_id(self, db_session: Session):
        """create_user_from_oauth fires register_product_user with casdoor_id + registered_at."""
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

        with patch("app.services.oauth_service.argus_service.register_product_user") as mock_reg:
            user = oauth_service.create_user_from_oauth(
                db_session,
                email="oauth@example.com",
                name="OAuth User",
                provider_id=provider.id,
                provider_user_id="casdoor-sub-xyz",
            )

        mock_reg.assert_called_once()
        call_kwargs = mock_reg.call_args.kwargs
        assert call_kwargs["email"] == "oauth@example.com"
        assert call_kwargs["casdoor_id"] == "casdoor-sub-xyz"
        assert call_kwargs["registered_at"] == user.created_at

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

    def test_REGRESSION_second_identity_does_not_overwrite_first(self, db_session: Session):
        """Security fix (#ad2de48d): link_oauth_account() used to look up the
        row to update by (user_id, provider_id) alone — a second sign-in at
        the SAME provider with a DIFFERENT subject (e.g. reached via the
        callback's find-by-email fallback for an existing user) would
        silently overwrite provider_user_id on the user's one row instead of
        creating a second one. The original subject would then no longer
        resolve to anyone — a real account-takeover shape when a new
        external identity shares an email with an existing user.

        MUST fail on the pre-fix code: sub1 would no longer be found."""
        user = models.User(
            id=uuid.uuid4(),
            email="two-identities@example.com",
            password_hash="hash",
            is_admin=False,
            is_active=True,
        )
        db_session.add(user)
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

        oauth_service.link_oauth_account(
            db_session,
            user_id=user.id,
            provider_id=provider.id,
            provider_user_id="sub1",
            email="two-identities@example.com",
        )

        # A second identity at the SAME provider, same user (the callback's
        # find_user_by_email() path resolves to this same user_id).
        oauth_service.link_oauth_account(
            db_session,
            user_id=user.id,
            provider_id=provider.id,
            provider_user_id="sub2",
            email="two-identities@example.com",
        )

        # Both identities must still resolve to this user — sub1 must NOT
        # have been overwritten/lost.
        found_via_sub1 = oauth_service.find_user_by_oauth(db_session, provider.id, "sub1")
        found_via_sub2 = oauth_service.find_user_by_oauth(db_session, provider.id, "sub2")
        assert found_via_sub1 is not None
        assert found_via_sub1.id == user.id
        assert found_via_sub2 is not None
        assert found_via_sub2.id == user.id

        rows = (
            db_session.query(models.UserOAuthAccount)
            .filter_by(user_id=user.id, provider_id=provider.id)
            .all()
        )
        assert {r.provider_user_id for r in rows} == {"sub1", "sub2"}

    def test_repeat_login_same_identity_updates_in_place_not_duplicated(self, db_session: Session):
        """Positive control: logging in again with the SAME subject must
        update that one row's profile fields, not create a second row."""
        user = models.User(
            id=uuid.uuid4(),
            email="repeat-login@example.com",
            password_hash="hash",
            is_admin=False,
            is_active=True,
        )
        db_session.add(user)
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

        oauth_service.link_oauth_account(
            db_session,
            user_id=user.id,
            provider_id=provider.id,
            provider_user_id="stable-sub",
            email="repeat-login@example.com",
            name="Old Name",
        )
        oauth_service.link_oauth_account(
            db_session,
            user_id=user.id,
            provider_id=provider.id,
            provider_user_id="stable-sub",
            email="repeat-login@example.com",
            name="New Name",
        )

        rows = (
            db_session.query(models.UserOAuthAccount)
            .filter_by(user_id=user.id, provider_id=provider.id)
            .all()
        )
        assert len(rows) == 1
        assert rows[0].name == "New Name"


class TestOAuthEndpoints:
    """Tests for OAuth API endpoints."""

    def test_list_providers_when_none_configured(self, client: TestClient):
        """Test listing OAuth providers when none are configured."""
        response = client.get("/v1/auth/oauth/providers")
        assert response.status_code == 200
        assert response.json() == []

    def test_list_providers_with_env_config(self, client: TestClient, db_session: Session):
        """Test listing OAuth providers that exist in the database."""
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

    def test_authorize_localhost_stays_http(self, client: TestClient):
        """Test that localhost/127.0.0.1 redirect URIs are not converted to HTTPS."""
        # Use mock settings so get_oauth_provider auto-creates the provider from
        # env config within the handler's own session — no cross-session DB setup.
        with patch("app.services.oauth_service.get_settings") as mock_settings:
            mock_settings.return_value = MagicMock(
                oauth_enabled=True,
                oauth_provider_name="casdoor",
                oauth_issuer_url="https://casdoor.example.com",
                oauth_client_id="test_client_id",
                oauth_client_secret="test_secret",
                oauth_auto_register=True,
                oauth_scopes="openid profile email",
                oauth_state_secret=None,
            )

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


class TestOAuthCallbackSessionNotification:
    """TR-16: OAuth login must send the security-alert email + session.created
    webhook after create_session(), same as the password-login path (auth.py)
    already does. oauth.py's callback() previously called create_session()
    directly with no notification_service call at all."""

    def _make_provider(self, db_session: Session) -> models.OAuthProvider:
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
        return provider

    def _make_global_webhook(self, db_session: Session, event_type: str) -> models.Webhook:
        webhook = models.Webhook(
            id=uuid.uuid4(),
            user_id=None,  # admin/global webhook — matches any user per find_matching_webhooks
            name="test session webhook",
            url="https://example.com/hook",
            secret="whsec_test",
            events=[event_type],
            active=True,
        )
        db_session.add(webhook)
        db_session.commit()
        return webhook

    def _run_callback(
        self, client: TestClient, db_session: Session, provider: models.OAuthProvider
    ):
        from app.schemas.oauth import OAuthStateData, OAuthUserInfo

        state = oauth_service.encode_state(
            OAuthStateData(
                code_verifier="test-verifier-1234567890abcdefghijklmno",
                redirect_uri="https://cp.example.com/callback",
            )
        )
        userinfo = OAuthUserInfo(
            sub="casdoor-user-1",
            email="oauth-login@example.com",
            name="OAuth Login User",
            groups=[],
        )
        with (
            patch(
                "app.api.routers.oauth.oauth_service.exchange_code_for_tokens",
                new_callable=AsyncMock,
                return_value={"access_token": "fake-provider-access-token"},
            ),
            patch(
                "app.api.routers.oauth.oauth_service.get_user_info",
                new_callable=AsyncMock,
                return_value=userinfo,
            ),
        ):
            response = client.get(
                f"/v1/auth/oauth/{provider.name}/callback",
                params={"code": "fake-code", "state": state},
            )
        return response

    def test_callback_queues_security_email(self, client: TestClient, db_session: Session):
        provider = self._make_provider(db_session)

        response = self._run_callback(client, db_session, provider)
        assert response.status_code == 200, response.text

        emails = (
            db_session.query(models.EmailQueue)
            .filter(models.EmailQueue.to_email == "oauth-login@example.com")
            .all()
        )
        security_emails = [e for e in emails if e.email_type == "security_new_session"]
        assert len(security_emails) == 1, [e.email_type for e in emails]

    def test_callback_queues_session_created_webhook(self, client: TestClient, db_session: Session):
        provider = self._make_provider(db_session)
        self._make_global_webhook(db_session, "session.created")

        response = self._run_callback(client, db_session, provider)
        assert response.status_code == 200, response.text

        deliveries = (
            db_session.query(models.WebhookDelivery)
            .filter(models.WebhookDelivery.event_type == "session.created")
            .all()
        )
        assert len(deliveries) == 1
        assert deliveries[0].payload["data"]["user"]["email"] == "oauth-login@example.com"

    def test_callback_without_notification_fix_would_have_sent_nothing(
        self, client: TestClient, db_session: Session
    ):
        """Guards against the exact regression: with notify_session_created()
        stubbed out (simulating the pre-fix code), NEITHER channel fires —
        proves the two tests above actually exercise the fix, not some other
        path that would queue these regardless."""
        provider = self._make_provider(db_session)
        self._make_global_webhook(db_session, "session.created")

        with patch(
            "app.api.routers.oauth.get_notification_service"
        ) as mock_get_notification_service:
            mock_service = MagicMock()
            mock_service.notify_session_created = AsyncMock()
            mock_get_notification_service.return_value = mock_service

            response = self._run_callback(client, db_session, provider)
            assert response.status_code == 200, response.text
            mock_service.notify_session_created.assert_awaited_once()

        # With the real notification service swapped out, nothing should have
        # actually landed in either queue.
        emails = (
            db_session.query(models.EmailQueue)
            .filter(models.EmailQueue.to_email == "oauth-login@example.com")
            .all()
        )
        assert emails == []
        deliveries = (
            db_session.query(models.WebhookDelivery)
            .filter(models.WebhookDelivery.event_type == "session.created")
            .all()
        )
        assert deliveries == []


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

            updated, changes = oauth_service.sync_user_info(db_session, user, userinfo)

            assert updated is True
            assert user.is_admin is True
            assert "is_admin" in changes
            assert changes["is_admin"]["old"] is False
            assert changes["is_admin"]["new"] is True

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

            updated, changes = oauth_service.sync_user_info(db_session, user, userinfo)

            assert updated is False
            assert changes == {}
            assert user.is_admin is False  # Unchanged

    def test_sync_user_info_only_elevates_admin(self, db_session: Session):
        """Test that sync only ELEVATES admin rights, never revokes them."""
        from app.schemas.oauth import OAuthUserInfo

        # Create user as admin
        user = models.User(
            id=uuid.uuid4(),
            email="admin@example.com",
            password_hash="hash",
            is_admin=True,  # Already admin
            is_active=True,
        )
        db_session.add(user)
        db_session.commit()

        # User NOT in admin groups anymore
        userinfo = OAuthUserInfo(
            sub="oauth_admin_1",
            email="admin@example.com",
            name="Admin User",
            groups=["users"],  # No admin group
        )

        with patch("app.services.oauth_service.get_settings") as mock_settings:
            mock_settings.return_value = MagicMock(
                oauth_sync_user_info=True,
                oauth_admin_groups="admins",
            )

            updated, changes = oauth_service.sync_user_info(db_session, user, userinfo)

            # Should NOT revoke admin rights
            assert updated is False
            assert changes == {}
            assert user.is_admin is True  # Still admin

    def test_should_be_admin_with_org_prefix(self):
        """Test admin group matching with org/group format (Casdoor style)."""
        with patch("app.services.oauth_service.get_settings") as mock_settings:
            mock_settings.return_value = MagicMock(
                oauth_admin_groups="admin,evc_relay_admins",
            )

            # Exact match
            assert oauth_service.should_be_admin(["admin"]) is True
            assert oauth_service.should_be_admin(["evc_relay_admins"]) is True

            # Org/group format (Casdoor style) - simple admin group name matches any org
            assert oauth_service.should_be_admin(["entire_vc/admin"]) is True
            assert oauth_service.should_be_admin(["entire_vc/evc_relay_admins"]) is True

            # Cross-org match - "admin" matches any org's admin group
            assert oauth_service.should_be_admin(["other_org/admin"]) is True

            # Mixed case
            assert oauth_service.should_be_admin(["Entire_VC/Admin"]) is True

            # Multiple groups
            assert oauth_service.should_be_admin(["entire_vc/users", "entire_vc/admin"]) is True

            # Non-matching
            assert oauth_service.should_be_admin(["entire_vc/users"]) is False
            assert oauth_service.should_be_admin([]) is False

    def test_should_be_admin_with_full_org_path(self):
        """Test admin group matching with full org/group path specified."""
        with patch("app.services.oauth_service.get_settings") as mock_settings:
            # Configure with FULL org/group path
            mock_settings.return_value = MagicMock(
                oauth_admin_groups="entire_vc/admin,entire_vc/superusers",
            )

            # Exact match with full path
            assert oauth_service.should_be_admin(["entire_vc/admin"]) is True
            assert oauth_service.should_be_admin(["entire_vc/superusers"]) is True

            # Different org should NOT match when full path is specified
            assert oauth_service.should_be_admin(["other_org/admin"]) is False

            # Simple name should NOT match when full path is specified
            assert oauth_service.should_be_admin(["admin"]) is False

            # Non-matching
            assert oauth_service.should_be_admin(["entire_vc/users"]) is False
            assert oauth_service.should_be_admin([]) is False

    def test_should_be_admin_with_partial_match_rejected(self):
        """Test that partial group name matches are rejected."""
        with patch("app.services.oauth_service.get_settings") as mock_settings:
            mock_settings.return_value = MagicMock(
                oauth_admin_groups="admin",
            )

            # "superadmin" should NOT match "admin"
            assert oauth_service.should_be_admin(["superadmin"]) is False
            assert oauth_service.should_be_admin(["entire_vc/superadmin"]) is False

            # Only exact match or org/exact_match should work
            assert oauth_service.should_be_admin(["admin"]) is True
            assert oauth_service.should_be_admin(["org/admin"]) is True


_TEST_STATE_SECRET = "testsecret32bytes00000000000000x"


class TestOAuthHmacState:
    """Tests for H5 — OAuth state HMAC signing (login-CSRF prevention)."""

    def test_encode_decode_state_with_hmac(self):
        """encode_state + decode_state round-trip when OAUTH_STATE_SECRET is set."""
        from app.schemas.oauth import OAuthStateData

        state_data = OAuthStateData(
            code_verifier="test_verifier",
            redirect_uri="https://example.com/callback",
        )

        with patch("app.services.oauth_service.get_settings") as mock_settings:
            mock_settings.return_value = MagicMock(oauth_state_secret=_TEST_STATE_SECRET)
            encoded = oauth_service.encode_state(state_data)
            # Signed state must contain separator
            assert "." in encoded, "HMAC-signed state must contain a separator"
            decoded = oauth_service.decode_state(encoded)

        assert decoded.code_verifier == state_data.code_verifier
        assert decoded.redirect_uri == state_data.redirect_uri

    def test_decode_state_rejects_tampered_payload(self):
        """State with a valid signature but altered payload must be rejected (400)."""
        from fastapi import HTTPException

        from app.schemas.oauth import OAuthStateData

        state_data = OAuthStateData(
            code_verifier="test_verifier",
            redirect_uri="https://example.com/callback",
        )

        with patch("app.services.oauth_service.get_settings") as mock_settings:
            mock_settings.return_value = MagicMock(oauth_state_secret=_TEST_STATE_SECRET)
            encoded = oauth_service.encode_state(state_data)
            payload_b64, sig = encoded.rsplit(".", 1)
            # Replace payload with a different one (attacker's state) but keep original sig
            import base64
            import json

            evil_payload = base64.urlsafe_b64encode(
                json.dumps(
                    {"code_verifier": "evil", "redirect_uri": "https://attacker.com"}
                ).encode()
            ).decode()
            tampered = f"{evil_payload}.{sig}"

        with patch("app.services.oauth_service.get_settings") as mock_settings:
            mock_settings.return_value = MagicMock(oauth_state_secret=_TEST_STATE_SECRET)
            with pytest.raises(HTTPException) as exc_info:
                oauth_service.decode_state(tampered)

        assert exc_info.value.status_code == 400
        assert "Invalid state parameter" in exc_info.value.detail

    def test_decode_state_rejects_missing_signature(self):
        """State without HMAC signature is rejected (400) when secret is configured."""
        import base64
        import json

        from fastapi import HTTPException

        # Craft an unsigned state (plain base64 JSON, no sig)
        unsigned = base64.urlsafe_b64encode(
            json.dumps({"code_verifier": "v", "redirect_uri": "https://evil.com"}).encode()
        ).decode()

        with patch("app.services.oauth_service.get_settings") as mock_settings:
            mock_settings.return_value = MagicMock(oauth_state_secret=_TEST_STATE_SECRET)
            with pytest.raises(HTTPException) as exc_info:
                oauth_service.decode_state(unsigned)

        assert exc_info.value.status_code == 400

    def test_decode_state_unsigned_allowed_without_secret(self):
        """When oauth_state_secret is None, unsigned states are still accepted (backcompat)."""
        from app.schemas.oauth import OAuthStateData

        state_data = OAuthStateData(
            code_verifier="test_verifier",
            redirect_uri="https://example.com/callback",
        )

        with patch("app.services.oauth_service.get_settings") as mock_settings:
            mock_settings.return_value = MagicMock(oauth_state_secret=None)
            encoded = oauth_service.encode_state(state_data)
            assert "." not in encoded, "Unsigned state must not contain a separator"
            decoded = oauth_service.decode_state(encoded)

        assert decoded.code_verifier == state_data.code_verifier

    def test_compute_state_hmac_deterministic(self):
        """_compute_state_hmac returns same value for same inputs (deterministic)."""
        sig1 = oauth_service._compute_state_hmac("payload", "secret")
        sig2 = oauth_service._compute_state_hmac("payload", "secret")
        assert sig1 == sig2

    def test_compute_state_hmac_differs_on_different_payload(self):
        """_compute_state_hmac returns different values for different payloads."""
        sig1 = oauth_service._compute_state_hmac("payload_a", "secret")
        sig2 = oauth_service._compute_state_hmac("payload_b", "secret")
        assert sig1 != sig2


class TestCorsAllowlistConfig:
    """Tests for H4 — CORS_ALLOWED_ORIGINS parsing."""

    def test_single_origin_parsed(self):
        """Single origin string is correctly parsed into a list."""
        from app.core.config import Settings

        s = Settings(cors_allowed_origins="https://cp.tr.entire.vc")
        origins = [o.strip() for o in s.cors_allowed_origins.split(",") if o.strip()]
        assert origins == ["https://cp.tr.entire.vc"]

    def test_multiple_origins_parsed(self):
        """Comma-separated origins are correctly split."""
        from app.core.config import Settings

        s = Settings(cors_allowed_origins="https://app.example.com, https://admin.example.com")
        origins = [o.strip() for o in s.cors_allowed_origins.split(",") if o.strip()]
        assert origins == ["https://app.example.com", "https://admin.example.com"]

    def test_default_is_production_origin(self):
        """Default CORS origin is the production control-plane URL, not wildcard."""
        from app.core.config import Settings

        s = Settings()
        assert "*" not in s.cors_allowed_origins
        assert "cp.tr.entire.vc" in s.cors_allowed_origins


def _make_rsa_keypair():
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return private_key, private_key.public_key()


def _make_id_token(private_key, **claim_overrides):
    now = datetime.now(timezone.utc)
    claims = {
        "iss": "https://casdoor.example.com",
        "aud": "test_client",
        "sub": "casdoor-sub-1",
        "iat": now,
        "exp": now + timedelta(minutes=5),
    }
    claims.update(claim_overrides)
    return pyjwt.encode(claims, private_key, algorithm="RS256")


def _make_provider(**overrides):
    defaults = dict(
        id=uuid.uuid4(),
        name="casdoor",
        provider_type=models.OAuthProviderType.OIDC,
        issuer_url="https://casdoor.example.com",
        client_id="test_client",
        client_secret_encrypted="secret",
        enabled=True,
        auto_register=True,
    )
    defaults.update(overrides)
    return models.OAuthProvider(**defaults)


_DISCOVERY = {
    "jwks_uri": "https://casdoor.example.com/.well-known/jwks",
    "issuer": "https://casdoor.example.com",
    "id_token_signing_alg_values_supported": ["RS256"],
}


class TestValidateIdToken:
    """Direct crypto tests for validate_id_token() (#f1e6f0dc).

    Only the network fetch (discovery + JWKS) is mocked; jwt.decode() runs
    for real, so a wrong key / wrong issuer / wrong audience / expired token
    is rejected by PyJWT itself, not by an assertion we wrote ourselves.
    """

    def _mocked_client(self, public_key):
        client = MagicMock()
        client.get_signing_key_from_jwt.return_value = SimpleNamespace(key=public_key)
        return client

    @pytest.mark.asyncio
    async def test_accepts_correctly_signed_token(self):
        private_key, public_key = _make_rsa_keypair()
        token = _make_id_token(private_key, email_verified=True, email="a@example.com")
        provider = _make_provider()

        with (
            patch(
                "app.services.oauth_service._discover_oidc_config",
                new_callable=AsyncMock,
                return_value=_DISCOVERY,
            ),
            patch(
                "app.services.oauth_service._get_jwks_client",
                return_value=self._mocked_client(public_key),
            ),
        ):
            claims = await oauth_service.validate_id_token(provider, token)

        assert claims["email_verified"] is True
        assert claims["sub"] == "casdoor-sub-1"

    @pytest.mark.asyncio
    async def test_rejects_wrong_signing_key(self):
        """Token signed with one key, JWKS serves a DIFFERENT one — the
        exact shape of a forged/unvalidated token being trusted."""
        private_key, _ = _make_rsa_keypair()
        _, wrong_public_key = _make_rsa_keypair()
        token = _make_id_token(private_key, email_verified=True)
        provider = _make_provider()

        with (
            patch(
                "app.services.oauth_service._discover_oidc_config",
                new_callable=AsyncMock,
                return_value=_DISCOVERY,
            ),
            patch(
                "app.services.oauth_service._get_jwks_client",
                return_value=self._mocked_client(wrong_public_key),
            ),
            pytest.raises(pyjwt.InvalidSignatureError),
        ):
            await oauth_service.validate_id_token(provider, token)

    @pytest.mark.asyncio
    async def test_rejects_wrong_audience(self):
        """Token issued for a DIFFERENT client_id than ours must be rejected —
        otherwise a token minted for another application on the same IdP
        would be accepted here."""
        private_key, public_key = _make_rsa_keypair()
        token = _make_id_token(private_key, aud="some-other-client", email_verified=True)
        provider = _make_provider(client_id="test_client")

        with (
            patch(
                "app.services.oauth_service._discover_oidc_config",
                new_callable=AsyncMock,
                return_value=_DISCOVERY,
            ),
            patch(
                "app.services.oauth_service._get_jwks_client",
                return_value=self._mocked_client(public_key),
            ),
            pytest.raises(pyjwt.InvalidAudienceError),
        ):
            await oauth_service.validate_id_token(provider, token)

    @pytest.mark.asyncio
    async def test_rejects_expired_token(self):
        private_key, public_key = _make_rsa_keypair()
        past = datetime.now(timezone.utc) - timedelta(minutes=10)
        token = _make_id_token(
            private_key, email_verified=True, iat=past, exp=past + timedelta(minutes=1)
        )
        provider = _make_provider()

        with (
            patch(
                "app.services.oauth_service._discover_oidc_config",
                new_callable=AsyncMock,
                return_value=_DISCOVERY,
            ),
            patch(
                "app.services.oauth_service._get_jwks_client",
                return_value=self._mocked_client(public_key),
            ),
            pytest.raises(pyjwt.ExpiredSignatureError),
        ):
            await oauth_service.validate_id_token(provider, token)


class TestGetUserInfoEmailVerifiedSource:
    """get_user_info() must source email_verified from the id_token, never
    from /api/userinfo (#f1e6f0dc / #970e22f4)."""

    def _mock_userinfo_http(self, userinfo_json: dict):
        """Patch the AsyncOAuth2Client used to hit /api/userinfo."""
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = userinfo_json
        mock_client_instance = AsyncMock()
        mock_client_instance.get = AsyncMock(return_value=mock_response)
        mock_client_instance.__aenter__ = AsyncMock(return_value=mock_client_instance)
        mock_client_instance.__aexit__ = AsyncMock(return_value=False)
        return patch(
            "app.services.oauth_service.AsyncOAuth2Client", return_value=mock_client_instance
        )

    @pytest.mark.asyncio
    async def test_userinfo_claims_verified_but_id_token_says_unverified(self):
        """THE tamper-control from #f1e6f0dc's acceptance criteria: userinfo
        (which Casdoor always reports as email_verified=true when the email
        scope is granted) says verified; id_token says NOT verified. The
        result must follow the id_token — this is the regression test that
        would catch the fix being silently reverted to trust userinfo again.
        """
        private_key, public_key = _make_rsa_keypair()
        id_token = _make_id_token(private_key, email_verified=False, email="victim@example.com")
        provider = _make_provider()

        with (
            self._mock_userinfo_http(
                {
                    "sub": "casdoor-sub-1",
                    "email": "victim@example.com",
                    "email_verified": True,  # userinfo LIES — must be ignored
                }
            ),
            patch(
                "app.services.oauth_service._discover_oidc_config",
                new_callable=AsyncMock,
                return_value=_DISCOVERY,
            ),
            patch("app.services.oauth_service._get_jwks_client") as mock_get_client,
        ):
            mock_client = MagicMock()
            mock_client.get_signing_key_from_jwt.return_value = SimpleNamespace(key=public_key)
            mock_get_client.return_value = mock_client

            userinfo = await oauth_service.get_user_info(
                provider, {"access_token": "fake-token", "id_token": id_token}
            )

        assert userinfo.email_verified is False

    @pytest.mark.asyncio
    async def test_valid_verified_id_token_is_trusted(self):
        """Positive control: a genuinely verified id_token DOES produce
        email_verified=True, proving the gate isn't just permanently closed."""
        private_key, public_key = _make_rsa_keypair()
        id_token = _make_id_token(private_key, email_verified=True, email="real@example.com")
        provider = _make_provider()

        with (
            self._mock_userinfo_http(
                {"sub": "casdoor-sub-1", "email": "real@example.com", "email_verified": True}
            ),
            patch(
                "app.services.oauth_service._discover_oidc_config",
                new_callable=AsyncMock,
                return_value=_DISCOVERY,
            ),
            patch("app.services.oauth_service._get_jwks_client") as mock_get_client,
        ):
            mock_client = MagicMock()
            mock_client.get_signing_key_from_jwt.return_value = SimpleNamespace(key=public_key)
            mock_get_client.return_value = mock_client

            userinfo = await oauth_service.get_user_info(
                provider, {"access_token": "fake-token", "id_token": id_token}
            )

        assert userinfo.email_verified is True

    @pytest.mark.asyncio
    async def test_missing_id_token_fails_closed(self):
        """No id_token in the token response at all (e.g. 'openid' scope not
        granted) must default to email_verified=False, not raise and not
        default to True."""
        provider = _make_provider()

        with self._mock_userinfo_http(
            {"sub": "casdoor-sub-1", "email": "noidtoken@example.com", "email_verified": True}
        ):
            userinfo = await oauth_service.get_user_info(
                provider,
                {"access_token": "fake-token"},  # no id_token key at all
            )

        assert userinfo.email_verified is False

    @pytest.mark.asyncio
    async def test_unparseable_id_token_fails_closed(self):
        """A garbage id_token (network hiccup during JWKS fetch, malformed
        token, whatever) must fail closed to email_verified=False rather
        than raise out of the whole login flow."""
        provider = _make_provider()

        with (
            self._mock_userinfo_http(
                {"sub": "casdoor-sub-1", "email": "garbage@example.com", "email_verified": True}
            ),
            patch(
                "app.services.oauth_service._discover_oidc_config",
                new_callable=AsyncMock,
                side_effect=RuntimeError("network unreachable"),
            ),
        ):
            userinfo = await oauth_service.get_user_info(
                provider, {"access_token": "fake-token", "id_token": "not-a-real-jwt"}
            )

        assert userinfo.email_verified is False


class TestOAuthCallbackEmailVerifiedGate:
    """Router-level red/green controls for the account-linking gate
    (#f1e6f0dc). Mirrors TestOAuthCallbackSessionNotification's pattern:
    mock exchange_code_for_tokens + get_user_info, hit the real callback."""

    def _make_existing_user(self, db_session: Session, email: str) -> models.User:
        user = models.User(
            id=uuid.uuid4(),
            email=email,
            password_hash="hash",
            is_admin=False,
            is_active=True,
        )
        db_session.add(user)
        db_session.commit()
        return user

    def _make_provider_row(self, db_session: Session) -> models.OAuthProvider:
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
        return provider

    def _run_callback(
        self,
        client: TestClient,
        provider: models.OAuthProvider,
        email: str,
        email_verified: bool,
        sub: str = "new-casdoor-sub",
    ):
        from app.schemas.oauth import OAuthStateData, OAuthUserInfo

        state = oauth_service.encode_state(
            OAuthStateData(
                code_verifier="test-verifier-1234567890abcdefghijklmno",
                redirect_uri="https://cp.example.com/callback",
            )
        )
        userinfo = OAuthUserInfo(
            sub=sub,
            email=email,
            name="Some User",
            groups=[],
            email_verified=email_verified,
        )
        with (
            patch(
                "app.api.routers.oauth.oauth_service.exchange_code_for_tokens",
                new_callable=AsyncMock,
                return_value={"access_token": "fake-provider-access-token"},
            ),
            patch(
                "app.api.routers.oauth.oauth_service.get_user_info",
                new_callable=AsyncMock,
                return_value=userinfo,
            ),
        ):
            return client.get(
                f"/v1/auth/oauth/{provider.name}/callback",
                params={"code": "fake-code", "state": state},
            )

    def test_RED_unverified_email_match_is_refused(self, client: TestClient, db_session: Session):
        """Existing user's email, but the id_token says NOT verified — MUST
        NOT be linked, MUST NOT issue tokens for the existing account."""
        provider = self._make_provider_row(db_session)
        existing = self._make_existing_user(db_session, "victim@example.com")

        response = self._run_callback(
            client, provider, email="victim@example.com", email_verified=False
        )

        assert response.status_code == 409, response.text
        assert "already exists" in response.json().get("detail", response.text)

        # No new OAuth account link was created for the existing user.
        linked = oauth_service.find_user_by_oauth(db_session, provider.id, "new-casdoor-sub")
        assert linked is None

        # The existing user's account is untouched (no session/token issued).
        db_session.refresh(existing)
        accounts = db_session.query(models.UserOAuthAccount).filter_by(user_id=existing.id).all()
        assert accounts == []

        # An audit record of the DENIAL exists, naming the attempted sub + email.
        denial = (
            db_session.query(models.AuditLog)
            .filter_by(action=models.AuditAction.OAUTH_ACCOUNT_LINK_DENIED)
            .one_or_none()
        )
        assert denial is not None
        assert denial.target_user_id == existing.id
        assert denial.details["provider_user_id"] == "new-casdoor-sub"
        assert denial.details["email"] == "victim@example.com"
        assert denial.details["reason"] == "email_not_verified"

    def test_GREEN_verified_email_match_links_as_before(
        self, client: TestClient, db_session: Session
    ):
        """Same scenario, but email_verified=True — linking proceeds exactly
        as it did before this fix."""
        provider = self._make_provider_row(db_session)
        existing = self._make_existing_user(db_session, "real-user@example.com")

        response = self._run_callback(
            client,
            provider,
            email="real-user@example.com",
            email_verified=True,
            sub="verified-casdoor-sub",
        )

        assert response.status_code == 200, response.text
        data = response.json()
        assert data["user_id"] == str(existing.id)

        linked = oauth_service.find_user_by_oauth(db_session, provider.id, "verified-casdoor-sub")
        assert linked is not None
        assert linked.id == existing.id

        # No denial record for the successful case.
        denial = (
            db_session.query(models.AuditLog)
            .filter_by(action=models.AuditAction.OAUTH_ACCOUNT_LINK_DENIED)
            .one_or_none()
        )
        assert denial is None

    def test_unverified_no_existing_user_falls_through_to_auto_register(
        self, client: TestClient, db_session: Session
    ):
        """The gate only applies to the find-by-email fallback path. A brand
        new email (no existing account) with email_verified=False must still
        be able to auto-register — this isn't a blanket "unverified email
        can never log in" rule, only "can't piggyback onto someone else's
        account by email match."""
        provider = self._make_provider_row(db_session)

        response = self._run_callback(
            client,
            provider,
            email="brand-new@example.com",
            email_verified=False,
            sub="brand-new-sub",
        )

        assert response.status_code == 200, response.text
        created = oauth_service.find_user_by_email(db_session, "brand-new@example.com")
        assert created is not None
