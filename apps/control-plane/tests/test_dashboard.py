from __future__ import annotations

from fastapi.testclient import TestClient


def auth_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def login(client: TestClient, email: str, password: str) -> str:
    response = client.post("/auth/login", json={"email": email, "password": password})
    assert response.status_code == 200, response.text
    return response.json()["access_token"]


def test_admin_dashboard_stats(client: TestClient) -> None:
    """Test admin dashboard statistics endpoint."""
    admin_token = login(client, "bootstrap@example.com", "super-secret")

    # Create some test data
    user_payload = {
        "email": "dashuser@example.com",
        "password": "dash-pass",
        "is_admin": False,
        "is_active": True,
    }
    client.post("/admin/users", json=user_payload, headers=auth_headers(admin_token))

    share_payload = {"kind": "doc", "path": "vault/dashboard.md", "visibility": "private"}
    client.post("/shares", json=share_payload, headers=auth_headers(admin_token))

    # Get admin stats
    response = client.get("/dashboard/admin/stats", headers=auth_headers(admin_token))
    assert response.status_code == 200
    data = response.json()

    # Verify response structure
    assert "total_users" in data
    assert "active_users" in data
    assert "admin_users" in data
    assert "total_shares" in data
    assert "shares_by_kind" in data
    assert "shares_by_visibility" in data
    assert "total_share_members" in data
    assert "recent_logins_count" in data
    assert "recent_shares_count" in data

    # Verify counts are reasonable
    assert data["total_users"] >= 2  # At least bootstrap + dashuser
    assert data["active_users"] >= 2
    assert data["admin_users"] >= 1
    assert data["total_shares"] >= 1
    assert isinstance(data["shares_by_kind"], dict)
    assert isinstance(data["shares_by_visibility"], dict)


def test_admin_dashboard_requires_admin(client: TestClient) -> None:
    """Test that admin dashboard requires admin privileges."""
    admin_token = login(client, "bootstrap@example.com", "super-secret")

    # Create regular user
    user_payload = {
        "email": "regular@example.com",
        "password": "regular-pass",
        "is_admin": False,
        "is_active": True,
    }
    client.post("/admin/users", json=user_payload, headers=auth_headers(admin_token))

    # Login as regular user
    user_token = login(client, "regular@example.com", "regular-pass")

    # Try to access admin dashboard
    response = client.get("/dashboard/admin/stats", headers=auth_headers(user_token))
    assert response.status_code == 403  # Forbidden


def test_user_dashboard_stats(client: TestClient) -> None:
    """Test user dashboard statistics endpoint."""
    admin_token = login(client, "bootstrap@example.com", "super-secret")

    # Create test user
    user_payload = {
        "email": "userstat@example.com",
        "password": "user-pass",
        "is_admin": False,
        "is_active": True,
    }
    user_resp = client.post("/admin/users", json=user_payload, headers=auth_headers(admin_token))
    assert user_resp.status_code == 201

    # Login as test user
    user_token = login(client, "userstat@example.com", "user-pass")

    # Create some shares as user
    share1 = {"kind": "doc", "path": "vault/userdoc.md", "visibility": "private"}
    client.post("/shares", json=share1, headers=auth_headers(user_token))

    share2 = {"kind": "folder", "path": "vault/userfolder", "visibility": "public"}
    client.post("/shares", json=share2, headers=auth_headers(user_token))

    # Get user stats
    response = client.get("/dashboard/user/stats", headers=auth_headers(user_token))
    assert response.status_code == 200
    data = response.json()

    # Verify response structure
    assert "owned_shares_count" in data
    assert "member_shares_count" in data
    assert "shares_by_kind" in data
    assert "total_share_members" in data
    assert "recent_activity_count" in data

    # Verify counts
    assert data["owned_shares_count"] == 2
    assert data["member_shares_count"] == 0  # User is not a member of other shares
    assert isinstance(data["shares_by_kind"], dict)
    assert data["recent_activity_count"] >= 2  # At least login + 2 share creations


def test_user_dashboard_with_memberships(client: TestClient) -> None:
    """Test user dashboard with share memberships."""
    admin_token = login(client, "bootstrap@example.com", "super-secret")

    # Create two users
    owner_payload = {
        "email": "owner@example.com",
        "password": "owner-pass",
        "is_admin": False,
        "is_active": True,
    }
    owner_resp = client.post("/admin/users", json=owner_payload, headers=auth_headers(admin_token))
    owner_resp.json()["id"]

    member_payload = {
        "email": "member@example.com",
        "password": "member-pass",
        "is_admin": False,
        "is_active": True,
    }
    member_resp = client.post(
        "/admin/users", json=member_payload, headers=auth_headers(admin_token)
    )
    member_id = member_resp.json()["id"]

    # Login as owner
    owner_token = login(client, "owner@example.com", "owner-pass")

    # Create share as owner
    share_payload = {"kind": "doc", "path": "vault/shared.md", "visibility": "private"}
    share_resp = client.post("/shares", json=share_payload, headers=auth_headers(owner_token))
    share_id = share_resp.json()["id"]

    # Add member to share
    client.post(
        f"/shares/{share_id}/members",
        json={"user_id": member_id, "role": "viewer"},
        headers=auth_headers(owner_token),
    )

    # Login as member and check stats
    member_token = login(client, "member@example.com", "member-pass")
    response = client.get("/dashboard/user/stats", headers=auth_headers(member_token))
    assert response.status_code == 200
    data = response.json()

    assert data["owned_shares_count"] == 0  # Member doesn't own any shares
    assert data["member_shares_count"] == 1  # Member is part of 1 share

    # Check owner stats
    owner_response = client.get("/dashboard/user/stats", headers=auth_headers(owner_token))
    assert owner_response.status_code == 200
    owner_data = owner_response.json()

    assert owner_data["owned_shares_count"] == 1
    assert owner_data["total_share_members"] == 1  # 1 member in owner's share


def test_user_dashboard_requires_auth(client: TestClient) -> None:
    """Test that user dashboard requires authentication."""
    response = client.get("/dashboard/user/stats")
    assert response.status_code == 401  # Unauthorized
