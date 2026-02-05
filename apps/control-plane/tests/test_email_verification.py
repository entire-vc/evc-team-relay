"""Tests for email verification functionality."""

from __future__ import annotations

import hashlib

from fastapi.testclient import TestClient


def auth_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def login(client: TestClient, email: str, password: str) -> str:
    response = client.post("/auth/login", json={"email": email, "password": password})
    assert response.status_code == 200, response.text
    return response.json()["access_token"]


def test_email_verification_status_unverified(client: TestClient) -> None:
    """Test email verification status for new (unverified) user."""
    admin_token = login(client, "bootstrap@example.com", "super-secret")

    # Create new user
    user_payload = {
        "email": "unverified@example.com",
        "password": "password123",
        "is_admin": False,
        "is_active": True,
    }
    client.post("/admin/users", json=user_payload, headers=auth_headers(admin_token))

    # Login as new user
    user_token = login(client, "unverified@example.com", "password123")

    # Check verification status
    response = client.get("/auth/email/verify/status", headers=auth_headers(user_token))
    assert response.status_code == 200
    data = response.json()
    assert data["email_verified"] is False
    assert data["email"] == "unverified@example.com"


def test_request_verification_email(client: TestClient) -> None:
    """Test requesting verification email."""
    admin_token = login(client, "bootstrap@example.com", "super-secret")

    # Create new user
    user_payload = {
        "email": "verifyme@example.com",
        "password": "password123",
        "is_admin": False,
        "is_active": True,
    }
    client.post("/admin/users", json=user_payload, headers=auth_headers(admin_token))

    # Login as new user
    user_token = login(client, "verifyme@example.com", "password123")

    # Request verification email
    response = client.post("/auth/email/verify/request", headers=auth_headers(user_token))
    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is True
    assert "sent" in data["message"].lower() or "verification" in data["message"].lower()


def test_request_verification_already_verified(client: TestClient, db_session) -> None:
    """Test requesting verification when already verified."""
    admin_token = login(client, "bootstrap@example.com", "super-secret")

    # Create new user
    user_payload = {
        "email": "alreadyverified@example.com",
        "password": "password123",
        "is_admin": False,
        "is_active": True,
    }
    user_resp = client.post("/admin/users", json=user_payload, headers=auth_headers(admin_token))
    user_id = user_resp.json()["id"]

    # Manually mark as verified in database
    from app.db import models

    user = (
        db_session.query(models.User)
        .filter(models.User.email == "alreadyverified@example.com")
        .first()
    )
    user.email_verified = True
    db_session.commit()

    # Login as user
    user_token = login(client, "alreadyverified@example.com", "password123")

    # Request verification - should return success but note already verified
    response = client.post("/auth/email/verify/request", headers=auth_headers(user_token))
    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is True
    assert "already" in data["message"].lower()


def test_verify_email_valid_token(client: TestClient, db_session) -> None:
    """Test email verification with valid token."""
    admin_token = login(client, "bootstrap@example.com", "super-secret")

    # Create new user
    user_payload = {
        "email": "validtoken@example.com",
        "password": "password123",
        "is_admin": False,
        "is_active": True,
    }
    user_resp = client.post("/admin/users", json=user_payload, headers=auth_headers(admin_token))
    user_id = user_resp.json()["id"]

    # Login and request verification
    user_token = login(client, "validtoken@example.com", "password123")
    client.post("/auth/email/verify/request", headers=auth_headers(user_token))

    # Get the token from database
    from app.db import models

    token_record = (
        db_session.query(models.EmailVerificationToken)
        .join(models.User)
        .filter(models.User.email == "validtoken@example.com")
        .first()
    )
    assert token_record is not None

    # We need to find the raw token from the hash - this is tricky in tests
    # For test purposes, let's create a known token
    import secrets
    import uuid
    from datetime import datetime, timedelta, timezone

    raw_token = secrets.token_hex(32)
    token_hash = hashlib.sha256(raw_token.encode()).hexdigest()

    # Delete existing token and create new one with known value
    db_session.delete(token_record)
    user = (
        db_session.query(models.User).filter(models.User.email == "validtoken@example.com").first()
    )
    new_token_record = models.EmailVerificationToken(
        id=uuid.uuid4(),
        user_id=user.id,
        token_hash=token_hash,
        expires_at=datetime.now(timezone.utc) + timedelta(hours=24),
    )
    db_session.add(new_token_record)
    db_session.commit()

    # Verify with the token
    response = client.get(f"/auth/email/verify/{raw_token}")
    assert response.status_code == 200
    assert "verified" in response.text.lower() or "success" in response.text.lower()

    # Check user is now verified
    db_session.refresh(user)
    assert user.email_verified is True


def test_verify_email_invalid_token(client: TestClient) -> None:
    """Test email verification with invalid token."""
    response = client.get("/auth/email/verify/invalid-token-that-does-not-exist")
    assert response.status_code == 200  # Returns HTML page
    assert "invalid" in response.text.lower() or "expired" in response.text.lower()


def test_verify_email_expired_token(client: TestClient, db_session) -> None:
    """Test email verification with expired token."""
    admin_token = login(client, "bootstrap@example.com", "super-secret")

    # Create new user
    user_payload = {
        "email": "expiredtoken@example.com",
        "password": "password123",
        "is_admin": False,
        "is_active": True,
    }
    client.post("/admin/users", json=user_payload, headers=auth_headers(admin_token))

    # Create expired token directly in database
    import secrets
    import uuid
    from datetime import datetime, timedelta, timezone

    from app.db import models

    user = (
        db_session.query(models.User)
        .filter(models.User.email == "expiredtoken@example.com")
        .first()
    )
    raw_token = secrets.token_hex(32)
    token_hash = hashlib.sha256(raw_token.encode()).hexdigest()

    expired_token = models.EmailVerificationToken(
        id=uuid.uuid4(),
        user_id=user.id,
        token_hash=token_hash,
        expires_at=datetime.now(timezone.utc) - timedelta(hours=1),  # Already expired
    )
    db_session.add(expired_token)
    db_session.commit()

    # Try to verify with expired token
    response = client.get(f"/auth/email/verify/{raw_token}")
    assert response.status_code == 200
    assert "invalid" in response.text.lower() or "expired" in response.text.lower()

    # Check user is still not verified
    db_session.refresh(user)
    assert user.email_verified is False


def test_verify_email_already_used_token(client: TestClient, db_session) -> None:
    """Test email verification with already used token."""
    admin_token = login(client, "bootstrap@example.com", "super-secret")

    # Create new user
    user_payload = {
        "email": "usedtoken@example.com",
        "password": "password123",
        "is_admin": False,
        "is_active": True,
    }
    client.post("/admin/users", json=user_payload, headers=auth_headers(admin_token))

    # Create already-used token directly in database
    import secrets
    import uuid
    from datetime import datetime, timedelta, timezone

    from app.db import models

    user = (
        db_session.query(models.User).filter(models.User.email == "usedtoken@example.com").first()
    )
    raw_token = secrets.token_hex(32)
    token_hash = hashlib.sha256(raw_token.encode()).hexdigest()

    used_token = models.EmailVerificationToken(
        id=uuid.uuid4(),
        user_id=user.id,
        token_hash=token_hash,
        expires_at=datetime.now(timezone.utc) + timedelta(hours=24),
        verified_at=datetime.now(timezone.utc) - timedelta(minutes=30),  # Already used
    )
    db_session.add(used_token)
    db_session.commit()

    # Try to verify with used token
    response = client.get(f"/auth/email/verify/{raw_token}")
    assert response.status_code == 200
    assert "invalid" in response.text.lower() or "expired" in response.text.lower()


def test_verification_requires_auth(client: TestClient) -> None:
    """Test that verification request requires authentication."""
    response = client.post("/auth/email/verify/request")
    assert response.status_code == 401


def test_verification_status_requires_auth(client: TestClient) -> None:
    """Test that verification status requires authentication."""
    response = client.get("/auth/email/verify/status")
    assert response.status_code == 401


def test_new_verification_invalidates_old_tokens(client: TestClient, db_session) -> None:
    """Test that requesting new verification invalidates old tokens."""
    admin_token = login(client, "bootstrap@example.com", "super-secret")

    # Create new user
    user_payload = {
        "email": "multitoken@example.com",
        "password": "password123",
        "is_admin": False,
        "is_active": True,
    }
    client.post("/admin/users", json=user_payload, headers=auth_headers(admin_token))

    user_token = login(client, "multitoken@example.com", "password123")

    # Request first verification
    client.post("/auth/email/verify/request", headers=auth_headers(user_token))

    # Count tokens
    from app.db import models

    user = (
        db_session.query(models.User).filter(models.User.email == "multitoken@example.com").first()
    )
    tokens_before = (
        db_session.query(models.EmailVerificationToken)
        .filter(
            models.EmailVerificationToken.user_id == user.id,
            models.EmailVerificationToken.verified_at.is_(None),
        )
        .count()
    )
    assert tokens_before == 1

    # Request second verification
    client.post("/auth/email/verify/request", headers=auth_headers(user_token))

    # Should still only have one active token (old one deleted)
    tokens_after = (
        db_session.query(models.EmailVerificationToken)
        .filter(
            models.EmailVerificationToken.user_id == user.id,
            models.EmailVerificationToken.verified_at.is_(None),
        )
        .count()
    )
    assert tokens_after == 1
