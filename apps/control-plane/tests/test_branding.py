"""Tests for instance branding functionality."""

from __future__ import annotations

from fastapi.testclient import TestClient


def auth_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def login(client: TestClient, email: str, password: str) -> str:
    response = client.post("/auth/login", json={"email": email, "password": password})
    assert response.status_code == 200, response.text
    return response.json()["access_token"]


def test_server_info_includes_branding(client):
    """Test that GET /server/info includes branding information."""
    response = client.get("/server/info")
    assert response.status_code == 200

    data = response.json()
    assert "branding" in data

    # Check branding structure
    branding = data["branding"]
    assert "name" in branding
    assert "logo_url" in branding
    assert "favicon_url" in branding

    # Default values should be present
    assert branding["name"] == "Relay Server"
    assert branding["logo_url"] == "/static/img/evc-ava.svg"
    assert branding["favicon_url"] == "/static/img/evc-ava.svg"


def test_server_info_branding_no_auth_required(client):
    """Test that branding information is available without authentication."""
    response = client.get("/server/info")
    assert response.status_code == 200

    data = response.json()
    assert "branding" in data


def test_admin_get_branding_requires_admin(client, test_user):
    """Test that GET /admin/settings/branding requires admin auth."""
    # Login as regular user
    user_token = login(client, "testuser@example.com", "test123456")
    response = client.get("/admin/settings/branding", headers=auth_headers(user_token))
    assert response.status_code == 403  # Regular user, not admin


def test_admin_get_branding_success(client):
    """Test that admin can get branding settings."""
    admin_token = login(client, "bootstrap@example.com", "super-secret")
    response = client.get("/admin/settings/branding", headers=auth_headers(admin_token))
    assert response.status_code == 200

    data = response.json()
    assert data["name"] == "Relay Server"
    assert data["logo_url"] == "/static/img/evc-ava.svg"
    assert data["favicon_url"] == "/static/img/evc-ava.svg"


def test_admin_update_branding_requires_admin(client, test_user):
    """Test that PATCH /admin/settings/branding requires admin auth."""
    # Login as regular user
    user_token = login(client, "testuser@example.com", "test123456")
    payload = {
        "name": "My Company Relay",
        "logo_url": "https://example.com/logo.png",
        "favicon_url": "https://example.com/favicon.ico",
    }
    response = client.patch(
        "/admin/settings/branding", json=payload, headers=auth_headers(user_token)
    )
    assert response.status_code == 403  # Regular user, not admin


def test_admin_update_branding_success(client):
    """Test that admin can update branding settings."""
    admin_token = login(client, "bootstrap@example.com", "super-secret")
    payload = {
        "name": "My Company Relay",
        "logo_url": "https://example.com/logo.png",
        "favicon_url": "https://example.com/favicon.ico",
    }
    response = client.patch(
        "/admin/settings/branding", json=payload, headers=auth_headers(admin_token)
    )
    assert response.status_code == 200

    data = response.json()
    assert data["name"] == "My Company Relay"
    assert data["logo_url"] == "https://example.com/logo.png"
    assert data["favicon_url"] == "https://example.com/favicon.ico"

    # Verify the change persists via GET
    response = client.get("/admin/settings/branding", headers=auth_headers(admin_token))
    assert response.status_code == 200
    data = response.json()
    assert data["name"] == "My Company Relay"
    assert data["logo_url"] == "https://example.com/logo.png"
    assert data["favicon_url"] == "https://example.com/favicon.ico"


def test_admin_update_branding_reflected_in_server_info(client):
    """Test that branding updates are reflected in /server/info."""
    admin_token = login(client, "bootstrap@example.com", "super-secret")
    # Update branding
    payload = {
        "name": "Custom Instance",
        "logo_url": "/custom-logo.svg",
        "favicon_url": "/custom-favicon.ico",
    }
    response = client.patch(
        "/admin/settings/branding", json=payload, headers=auth_headers(admin_token)
    )
    assert response.status_code == 200

    # Check server info
    response = client.get("/server/info")
    assert response.status_code == 200

    data = response.json()
    branding = data["branding"]
    assert branding["name"] == "Custom Instance"
    assert branding["logo_url"] == "/custom-logo.svg"
    assert branding["favicon_url"] == "/custom-favicon.ico"


def test_admin_update_branding_validation_name_required(client):
    """Test that name field is required."""
    admin_token = login(client, "bootstrap@example.com", "super-secret")
    payload = {
        "name": "",  # Empty name
        "logo_url": "https://example.com/logo.png",
        "favicon_url": "https://example.com/favicon.ico",
    }
    response = client.patch(
        "/admin/settings/branding", json=payload, headers=auth_headers(admin_token)
    )
    assert response.status_code == 422


def test_admin_update_branding_validation_logo_url_required(client):
    """Test that logo_url field is required."""
    admin_token = login(client, "bootstrap@example.com", "super-secret")
    payload = {
        "name": "My Company",
        "logo_url": "",  # Empty URL
        "favicon_url": "https://example.com/favicon.ico",
    }
    response = client.patch(
        "/admin/settings/branding", json=payload, headers=auth_headers(admin_token)
    )
    assert response.status_code == 422


def test_admin_update_branding_validation_favicon_url_required(client):
    """Test that favicon_url field is required."""
    admin_token = login(client, "bootstrap@example.com", "super-secret")
    payload = {
        "name": "My Company",
        "logo_url": "https://example.com/logo.png",
        "favicon_url": "",  # Empty URL
    }
    response = client.patch(
        "/admin/settings/branding", json=payload, headers=auth_headers(admin_token)
    )
    assert response.status_code == 422


def test_admin_update_branding_validation_name_too_long(client):
    """Test that name field has max length validation."""
    admin_token = login(client, "bootstrap@example.com", "super-secret")
    payload = {
        "name": "A" * 101,  # Exceeds 100 character limit
        "logo_url": "https://example.com/logo.png",
        "favicon_url": "https://example.com/favicon.ico",
    }
    response = client.patch(
        "/admin/settings/branding", json=payload, headers=auth_headers(admin_token)
    )
    assert response.status_code == 422


def test_admin_update_branding_validation_url_too_long(client):
    """Test that URL fields have max length validation."""
    admin_token = login(client, "bootstrap@example.com", "super-secret")
    payload = {
        "name": "My Company",
        "logo_url": "https://" + "a" * 2041,  # Exceeds 2048 character limit
        "favicon_url": "https://example.com/favicon.ico",
    }
    response = client.patch(
        "/admin/settings/branding", json=payload, headers=auth_headers(admin_token)
    )
    assert response.status_code == 422


def test_admin_update_branding_custom_code(client):
    """Test that admin can set custom head/body code for analytics injection."""
    admin_token = login(client, "bootstrap@example.com", "super-secret")
    payload = {
        "name": "Analytics Test",
        "logo_url": "/logo.svg",
        "favicon_url": "/favicon.ico",
        "custom_head_code": '<script>console.log("head")</script>',
        "custom_body_code": '<script>console.log("body")</script>',
    }
    response = client.patch(
        "/admin/settings/branding", json=payload, headers=auth_headers(admin_token)
    )
    assert response.status_code == 200
    data = response.json()
    assert data["custom_head_code"] == '<script>console.log("head")</script>'
    assert data["custom_body_code"] == '<script>console.log("body")</script>'

    # Verify via GET
    response = client.get("/admin/settings/branding", headers=auth_headers(admin_token))
    assert response.status_code == 200
    data = response.json()
    assert data["custom_head_code"] == '<script>console.log("head")</script>'
    assert data["custom_body_code"] == '<script>console.log("body")</script>'

    # Verify reflected in /server/info
    response = client.get("/server/info")
    data = response.json()
    assert data["branding"]["custom_head_code"] == '<script>console.log("head")</script>'
    assert data["branding"]["custom_body_code"] == '<script>console.log("body")</script>'


def test_admin_update_branding_custom_code_defaults_empty(client):
    """Test that custom code fields default to empty string."""
    admin_token = login(client, "bootstrap@example.com", "super-secret")
    # Send without custom_code fields
    payload = {
        "name": "No Code Test",
        "logo_url": "/logo.svg",
        "favicon_url": "/favicon.ico",
    }
    response = client.patch(
        "/admin/settings/branding", json=payload, headers=auth_headers(admin_token)
    )
    assert response.status_code == 200
    data = response.json()
    assert data["custom_head_code"] == ""
    assert data["custom_body_code"] == ""


def test_admin_update_branding_custom_code_too_long(client):
    """Test that custom code fields have max length validation (10000 chars)."""
    admin_token = login(client, "bootstrap@example.com", "super-secret")
    payload = {
        "name": "Long Code Test",
        "logo_url": "/logo.svg",
        "favicon_url": "/favicon.ico",
        "custom_head_code": "x" * 10001,
    }
    response = client.patch(
        "/admin/settings/branding", json=payload, headers=auth_headers(admin_token)
    )
    assert response.status_code == 422


def test_branding_defaults_when_no_settings_exist(client):
    """Test that default values are returned when no settings exist in database."""
    # This test verifies the service fallback behavior
    response = client.get("/server/info")
    assert response.status_code == 200

    data = response.json()
    branding = data["branding"]

    # Should return defaults even if DB has no records
    assert isinstance(branding["name"], str)
    assert isinstance(branding["logo_url"], str)
    assert isinstance(branding["favicon_url"], str)
