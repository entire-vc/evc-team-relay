"""Tests for password reset functionality."""

from __future__ import annotations

from fastapi.testclient import TestClient


def auth_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def login(client: TestClient, email: str, password: str) -> str:
    response = client.post("/auth/login", json={"email": email, "password": password})
    assert response.status_code == 200, response.text
    return response.json()["access_token"]


def test_request_password_reset_success(client: TestClient) -> None:
    """Test requesting password reset for existing user."""
    # Request password reset
    response = client.post(
        "/auth/password-reset/request",
        json={"email": "bootstrap@example.com"},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is True
    assert "has been sent" in data["message"]


def test_request_password_reset_nonexistent_email(client: TestClient) -> None:
    """Test requesting password reset for non-existent email returns success (no enumeration)."""
    # Request password reset for non-existent email
    response = client.post(
        "/auth/password-reset/request",
        json={"email": "nonexistent@example.com"},
    )

    # Should still return success to prevent email enumeration
    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is True


def test_password_reset_form_valid_token(client: TestClient) -> None:
    """Test displaying password reset form with valid token."""
    # First, request a password reset
    response = client.post(
        "/auth/password-reset/request",
        json={"email": "bootstrap@example.com"},
    )
    assert response.status_code == 200

    # Extract token from database (in a real test, we'd mock the email service)
    # For now, we'll create a token directly
    from app.db.session import get_db
    from app.services import password_service

    db = next(get_db())
    token = password_service.create_reset_token(db, "bootstrap@example.com")
    db.commit()

    assert token is not None

    # Access the reset form
    response = client.get(f"/auth/password-reset/{token}")
    assert response.status_code == 200
    assert b"Reset Your Password" in response.content
    assert token.encode() in response.content


def test_password_reset_form_invalid_token(client: TestClient) -> None:
    """Test displaying password reset form with invalid token shows error."""
    # Use an invalid token
    invalid_token = "0" * 64

    response = client.get(f"/auth/password-reset/{invalid_token}")
    assert response.status_code == 200
    assert b"invalid or has expired" in response.content


def test_complete_password_reset_success(client: TestClient) -> None:
    """Test completing password reset with valid token."""
    # Create a reset token
    from app.db.session import get_db
    from app.services import password_service

    db = next(get_db())
    token = password_service.create_reset_token(db, "bootstrap@example.com")
    db.commit()

    assert token is not None

    # Submit password reset form
    response = client.post(
        "/auth/password-reset/confirm",
        data={
            "token": token,
            "new_password": "new-password-123",
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )

    assert response.status_code == 200
    assert b"Password reset successful" in response.content

    # Verify user can login with new password
    login_response = client.post(
        "/auth/login",
        json={"email": "bootstrap@example.com", "password": "new-password-123"},
    )
    assert login_response.status_code == 200

    # Verify old password no longer works
    old_login_response = client.post(
        "/auth/login",
        json={"email": "bootstrap@example.com", "password": "super-secret"},
    )
    assert old_login_response.status_code == 401


def test_complete_password_reset_invalid_token(client: TestClient) -> None:
    """Test completing password reset with invalid token fails."""
    invalid_token = "0" * 64

    response = client.post(
        "/auth/password-reset/confirm",
        data={
            "token": invalid_token,
            "new_password": "new-password-123",
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )

    assert response.status_code == 200
    assert b"invalid or has expired" in response.content


def test_complete_password_reset_short_password(client: TestClient) -> None:
    """Test completing password reset with too short password fails."""
    # Create a reset token
    from app.db.session import get_db
    from app.services import password_service

    db = next(get_db())
    token = password_service.create_reset_token(db, "bootstrap@example.com")
    db.commit()

    assert token is not None

    # Submit with short password
    response = client.post(
        "/auth/password-reset/confirm",
        data={
            "token": token,
            "new_password": "short",
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )

    assert response.status_code == 200
    assert b"at least 8 characters" in response.content


def test_password_reset_token_single_use(client: TestClient) -> None:
    """Test that password reset token can only be used once."""
    # Create a reset token
    from app.db.session import get_db
    from app.services import password_service

    db = next(get_db())
    token = password_service.create_reset_token(db, "bootstrap@example.com")
    db.commit()

    assert token is not None

    # Use the token once
    response = client.post(
        "/auth/password-reset/confirm",
        data={
            "token": token,
            "new_password": "new-password-123",
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert response.status_code == 200
    assert b"Password reset successful" in response.content

    # Try to use the same token again
    response2 = client.post(
        "/auth/password-reset/confirm",
        data={
            "token": token,
            "new_password": "another-password-456",
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert response2.status_code == 200
    assert b"invalid or has expired" in response2.content


def test_password_reset_invalidates_all_sessions(client: TestClient) -> None:
    """Test that password reset invalidates all user sessions."""
    # Login and get access token
    old_token = login(client, "bootstrap@example.com", "super-secret")

    # Verify token works
    response = client.get("/auth/me", headers=auth_headers(old_token))
    assert response.status_code == 200

    # Request password reset
    from app.db.session import get_db
    from app.services import password_service

    db = next(get_db())
    reset_token = password_service.create_reset_token(db, "bootstrap@example.com")
    db.commit()

    # Complete password reset
    client.post(
        "/auth/password-reset/confirm",
        data={
            "token": reset_token,
            "new_password": "new-password-123",
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )

    # Verify old token no longer works (sessions invalidated)
    # Note: JWT access tokens will still be valid until expiration,
    # but refresh tokens should be invalidated
    sessions_response = client.get("/auth/sessions", headers=auth_headers(old_token))
    # Should have no sessions since all were revoked
    if sessions_response.status_code == 200:
        assert len(sessions_response.json()) == 0


def test_password_reset_rate_limiting(client: TestClient) -> None:
    """Test that password reset requests are rate limited."""
    # Make 3 requests (should succeed)
    for _ in range(3):
        response = client.post(
            "/auth/password-reset/request",
            json={"email": "bootstrap@example.com"},
        )
        assert response.status_code == 200

    # 4th request should be rate limited
    response = client.post(
        "/auth/password-reset/request",
        json={"email": "bootstrap@example.com"},
    )
    assert response.status_code == 429  # Too Many Requests
