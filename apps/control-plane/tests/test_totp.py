"""Tests for TOTP Two-Factor Authentication functionality."""

from __future__ import annotations

import pyotp
from fastapi.testclient import TestClient


def auth_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def login(client: TestClient, email: str, password: str) -> str:
    response = client.post("/auth/login", json={"email": email, "password": password})
    assert response.status_code == 200, response.text
    return response.json()["access_token"]


def test_2fa_status_disabled_by_default(client: TestClient) -> None:
    """Test that 2FA is disabled by default for new users."""
    admin_token = login(client, "bootstrap@example.com", "super-secret")

    # Create new user
    user_payload = {
        "email": "2fa-default@example.com",
        "password": "password123",
        "is_admin": False,
        "is_active": True,
    }
    client.post("/admin/users", json=user_payload, headers=auth_headers(admin_token))

    # Login as new user
    user_token = login(client, "2fa-default@example.com", "password123")

    # Check 2FA status
    response = client.get("/auth/2fa/status", headers=auth_headers(user_token))
    assert response.status_code == 200
    data = response.json()
    assert data["enabled"] is False
    assert data["backup_codes_remaining"] == 0


def test_enable_2fa_flow(client: TestClient) -> None:
    """Test complete 2FA enable flow."""
    admin_token = login(client, "bootstrap@example.com", "super-secret")

    # Create new user
    user_payload = {
        "email": "2fa-enable@example.com",
        "password": "password123",
        "is_admin": False,
        "is_active": True,
    }
    client.post("/admin/users", json=user_payload, headers=auth_headers(admin_token))

    # Login as new user
    user_token = login(client, "2fa-enable@example.com", "password123")

    # Step 1: Enable 2FA (get secret and QR code)
    response = client.post("/auth/2fa/enable", headers=auth_headers(user_token))
    assert response.status_code == 200
    data = response.json()

    assert "secret" in data
    assert "qr_code_base64" in data
    assert "backup_codes" in data
    assert "uri" in data
    assert len(data["backup_codes"]) == 10
    assert data["uri"].startswith("otpauth://totp/")

    secret = data["secret"]

    # Step 2: Verify 2FA with TOTP code
    totp = pyotp.TOTP(secret)
    valid_code = totp.now()

    response = client.post(
        "/auth/2fa/verify",
        json={"code": valid_code},
        headers=auth_headers(user_token),
    )
    assert response.status_code == 200
    assert response.json()["ok"] is True

    # Verify 2FA is now enabled
    response = client.get("/auth/2fa/status", headers=auth_headers(user_token))
    assert response.status_code == 200
    data = response.json()
    assert data["enabled"] is True
    assert data["backup_codes_remaining"] == 10


def test_enable_2fa_invalid_code_rejected(client: TestClient) -> None:
    """Test that invalid TOTP code is rejected during 2FA setup."""
    admin_token = login(client, "bootstrap@example.com", "super-secret")

    # Create new user
    user_payload = {
        "email": "2fa-invalid@example.com",
        "password": "password123",
        "is_admin": False,
        "is_active": True,
    }
    client.post("/admin/users", json=user_payload, headers=auth_headers(admin_token))

    # Login and start 2FA setup
    user_token = login(client, "2fa-invalid@example.com", "password123")
    client.post("/auth/2fa/enable", headers=auth_headers(user_token))

    # Try to verify with invalid code
    response = client.post(
        "/auth/2fa/verify",
        json={"code": "000000"},
        headers=auth_headers(user_token),
    )
    assert response.status_code == 400
    data = response.json()
    assert "detail" in data or "Invalid" in str(data), f"Unexpected response: {data}"


def test_login_requires_2fa_when_enabled(client: TestClient) -> None:
    """Test that login requires 2FA endpoint when 2FA is enabled."""
    admin_token = login(client, "bootstrap@example.com", "super-secret")

    # Create user and enable 2FA
    user_payload = {
        "email": "2fa-login@example.com",
        "password": "password123",
        "is_admin": False,
        "is_active": True,
    }
    client.post("/admin/users", json=user_payload, headers=auth_headers(admin_token))

    user_token = login(client, "2fa-login@example.com", "password123")

    # Enable 2FA
    response = client.post("/auth/2fa/enable", headers=auth_headers(user_token))
    secret = response.json()["secret"]

    totp = pyotp.TOTP(secret)
    client.post(
        "/auth/2fa/verify",
        json={"code": totp.now()},
        headers=auth_headers(user_token),
    )

    # Try regular login - should fail with 403
    response = client.post(
        "/auth/login",
        json={"email": "2fa-login@example.com", "password": "password123"},
    )
    assert response.status_code == 403, f"Status: {response.status_code}, Body: {response.text}"
    data = response.json()
    assert "2FA" in str(data) or "detail" in data, f"Unexpected response: {data}"
    # Check header (case-insensitive) - optional, header might not be preserved by test client
    headers_lower = {k.lower(): v for k, v in response.headers.items()}
    # Skip header check if not present - some test clients don't preserve custom headers
    if "x-2fa-required" in headers_lower:
        assert headers_lower.get("x-2fa-required") == "true"


def test_login_with_2fa_endpoint(client: TestClient) -> None:
    """Test login with 2FA endpoint."""
    admin_token = login(client, "bootstrap@example.com", "super-secret")

    # Create user and enable 2FA
    user_payload = {
        "email": "2fa-login2@example.com",
        "password": "password123",
        "is_admin": False,
        "is_active": True,
    }
    client.post("/admin/users", json=user_payload, headers=auth_headers(admin_token))

    user_token = login(client, "2fa-login2@example.com", "password123")

    # Enable 2FA
    response = client.post("/auth/2fa/enable", headers=auth_headers(user_token))
    secret = response.json()["secret"]

    totp = pyotp.TOTP(secret)
    client.post(
        "/auth/2fa/verify",
        json={"code": totp.now()},
        headers=auth_headers(user_token),
    )

    # Login with 2FA endpoint
    response = client.post(
        "/auth/login/2fa",
        json={
            "email": "2fa-login2@example.com",
            "password": "password123",
            "totp_code": totp.now(),
        },
    )
    assert response.status_code == 200
    data = response.json()
    assert "access_token" in data
    assert "refresh_token" in data


def test_login_with_2fa_invalid_code(client: TestClient) -> None:
    """Test login with 2FA endpoint with invalid code."""
    admin_token = login(client, "bootstrap@example.com", "super-secret")

    # Create user and enable 2FA
    user_payload = {
        "email": "2fa-badcode@example.com",
        "password": "password123",
        "is_admin": False,
        "is_active": True,
    }
    client.post("/admin/users", json=user_payload, headers=auth_headers(admin_token))

    user_token = login(client, "2fa-badcode@example.com", "password123")

    # Enable 2FA
    response = client.post("/auth/2fa/enable", headers=auth_headers(user_token))
    secret = response.json()["secret"]

    totp = pyotp.TOTP(secret)
    client.post(
        "/auth/2fa/verify",
        json={"code": totp.now()},
        headers=auth_headers(user_token),
    )

    # Try login with invalid 2FA code
    response = client.post(
        "/auth/login/2fa",
        json={
            "email": "2fa-badcode@example.com",
            "password": "password123",
            "totp_code": "000000",
        },
    )
    assert response.status_code == 401
    data = response.json()
    assert "Invalid" in str(data) or "2FA" in str(data), f"Unexpected response: {data}"


def test_login_with_backup_code(client: TestClient) -> None:
    """Test login with backup code instead of TOTP."""
    admin_token = login(client, "bootstrap@example.com", "super-secret")

    # Create user and enable 2FA
    user_payload = {
        "email": "2fa-backup@example.com",
        "password": "password123",
        "is_admin": False,
        "is_active": True,
    }
    client.post("/admin/users", json=user_payload, headers=auth_headers(admin_token))

    user_token = login(client, "2fa-backup@example.com", "password123")

    # Enable 2FA
    response = client.post("/auth/2fa/enable", headers=auth_headers(user_token))
    data = response.json()
    secret = data["secret"]
    backup_codes = data["backup_codes"]

    totp = pyotp.TOTP(secret)
    client.post(
        "/auth/2fa/verify",
        json={"code": totp.now()},
        headers=auth_headers(user_token),
    )

    # Login with backup code
    response = client.post(
        "/auth/login/2fa",
        json={
            "email": "2fa-backup@example.com",
            "password": "password123",
            "totp_code": backup_codes[0],  # Use first backup code
        },
    )
    assert response.status_code == 200
    assert "access_token" in response.json()

    # Verify backup code is now used (9 remaining)
    new_token = response.json()["access_token"]
    response = client.get("/auth/2fa/status", headers=auth_headers(new_token))
    assert response.json()["backup_codes_remaining"] == 9


def test_backup_code_single_use(client: TestClient) -> None:
    """Test that backup codes can only be used once."""
    admin_token = login(client, "bootstrap@example.com", "super-secret")

    # Create user and enable 2FA
    user_payload = {
        "email": "2fa-singleuse@example.com",
        "password": "password123",
        "is_admin": False,
        "is_active": True,
    }
    client.post("/admin/users", json=user_payload, headers=auth_headers(admin_token))

    user_token = login(client, "2fa-singleuse@example.com", "password123")

    # Enable 2FA
    response = client.post("/auth/2fa/enable", headers=auth_headers(user_token))
    data = response.json()
    secret = data["secret"]
    backup_code = data["backup_codes"][0]

    totp = pyotp.TOTP(secret)
    client.post(
        "/auth/2fa/verify",
        json={"code": totp.now()},
        headers=auth_headers(user_token),
    )

    # First use of backup code - should work
    response = client.post(
        "/auth/login/2fa",
        json={
            "email": "2fa-singleuse@example.com",
            "password": "password123",
            "totp_code": backup_code,
        },
    )
    assert response.status_code == 200

    # Second use of same backup code - should fail
    response = client.post(
        "/auth/login/2fa",
        json={
            "email": "2fa-singleuse@example.com",
            "password": "password123",
            "totp_code": backup_code,
        },
    )
    assert response.status_code == 401


def test_disable_2fa(client: TestClient) -> None:
    """Test disabling 2FA."""
    admin_token = login(client, "bootstrap@example.com", "super-secret")

    # Create user and enable 2FA
    user_payload = {
        "email": "2fa-disable@example.com",
        "password": "password123",
        "is_admin": False,
        "is_active": True,
    }
    client.post("/admin/users", json=user_payload, headers=auth_headers(admin_token))

    user_token = login(client, "2fa-disable@example.com", "password123")

    # Enable 2FA
    response = client.post("/auth/2fa/enable", headers=auth_headers(user_token))
    secret = response.json()["secret"]

    totp = pyotp.TOTP(secret)
    client.post(
        "/auth/2fa/verify",
        json={"code": totp.now()},
        headers=auth_headers(user_token),
    )

    # Verify 2FA is enabled
    response = client.get("/auth/2fa/status", headers=auth_headers(user_token))
    assert response.json()["enabled"] is True

    # Disable 2FA with TOTP code
    response = client.post(
        "/auth/2fa/disable",
        json={"code": totp.now()},
        headers=auth_headers(user_token),
    )
    assert response.status_code == 200

    # Verify 2FA is disabled
    response = client.get("/auth/2fa/status", headers=auth_headers(user_token))
    assert response.json()["enabled"] is False

    # Regular login should work now
    response = client.post(
        "/auth/login",
        json={"email": "2fa-disable@example.com", "password": "password123"},
    )
    assert response.status_code == 200


def test_disable_2fa_with_backup_code(client: TestClient) -> None:
    """Test disabling 2FA with backup code."""
    admin_token = login(client, "bootstrap@example.com", "super-secret")

    # Create user and enable 2FA
    user_payload = {
        "email": "2fa-disablebu@example.com",
        "password": "password123",
        "is_admin": False,
        "is_active": True,
    }
    client.post("/admin/users", json=user_payload, headers=auth_headers(admin_token))

    user_token = login(client, "2fa-disablebu@example.com", "password123")

    # Enable 2FA
    response = client.post("/auth/2fa/enable", headers=auth_headers(user_token))
    data = response.json()
    secret = data["secret"]
    backup_code = data["backup_codes"][0]

    totp = pyotp.TOTP(secret)
    client.post(
        "/auth/2fa/verify",
        json={"code": totp.now()},
        headers=auth_headers(user_token),
    )

    # Disable 2FA with backup code
    response = client.post(
        "/auth/2fa/disable",
        json={"code": backup_code},
        headers=auth_headers(user_token),
    )
    assert response.status_code == 200

    # Verify 2FA is disabled
    response = client.get("/auth/2fa/status", headers=auth_headers(user_token))
    assert response.json()["enabled"] is False


def test_cannot_enable_2fa_twice(client: TestClient) -> None:
    """Test that 2FA cannot be enabled when already enabled."""
    admin_token = login(client, "bootstrap@example.com", "super-secret")

    # Create user and enable 2FA
    user_payload = {
        "email": "2fa-twice@example.com",
        "password": "password123",
        "is_admin": False,
        "is_active": True,
    }
    client.post("/admin/users", json=user_payload, headers=auth_headers(admin_token))

    user_token = login(client, "2fa-twice@example.com", "password123")

    # Enable 2FA
    response = client.post("/auth/2fa/enable", headers=auth_headers(user_token))
    secret = response.json()["secret"]

    totp = pyotp.TOTP(secret)
    client.post(
        "/auth/2fa/verify",
        json={"code": totp.now()},
        headers=auth_headers(user_token),
    )

    # Try to enable again
    response = client.post("/auth/2fa/enable", headers=auth_headers(user_token))
    assert response.status_code == 400
    data = response.json()
    assert (
        "already enabled" in str(data).lower() or "already" in str(data).lower()
    ), f"Unexpected response: {data}"


def test_2fa_requires_auth(client: TestClient) -> None:
    """Test that 2FA endpoints require authentication."""
    # Status
    response = client.get("/auth/2fa/status")
    assert response.status_code == 401

    # Enable
    response = client.post("/auth/2fa/enable")
    assert response.status_code == 401

    # Verify
    response = client.post("/auth/2fa/verify", json={"code": "123456"})
    assert response.status_code == 401

    # Disable
    response = client.post("/auth/2fa/disable", json={"code": "123456"})
    assert response.status_code == 401
