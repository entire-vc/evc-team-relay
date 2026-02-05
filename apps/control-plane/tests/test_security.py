"""Tests for security features: rate limiting and public key distribution."""

from __future__ import annotations

from fastapi.testclient import TestClient


def auth_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def login(client: TestClient, email: str, password: str) -> str:
    response = client.post("/auth/login", json={"email": email, "password": password})
    assert response.status_code == 200, response.text
    return response.json()["access_token"]


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
