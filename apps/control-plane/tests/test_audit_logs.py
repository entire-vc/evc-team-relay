from __future__ import annotations

from fastapi.testclient import TestClient


def auth_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def login(client: TestClient, email: str, password: str) -> str:
    response = client.post("/auth/login", json={"email": email, "password": password})
    assert response.status_code == 200, response.text
    return response.json()["access_token"]


def test_user_audit_logs(client: TestClient) -> None:
    """Test audit logs for user operations"""
    admin_token = login(client, "bootstrap@example.com", "super-secret")

    # Create user
    user_payload = {
        "email": "testuser@example.com",
        "password": "test-pass",
        "is_admin": False,
        "is_active": True,
    }
    create_resp = client.post("/admin/users", json=user_payload, headers=auth_headers(admin_token))
    assert create_resp.status_code == 201
    user_id = create_resp.json()["id"]

    # Update user
    update_resp = client.patch(
        f"/admin/users/{user_id}", json={"is_active": False}, headers=auth_headers(admin_token)
    )
    assert update_resp.status_code == 200

    # Delete user
    delete_resp = client.delete(f"/admin/users/{user_id}", headers=auth_headers(admin_token))
    assert delete_resp.status_code == 204

    # Check audit logs
    logs_resp = client.get("/admin/audit-logs", headers=auth_headers(admin_token))
    assert logs_resp.status_code == 200
    logs = logs_resp.json()

    # Find logs for our actions
    user_created = [
        log for log in logs if log["action"] == "user_created" and log["target_user_id"] == user_id
    ]
    user_updated = [
        log for log in logs if log["action"] == "user_updated" and log["target_user_id"] == user_id
    ]
    user_deleted = [
        log for log in logs if log["action"] == "user_deleted" and log["target_user_id"] == user_id
    ]

    assert len(user_created) == 1
    assert user_created[0]["details"]["email"] == "testuser@example.com"

    assert len(user_updated) == 1
    assert user_updated[0]["details"]["changes"]["is_active"]["new"] is False

    assert len(user_deleted) == 1
    assert user_deleted[0]["details"]["email"] == "testuser@example.com"


def test_share_audit_logs(client: TestClient) -> None:
    """Test audit logs for share operations"""
    admin_token = login(client, "bootstrap@example.com", "super-secret")

    # Create share
    share_payload = {"kind": "doc", "path": "vault/test.md", "visibility": "private"}
    create_resp = client.post("/shares", json=share_payload, headers=auth_headers(admin_token))
    assert create_resp.status_code == 201
    share_id = create_resp.json()["id"]

    # Update share
    update_resp = client.patch(
        f"/shares/{share_id}", json={"visibility": "public"}, headers=auth_headers(admin_token)
    )
    assert update_resp.status_code == 200

    # Delete share
    delete_resp = client.delete(f"/shares/{share_id}", headers=auth_headers(admin_token))
    assert delete_resp.status_code == 204

    # Check audit logs
    logs_resp = client.get("/admin/audit-logs", headers=auth_headers(admin_token))
    assert logs_resp.status_code == 200
    logs = logs_resp.json()

    # Find logs for our actions
    share_created = [
        log
        for log in logs
        if log["action"] == "share_created" and log["target_share_id"] == share_id
    ]
    share_updated = [
        log
        for log in logs
        if log["action"] == "share_updated" and log["target_share_id"] == share_id
    ]
    share_deleted = [
        log
        for log in logs
        if log["action"] == "share_deleted" and log["target_share_id"] == share_id
    ]

    assert len(share_created) == 1
    assert share_created[0]["details"]["path"] == "vault/test.md"

    assert len(share_updated) == 1
    assert share_updated[0]["details"]["changes"]["visibility"]["new"] == "public"

    assert len(share_deleted) == 1


def test_member_audit_logs(client: TestClient) -> None:
    """Test audit logs for share member operations"""
    admin_token = login(client, "bootstrap@example.com", "super-secret")

    # Create user
    user_payload = {
        "email": "member@example.com",
        "password": "member-pass",
        "is_admin": False,
        "is_active": True,
    }
    user_resp = client.post("/admin/users", json=user_payload, headers=auth_headers(admin_token))
    assert user_resp.status_code == 201
    user_id = user_resp.json()["id"]

    # Create share
    share_payload = {"kind": "doc", "path": "vault/shared.md", "visibility": "private"}
    share_resp = client.post("/shares", json=share_payload, headers=auth_headers(admin_token))
    assert share_resp.status_code == 201
    share_id = share_resp.json()["id"]

    # Add member
    add_member_resp = client.post(
        f"/shares/{share_id}/members",
        json={"user_id": user_id, "role": "viewer"},
        headers=auth_headers(admin_token),
    )
    assert add_member_resp.status_code == 201

    # Update member role
    update_member_resp = client.patch(
        f"/shares/{share_id}/members/{user_id}",
        json={"role": "editor"},
        headers=auth_headers(admin_token),
    )
    assert update_member_resp.status_code == 200

    # Remove member
    remove_member_resp = client.delete(
        f"/shares/{share_id}/members/{user_id}", headers=auth_headers(admin_token)
    )
    assert remove_member_resp.status_code == 204

    # Check audit logs
    logs_resp = client.get("/admin/audit-logs", headers=auth_headers(admin_token))
    assert logs_resp.status_code == 200
    logs = logs_resp.json()

    # Find logs for member operations
    member_added = [
        log
        for log in logs
        if log["action"] == "share_member_added"
        and log["target_share_id"] == share_id
        and log["target_user_id"] == user_id
    ]
    member_updated = [
        log
        for log in logs
        if log["action"] == "share_member_updated"
        and log["target_share_id"] == share_id
        and log["target_user_id"] == user_id
    ]
    member_removed = [
        log
        for log in logs
        if log["action"] == "share_member_removed"
        and log["target_share_id"] == share_id
        and log["target_user_id"] == user_id
    ]

    assert len(member_added) == 1
    assert member_added[0]["details"]["role"] == "viewer"

    assert len(member_updated) >= 1  # At least one update
    latest_update = member_updated[0]  # Most recent first
    assert latest_update["details"]["role"]["new"] == "editor"

    assert len(member_removed) == 1
    assert member_removed[0]["details"]["role"] == "editor"


def test_login_logout_audit_logs(client: TestClient) -> None:
    """Test audit logs for login/logout operations"""
    admin_token = login(client, "bootstrap@example.com", "super-secret")

    # Logout
    logout_resp = client.post("/auth/logout", headers=auth_headers(admin_token))
    assert logout_resp.status_code == 200

    # Login again to check logs
    admin_token = login(client, "bootstrap@example.com", "super-secret")

    # Check audit logs
    logs_resp = client.get("/admin/audit-logs", headers=auth_headers(admin_token))
    assert logs_resp.status_code == 200
    logs = logs_resp.json()

    # Find login/logout logs
    logins = [log for log in logs if log["action"] == "user_login"]
    logouts = [log for log in logs if log["action"] == "user_logout"]

    assert len(logins) >= 2  # At least 2 logins in this test
    assert len(logouts) >= 1  # At least 1 logout


def test_audit_log_filters(client: TestClient) -> None:
    """Test audit log filtering"""
    admin_token = login(client, "bootstrap@example.com", "super-secret")

    # Create test user
    user_payload = {
        "email": "filter@example.com",
        "password": "filter-pass",
        "is_admin": False,
        "is_active": True,
    }
    user_resp = client.post("/admin/users", json=user_payload, headers=auth_headers(admin_token))
    assert user_resp.status_code == 201
    user_id = user_resp.json()["id"]

    # Filter by action
    logs_resp = client.get(
        "/admin/audit-logs?action=user_created", headers=auth_headers(admin_token)
    )
    assert logs_resp.status_code == 200
    logs = logs_resp.json()
    assert all(log["action"] == "user_created" for log in logs)

    # Filter by target_user_id
    logs_resp = client.get(
        f"/admin/audit-logs?target_user_id={user_id}", headers=auth_headers(admin_token)
    )
    assert logs_resp.status_code == 200
    logs = logs_resp.json()
    assert all(log["target_user_id"] == user_id for log in logs)

    # Test pagination
    logs_resp = client.get("/admin/audit-logs?skip=0&limit=2", headers=auth_headers(admin_token))
    assert logs_resp.status_code == 200
    logs = logs_resp.json()
    assert len(logs) <= 2


def test_audit_log_non_admin_access(client: TestClient) -> None:
    """Test that non-admin users cannot access audit logs"""
    admin_token = login(client, "bootstrap@example.com", "super-secret")

    # Create regular user
    user_payload = {
        "email": "regular@example.com",
        "password": "regular-pass",
        "is_admin": False,
        "is_active": True,
    }
    user_resp = client.post("/admin/users", json=user_payload, headers=auth_headers(admin_token))
    assert user_resp.status_code == 201

    # Login as regular user
    user_token = login(client, "regular@example.com", "regular-pass")

    # Try to access audit logs
    logs_resp = client.get("/admin/audit-logs", headers=auth_headers(user_token))
    assert logs_resp.status_code == 403  # Forbidden
