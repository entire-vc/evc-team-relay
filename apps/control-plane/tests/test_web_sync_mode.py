"""Tests for web_sync_mode feature."""

from __future__ import annotations

from fastapi.testclient import TestClient


def auth_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def login(client: TestClient, email: str, password: str) -> str:
    response = client.post("/auth/login", json={"email": email, "password": password})
    assert response.status_code == 200, response.text
    return response.json()["access_token"]


def test_create_share_default_sync_mode(client: TestClient) -> None:
    """Test that shares are created with default sync mode 'manual'."""
    token = login(client, "bootstrap@example.com", "super-secret")

    share_payload = {
        "kind": "doc",
        "path": "vault/test.md",
        "visibility": "private",
        "web_published": True,
    }
    response = client.post("/shares", json=share_payload, headers=auth_headers(token))
    assert response.status_code == 201, response.text

    data = response.json()
    assert data["web_sync_mode"] == "manual"


def test_create_share_with_auto_sync_mode(client: TestClient) -> None:
    """Test creating a share with auto sync mode."""
    token = login(client, "bootstrap@example.com", "super-secret")

    share_payload = {
        "kind": "doc",
        "path": "vault/auto-sync.md",
        "visibility": "private",
        "web_published": True,
        "web_sync_mode": "auto",
    }
    response = client.post("/shares", json=share_payload, headers=auth_headers(token))
    assert response.status_code == 201, response.text

    data = response.json()
    assert data["web_sync_mode"] == "auto"


def test_update_sync_mode_to_auto(client: TestClient) -> None:
    """Test updating sync mode from manual to auto."""
    token = login(client, "bootstrap@example.com", "super-secret")

    # Create share with default (manual) sync mode
    share_payload = {
        "kind": "doc",
        "path": "vault/update-test.md",
        "visibility": "private",
        "web_published": True,
    }
    create_response = client.post("/shares", json=share_payload, headers=auth_headers(token))
    assert create_response.status_code == 201
    share_id = create_response.json()["id"]

    # Update to auto sync mode
    update_payload = {"web_sync_mode": "auto"}
    update_response = client.patch(
        f"/shares/{share_id}", json=update_payload, headers=auth_headers(token)
    )
    assert update_response.status_code == 200, update_response.text

    data = update_response.json()
    assert data["web_sync_mode"] == "auto"

    # Verify by fetching the share
    get_response = client.get(f"/shares/{share_id}", headers=auth_headers(token))
    assert get_response.status_code == 200
    assert get_response.json()["web_sync_mode"] == "auto"


def test_update_sync_mode_to_manual(client: TestClient) -> None:
    """Test updating sync mode from auto to manual."""
    token = login(client, "bootstrap@example.com", "super-secret")

    # Create share with auto sync mode
    share_payload = {
        "kind": "doc",
        "path": "vault/auto-to-manual.md",
        "visibility": "private",
        "web_published": True,
        "web_sync_mode": "auto",
    }
    create_response = client.post("/shares", json=share_payload, headers=auth_headers(token))
    assert create_response.status_code == 201
    share_id = create_response.json()["id"]

    # Update to manual sync mode
    update_payload = {"web_sync_mode": "manual"}
    update_response = client.patch(
        f"/shares/{share_id}", json=update_payload, headers=auth_headers(token)
    )
    assert update_response.status_code == 200, update_response.text

    data = update_response.json()
    assert data["web_sync_mode"] == "manual"


def test_invalid_sync_mode_rejected(client: TestClient) -> None:
    """Test that invalid sync modes are rejected."""
    token = login(client, "bootstrap@example.com", "super-secret")

    # Try to create share with invalid sync mode
    share_payload = {
        "kind": "doc",
        "path": "vault/invalid-mode.md",
        "visibility": "private",
        "web_published": True,
        "web_sync_mode": "invalid",
    }
    response = client.post("/shares", json=share_payload, headers=auth_headers(token))
    assert response.status_code == 422  # Validation error


def test_invalid_sync_mode_update_rejected(client: TestClient) -> None:
    """Test that invalid sync modes are rejected on update."""
    token = login(client, "bootstrap@example.com", "super-secret")

    # Create valid share
    share_payload = {
        "kind": "doc",
        "path": "vault/valid-share.md",
        "visibility": "private",
        "web_published": True,
    }
    create_response = client.post("/shares", json=share_payload, headers=auth_headers(token))
    assert create_response.status_code == 201
    share_id = create_response.json()["id"]

    # Try to update with invalid sync mode
    update_payload = {"web_sync_mode": "realtime"}
    update_response = client.patch(
        f"/shares/{share_id}", json=update_payload, headers=auth_headers(token)
    )
    assert update_response.status_code == 422  # Validation error


def test_list_shares_includes_sync_mode(client: TestClient) -> None:
    """Test that listing shares includes sync mode information."""
    token = login(client, "bootstrap@example.com", "super-secret")

    # Create shares with different sync modes
    manual_share = {
        "kind": "doc",
        "path": "vault/manual.md",
        "visibility": "private",
        "web_published": True,
        "web_sync_mode": "manual",
    }
    client.post("/shares", json=manual_share, headers=auth_headers(token))

    auto_share = {
        "kind": "doc",
        "path": "vault/auto.md",
        "visibility": "private",
        "web_published": True,
        "web_sync_mode": "auto",
    }
    client.post("/shares", json=auto_share, headers=auth_headers(token))

    # List shares
    list_response = client.get("/shares", headers=auth_headers(token))
    assert list_response.status_code == 200

    shares = list_response.json()
    assert len(shares) == 2

    # Verify both shares have sync mode
    for share in shares:
        assert "web_sync_mode" in share
        assert share["web_sync_mode"] in ["manual", "auto"]
