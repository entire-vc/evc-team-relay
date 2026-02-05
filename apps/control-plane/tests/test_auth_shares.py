from __future__ import annotations

from fastapi.testclient import TestClient


def auth_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def login(client: TestClient, email: str, password: str) -> str:
    response = client.post("/auth/login", json={"email": email, "password": password})
    assert response.status_code == 200, response.text
    return response.json()["access_token"]


def test_bootstrap_admin_login_and_me(client: TestClient) -> None:
    token = login(client, "bootstrap@example.com", "super-secret")
    me_response = client.get("/auth/me", headers=auth_headers(token))
    assert me_response.status_code == 200
    assert me_response.json()["email"] == "bootstrap@example.com"


def test_share_token_permissions(client: TestClient) -> None:
    admin_token = login(client, "bootstrap@example.com", "super-secret")

    share_payload = {
        "kind": "doc",
        "path": "vault/wiki.md",
        "visibility": "protected",
        "password": "relay-pass",
    }
    share_resp = client.post("/shares", json=share_payload, headers=auth_headers(admin_token))
    assert share_resp.status_code == 201, share_resp.text
    share_id = share_resp.json()["id"]

    token_request = {"share_id": share_id, "doc_id": "vault/wiki.md", "mode": "read"}
    missing_password = client.post("/tokens/relay", json=token_request)
    assert missing_password.status_code == 403

    token_request["password"] = "relay-pass"
    read_token = client.post("/tokens/relay", json=token_request)
    assert read_token.status_code == 200, read_token.text
    body = read_token.json()
    assert body["relay_url"].startswith("wss://")
    assert "token" in body and body["token"]

    new_user_payload = {
        "email": "writer@example.com",
        "password": "writer-pass",
        "is_admin": False,
        "is_active": True,
    }
    create_user = client.post(
        "/admin/users", json=new_user_payload, headers=auth_headers(admin_token)
    )
    assert create_user.status_code == 201, create_user.text

    user_token = login(client, "writer@example.com", "writer-pass")
    write_req = {"share_id": share_id, "doc_id": "vault/wiki.md", "mode": "write"}
    write_resp = client.post("/tokens/relay", json=write_req, headers=auth_headers(user_token))
    assert write_resp.status_code == 403


def test_list_shares(client: TestClient) -> None:
    """Test GET /shares endpoint with filters"""
    admin_token = login(client, "bootstrap@example.com", "super-secret")

    # Create test shares
    doc_share = client.post(
        "/shares",
        json={"kind": "doc", "path": "vault/doc1.md", "visibility": "private"},
        headers=auth_headers(admin_token),
    )
    assert doc_share.status_code == 201
    doc_share_id = doc_share.json()["id"]

    folder_share = client.post(
        "/shares",
        json={"kind": "folder", "path": "vault/folder1", "visibility": "public"},
        headers=auth_headers(admin_token),
    )
    assert folder_share.status_code == 201

    # Create another user
    user_payload = {
        "email": "viewer@example.com",
        "password": "viewer-pass",
        "is_admin": False,
        "is_active": True,
    }
    create_user = client.post("/admin/users", json=user_payload, headers=auth_headers(admin_token))
    assert create_user.status_code == 201
    user_id = create_user.json()["id"]

    # Add user as viewer to doc share
    add_member = client.post(
        f"/shares/{doc_share_id}/members",
        json={"user_id": user_id, "role": "viewer"},
        headers=auth_headers(admin_token),
    )
    assert add_member.status_code == 201

    # Test: Admin sees all owned shares
    admin_list = client.get("/shares", headers=auth_headers(admin_token))
    assert admin_list.status_code == 200
    admin_shares = admin_list.json()
    assert len(admin_shares) == 2
    assert all(s["is_owner"] is True for s in admin_shares)
    assert all(s["user_role"] is None for s in admin_shares)

    # Test: Filter by kind
    doc_only = client.get("/shares?kind=doc", headers=auth_headers(admin_token))
    assert doc_only.status_code == 200
    assert len(doc_only.json()) == 1
    assert doc_only.json()[0]["kind"] == "doc"

    folder_only = client.get("/shares?kind=folder", headers=auth_headers(admin_token))
    assert folder_only.status_code == 200
    assert len(folder_only.json()) == 1
    assert folder_only.json()[0]["kind"] == "folder"

    # Test: User sees only member shares
    user_token = login(client, "viewer@example.com", "viewer-pass")
    user_list = client.get("/shares", headers=auth_headers(user_token))
    assert user_list.status_code == 200
    user_shares = user_list.json()
    assert len(user_shares) == 1
    assert user_shares[0]["id"] == doc_share_id
    assert user_shares[0]["is_owner"] is False
    assert user_shares[0]["user_role"] == "viewer"

    # Test: member_only filter (should return same result for user)
    member_only = client.get("/shares?member_only=true", headers=auth_headers(user_token))
    assert member_only.status_code == 200
    assert len(member_only.json()) == 1

    # Test: owned_only filter (should return empty for user)
    owned_only = client.get("/shares?owned_only=true", headers=auth_headers(user_token))
    assert owned_only.status_code == 200
    assert len(owned_only.json()) == 0

    # Test: Pagination
    paginated = client.get("/shares?skip=0&limit=1", headers=auth_headers(admin_token))
    assert paginated.status_code == 200
    assert len(paginated.json()) == 1
