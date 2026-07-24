"""Tests for security features: rate limiting and public key distribution."""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.db import models


def auth_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def login(client: TestClient, email: str, password: str) -> str:
    response = client.post("/auth/login", json={"email": email, "password": password})
    assert response.status_code == 200, response.text
    return response.json()["access_token"]


# ── TR-20: password-login of an OAuth-only user must 401, not 500 ────────────
#
# OAuth-only accounts are created with password_hash="" (oauth_service.py:457
# — "No password for OAuth-only accounts"). passlib can't identify an empty
# string as a hash and raises UnknownHashError rather than returning False;
# unhandled, that surfaced as a 500 from POST /auth/login instead of the
# expected 401 (prod repro 2026-07-19T08:12:15).


class TestTR20OAuthUserPasswordLogin:
    def test_verify_password_returns_false_for_empty_hash(self):
        """Direct unit test of the actual failure point (core/security.py)."""
        from app.core import security

        assert security.verify_password("anything", "") is False

    def test_verify_password_returns_false_for_garbage_hash(self):
        """Not just the empty-string case — any hash passlib can't identify
        must fail closed, not raise."""
        from app.core import security

        assert security.verify_password("anything", "not-a-real-hash") is False

    def test_verify_password_returns_false_for_malformed_bcrypt_hash(self):
        """A scheme-prefixed but corrupted hash (e.g. a truncated bcrypt salt)
        raises a *bare* ValueError from passlib's backend, not
        UnknownHashError — a narrower `except UnknownHashError` would still
        crash on this shape. Found by independent review, not the original
        repro."""
        from app.core import security

        assert security.verify_password("anything", "$2b$04$shorttoolong") is False

    def test_login_with_password_for_oauth_only_user_returns_401(
        self, db_session: Session, client: TestClient
    ):
        """End-to-end repro: an OAuth-only user (password_hash="", exactly
        as oauth_service.create/link produces it) attempts a password login —
        must get 401, never an unhandled 500."""
        user = models.User(
            id=uuid.uuid4(),
            email="oauth-only@example.com",
            password_hash="",
            is_admin=False,
            is_active=True,
        )
        db_session.add(user)
        db_session.commit()

        response = client.post(
            "/auth/login",
            json={"email": "oauth-only@example.com", "password": "whatever-they-typed"},
        )

        assert response.status_code == 401, response.text


def test_public_key_endpoint(client: TestClient) -> None:
    """Test GET /keys/public returns public key without authentication."""
    response = client.get("/keys/public")
    assert response.status_code == 200, response.text

    data = response.json()
    assert "key_id" in data
    assert "public_key" in data
    assert "algorithm" in data
    assert data["algorithm"] == "EdDSA"
    assert len(data["public_key"]) > 0  # Base64 encoded key should be non-empty
    assert data["key_id"].startswith("relay_cp_")


def test_rate_limiting_login(client: TestClient) -> None:
    """Test rate limiting on /auth/login endpoint (10/minute)."""
    # Make 11 login attempts with invalid credentials
    failed_attempts = 0
    rate_limited = False

    for i in range(11):
        response = client.post(
            "/auth/login",
            json={"email": "invalid@example.com", "password": "wrong"},
        )
        if response.status_code == 429:  # Too Many Requests
            rate_limited = True
            break
        elif response.status_code == 401:  # Unauthorized
            failed_attempts += 1

    # Should get rate limited before 11th attempt
    assert rate_limited or failed_attempts <= 10, "Rate limiting not triggered"


def test_rate_limiting_share_creation(client: TestClient) -> None:
    """Test rate limiting on POST /shares endpoint (20/minute)."""
    admin_token = login(client, "bootstrap@example.com", "super-secret")

    # Create 21 shares rapidly
    rate_limited = False
    created = 0

    for i in range(21):
        response = client.post(
            "/shares",
            json={
                "kind": "doc",
                "path": f"vault/test{i}.md",
                "visibility": "private",
            },
            headers=auth_headers(admin_token),
        )
        if response.status_code == 429:  # Too Many Requests
            rate_limited = True
            break
        elif response.status_code == 201:  # Created
            created += 1

    # Should get rate limited at some point
    assert rate_limited or created <= 20, "Rate limiting not triggered"


def test_rate_limiting_member_addition(client: TestClient) -> None:
    """Test rate limiting on POST /shares/{share_id}/members endpoint (30/minute)."""
    admin_token = login(client, "bootstrap@example.com", "super-secret")

    # Create a share
    share_response = client.post(
        "/shares",
        json={"kind": "doc", "path": "vault/test.md", "visibility": "private"},
        headers=auth_headers(admin_token),
    )
    assert share_response.status_code == 201
    share_id = share_response.json()["id"]

    # Create 31 users
    user_ids = []
    for i in range(31):
        user_response = client.post(
            "/admin/users",
            json={
                "email": f"user{i}@example.com",
                "password": "password",
                "is_admin": False,
                "is_active": True,
            },
            headers=auth_headers(admin_token),
        )
        if user_response.status_code == 201:
            user_ids.append(user_response.json()["id"])

    # Try to add all users rapidly
    rate_limited = False
    added = 0

    for user_id in user_ids:
        response = client.post(
            f"/shares/{share_id}/members",
            json={"user_id": user_id, "role": "viewer"},
            headers=auth_headers(admin_token),
        )
        if response.status_code == 429:  # Too Many Requests
            rate_limited = True
            break
        elif response.status_code == 201:  # Created
            added += 1

    # Should get rate limited at some point
    assert rate_limited or added <= 30, "Rate limiting not triggered"


def test_rate_limiting_token_issuance(client: TestClient) -> None:
    """Test rate limiting on POST /tokens/relay endpoint (30/minute)."""
    admin_token = login(client, "bootstrap@example.com", "super-secret")

    # Create a public share
    share_response = client.post(
        "/shares",
        json={"kind": "doc", "path": "vault/public.md", "visibility": "public"},
        headers=auth_headers(admin_token),
    )
    assert share_response.status_code == 201
    share_id = share_response.json()["id"]

    # Request 31 tokens rapidly
    rate_limited = False
    issued = 0

    for i in range(31):
        response = client.post(
            "/tokens/relay",
            json={
                "share_id": share_id,
                "doc_id": "vault/public.md",
                "mode": "read",
            },
        )
        if response.status_code == 429:  # Too Many Requests
            rate_limited = True
            break
        elif response.status_code == 200:  # OK
            issued += 1

    # Should get rate limited at some point
    assert rate_limited or issued <= 30, "Rate limiting not triggered"


def test_audit_logging_on_login(client: TestClient) -> None:
    """Test that login events are logged to audit log."""
    admin_token = login(client, "bootstrap@example.com", "super-secret")

    # Get audit logs
    audit_response = client.get(
        "/admin/audit-logs",
        headers=auth_headers(admin_token),
    )
    assert audit_response.status_code == 200

    logs = audit_response.json()
    # Should have at least one user_login event
    login_logs = [log for log in logs if log["action"] == "user_login"]
    assert len(login_logs) > 0, "No login events in audit log"


# ── H7: Default-credential startup validation ────────────────


class TestH7SecretValidation:
    """Startup check rejects known-insecure default credentials (H7)."""

    def test_insecure_jwt_secret_raises(self, monkeypatch):
        """build_app() must raise when JWT_SECRET is the hardcoded default."""
        from app.core.config import get_settings
        from app.main import build_app

        monkeypatch.setenv("JWT_SECRET", "dev-secret-change-me")
        get_settings.cache_clear()
        try:
            app = build_app()
            with pytest.raises(RuntimeError, match="JWT_SECRET"):
                with TestClient(app):
                    pass
        finally:
            get_settings.cache_clear()

    def test_empty_jwt_secret_raises(self, monkeypatch):
        """build_app() must raise when JWT_SECRET is empty."""
        from app.core.config import get_settings
        from app.main import build_app

        monkeypatch.setenv("JWT_SECRET", "")
        get_settings.cache_clear()
        try:
            app = build_app()
            with pytest.raises(RuntimeError, match="JWT_SECRET"):
                with TestClient(app):
                    pass
        finally:
            get_settings.cache_clear()

    def test_safe_jwt_secret_passes(self, monkeypatch):
        """build_app() must not raise when JWT_SECRET is a non-default value."""
        from app.core.config import get_settings
        from app.main import build_app

        monkeypatch.setenv("JWT_SECRET", "safe-random-value-for-test-only")
        get_settings.cache_clear()
        try:
            app = build_app()
            with TestClient(app):
                pass  # no exception expected
        finally:
            get_settings.cache_clear()


def test_audit_logging_on_logout(client: TestClient) -> None:
    """Test that logout events are logged to audit log."""
    admin_token = login(client, "bootstrap@example.com", "super-secret")

    # Logout
    logout_response = client.post("/auth/logout", headers=auth_headers(admin_token))
    assert logout_response.status_code == 200

    # Login again to check logs
    admin_token = login(client, "bootstrap@example.com", "super-secret")

    # Get audit logs
    audit_response = client.get(
        "/admin/audit-logs",
        headers=auth_headers(admin_token),
    )
    assert audit_response.status_code == 200

    logs = audit_response.json()
    # Should have at least one user_logout event
    logout_logs = [log for log in logs if log["action"] == "user_logout"]
    assert len(logout_logs) > 0, "No logout events in audit log"


# ── TR-22: relay-token TTL bounds the remove_member exposure window ──────────


class TestTR22RelayTokenTTL:
    """relay-token is a stateless CWT with no jti/revocation-list (#f63a2bea) —
    remove_member cannot invalidate an already-issued token, so
    relay_token_ttl_minutes is the entire window during which a removed member
    can still write to the CRDT doc. Guard against this silently regressing
    back to the old 30-minute default.
    """

    def test_default_ttl_is_short(self, monkeypatch):
        from app.core.config import get_settings

        monkeypatch.delenv("RELAY_TOKEN_TTL_MINUTES", raising=False)
        get_settings.cache_clear()
        try:
            settings = get_settings()
            assert settings.relay_token_ttl_minutes <= 10, (
                "relay_token_ttl_minutes default grew past the TR-22 bound — this "
                "directly widens the write-after-removal window since relay-tokens "
                "cannot be revoked early (no jti/revocation-list)."
            )
        finally:
            get_settings.cache_clear()

    def test_ttl_is_configurable_via_env(self, monkeypatch):
        """Deployments needing a different TTL can still override it explicitly."""
        from app.core.config import get_settings

        monkeypatch.setenv("RELAY_TOKEN_TTL_MINUTES", "2")
        get_settings.cache_clear()
        try:
            settings = get_settings()
            assert settings.relay_token_ttl_minutes == 2
        finally:
            monkeypatch.delenv("RELAY_TOKEN_TTL_MINUTES", raising=False)
            get_settings.cache_clear()
