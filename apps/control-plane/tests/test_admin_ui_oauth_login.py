"""Tests for Team Relay #86ac9eef: /admin-ui had no Casdoor OAuth login path.

An OAuth-only account (models.User with password_hash="") could authenticate
via Casdoor for the regular API, but /admin-ui/login was a password-only
form — such an account could never reach the admin panel at all ("Login
failed" really meant "there is no password to check").

The fix reuses the existing, already-tested /v1/auth/oauth/{provider}/callback
(it exchanges the code, finds-or-creates the user, and — when state carries a
return_url — sets the short-lived invite_token cookie and redirects) and adds
a small bridge, /admin-ui/login/oauth/complete, that resolves that cookie to a
user and applies the EXACT SAME gate as the password path in login_submit:
is_admin required, and a totp_enabled account still goes through
/admin-ui/login/2fa — OAuth must not bypass 2FA (TR-06, #fceefc4f).
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pyotp
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.db import models
from app.schemas.oauth import OAuthStateData, OAuthUserInfo
from app.services import oauth_service


def _make_provider(db_session: Session) -> models.OAuthProvider:
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


def _make_oauth_user(
    db_session: Session, provider: models.OAuthProvider, *, email: str, sub: str, is_admin: bool
) -> models.User:
    """An OAuth-only account, same shape create_user_from_oauth() produces:
    no password (password_hash=""), so the password form can never work."""
    user = models.User(
        id=uuid.uuid4(),
        email=email,
        password_hash="",
        is_admin=is_admin,
        is_active=True,
    )
    db_session.add(user)
    db_session.flush()

    db_session.add(
        models.UserOAuthAccount(
            id=uuid.uuid4(),
            user_id=user.id,
            provider_id=provider.id,
            provider_user_id=sub,
            email=email,
        )
    )
    db_session.commit()
    db_session.refresh(user)
    return user


def _run_oauth_callback(
    client: TestClient, provider: models.OAuthProvider, *, sub: str, email: str
) -> str:
    """Drive the real /v1/auth/oauth/{provider}/callback with return_url
    pointed at the admin-ui bridge — same mocking technique as
    TestOAuthCallbackSessionNotification._run_callback in test_oauth.py.
    Returns the invite_token cookie value the callback set."""
    state = oauth_service.encode_state(
        OAuthStateData(
            code_verifier="test-verifier-1234567890abcdefghijklmno",
            redirect_uri="https://cp.example.com/v1/auth/oauth/casdoor/callback",
            return_url="/admin-ui/login/oauth/complete",
        )
    )
    userinfo = OAuthUserInfo(sub=sub, email=email, name="OAuth Admin", groups=[])
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
            follow_redirects=False,
        )
    assert response.status_code == 302, response.text
    assert "invite_token" in response.cookies
    return response.cookies["invite_token"]


class TestAdminLoginPageOAuthButtonVisibility:
    def test_button_hidden_when_oauth_disabled(self, client: TestClient):
        # Default test settings: oauth_enabled=False.
        response = client.get("/admin-ui/login")
        assert response.status_code == 200
        assert "Sign in with Casdoor" not in response.text

    def test_button_shown_when_oauth_enabled(self, client: TestClient, db_session: Session):
        _make_provider(db_session)
        with patch("app.api.routers.admin_ui.get_settings") as mock_settings:
            mock_settings.return_value = MagicMock(
                oauth_enabled=True, oauth_provider_name="casdoor"
            )
            response = client.get("/admin-ui/login")
        assert response.status_code == 200
        assert "Sign in with Casdoor" in response.text
        assert "/v1/auth/oauth/casdoor/authorize" in response.text
        assert "return_url=" in response.text


class TestAdminOAuthLoginHappyPath:
    def test_admin_user_without_2fa_reaches_dashboard_via_oauth(
        self, client: TestClient, db_session: Session
    ):
        """The exact bug: an OAuth-only admin account (no password) must be
        able to reach the admin panel through the Casdoor button."""
        provider = _make_provider(db_session)
        user = _make_oauth_user(
            db_session,
            provider,
            email="oauth-admin@example.com",
            sub="casdoor-admin-1",
            is_admin=True,
        )
        assert user.password_hash == ""  # temp password removed / never had one

        invite_token = _run_oauth_callback(
            client, provider, sub="casdoor-admin-1", email="oauth-admin@example.com"
        )

        response = client.get(
            "/admin-ui/login/oauth/complete",
            cookies={"invite_token": invite_token},
            follow_redirects=False,
        )

        assert response.status_code == 302
        assert response.headers["location"] == "/admin-ui/dashboard"
        assert "admin_token" in response.cookies
        # Single-use bridge cookie must not survive past this exchange.
        assert response.cookies.get("invite_token") in (None, "", '""')

        dashboard = client.get(
            "/admin-ui/dashboard", cookies={"admin_token": response.cookies["admin_token"]}
        )
        assert dashboard.status_code == 200

    def test_repeat_oauth_login_does_not_duplicate_user_or_link(
        self, client: TestClient, db_session: Session
    ):
        """Acceptance criterion 4: a second OAuth login by the same person
        must reuse the existing user/oauth-account rows, not create new
        ones (find_user_by_oauth() + the uq_provider_user constraint)."""
        provider = _make_provider(db_session)
        _make_oauth_user(
            db_session,
            provider,
            email="oauth-admin2@example.com",
            sub="casdoor-admin-2",
            is_admin=True,
        )

        users_before = db_session.query(models.User).count()
        links_before = db_session.query(models.UserOAuthAccount).count()

        for _ in range(2):
            invite_token = _run_oauth_callback(
                client, provider, sub="casdoor-admin-2", email="oauth-admin2@example.com"
            )
            resp = client.get(
                "/admin-ui/login/oauth/complete",
                cookies={"invite_token": invite_token},
                follow_redirects=False,
            )
            assert resp.status_code == 302
            assert resp.headers["location"] == "/admin-ui/dashboard"

        assert db_session.query(models.User).count() == users_before
        assert db_session.query(models.UserOAuthAccount).count() == links_before


class TestAdminOAuthLoginRedControls:
    def test_non_admin_oauth_user_does_not_become_admin(
        self, client: TestClient, db_session: Session
    ):
        """Red control: a person outside the admin group must NOT reach the
        dashboard via the Casdoor button, same as the password path."""
        provider = _make_provider(db_session)
        _make_oauth_user(
            db_session,
            provider,
            email="oauth-user@example.com",
            sub="casdoor-user-1",
            is_admin=False,
        )

        invite_token = _run_oauth_callback(
            client, provider, sub="casdoor-user-1", email="oauth-user@example.com"
        )

        response = client.get(
            "/admin-ui/login/oauth/complete",
            cookies={"invite_token": invite_token},
            follow_redirects=False,
        )

        assert response.status_code == 302
        assert response.headers["location"].startswith("/admin-ui/login")
        assert "admin_token" not in response.cookies

    def test_missing_invite_token_redirects_to_login(self, client: TestClient):
        response = client.get("/admin-ui/login/oauth/complete", follow_redirects=False)
        assert response.status_code == 302
        assert response.headers["location"].startswith("/admin-ui/login")
        assert "admin_token" not in response.cookies

    def test_garbage_invite_token_is_rejected(self, client: TestClient):
        response = client.get(
            "/admin-ui/login/oauth/complete",
            cookies={"invite_token": "not-a-real-token"},
            follow_redirects=False,
        )
        assert response.status_code == 302
        assert response.headers["location"].startswith("/admin-ui/login")
        assert "admin_token" not in response.cookies


class TestAdminOAuthLoginDoesNotBypass2FA:
    def test_totp_enabled_admin_is_routed_through_2fa_not_straight_to_dashboard(
        self, client: TestClient, db_session: Session
    ):
        """OAuth must not be a side-door around TR-06's 2FA gate: a
        totp_enabled admin authenticating via Casdoor still has to clear
        /admin-ui/login/2fa before getting admin_token."""
        provider = _make_provider(db_session)
        user = _make_oauth_user(
            db_session,
            provider,
            email="oauth-2fa-admin@example.com",
            sub="casdoor-2fa-admin",
            is_admin=True,
        )

        # Enable TOTP directly on the model, mirroring how test_admin_ui_2fa.py
        # enables it via the real endpoint for a password account — here the
        # account has no password to log in with first, so seed it directly.
        secret = pyotp.random_base32()
        user.totp_secret_encrypted = secret
        user.totp_enabled = True
        db_session.commit()

        invite_token = _run_oauth_callback(
            client, provider, sub="casdoor-2fa-admin", email="oauth-2fa-admin@example.com"
        )

        response = client.get(
            "/admin-ui/login/oauth/complete",
            cookies={"invite_token": invite_token},
            follow_redirects=False,
        )

        assert response.status_code == 302
        assert response.headers["location"] == "/admin-ui/login/2fa"
        assert "admin_token" not in response.cookies
        assert "admin_2fa_pending" in response.cookies

        # Completing the real TOTP step issues the session, same endpoint the
        # password path uses.
        totp = pyotp.TOTP(secret)
        step2 = client.post(
            "/admin-ui/login/2fa",
            data={"totp_code": totp.now()},
            cookies={"admin_2fa_pending": response.cookies["admin_2fa_pending"]},
            follow_redirects=False,
        )
        assert step2.status_code == 302
        assert step2.headers["location"] == "/admin-ui/dashboard"
        assert "admin_token" in step2.cookies
