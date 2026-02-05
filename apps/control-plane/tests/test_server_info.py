"""Tests for the server info endpoint."""

from __future__ import annotations

from unittest.mock import patch

from app.core.config import Settings


def test_server_info_returns_metadata(client):
    """Test that GET /server/info returns server metadata."""
    response = client.get("/server/info")
    assert response.status_code == 200

    data = response.json()
    assert "id" in data
    assert "name" in data
    assert "version" in data
    assert "edition" in data
    assert data["edition"] == "community"  # OSS edition
    assert "relay_url" in data
    assert "features" in data
    assert "branding" in data

    # Check features structure
    features = data["features"]
    assert features["multi_user"] is True
    assert features["share_members"] is True
    assert features["audit_logging"] is True
    assert features["admin_ui"] is True
    # OAuth disabled by default
    assert features["oauth_enabled"] is False
    assert features["oauth_provider"] is None

    # Check branding structure
    branding = data["branding"]
    assert "name" in branding
    assert "logo_url" in branding
    assert "favicon_url" in branding


def test_server_info_relay_url_matches_config(client):
    """Test that relay_url matches configured RELAY_PUBLIC_URL."""
    response = client.get("/server/info")
    assert response.status_code == 200

    data = response.json()
    # From conftest.py: os.environ["RELAY_PUBLIC_URL"] = "wss://relay.test"
    assert data["relay_url"] == "wss://relay.test"


def test_server_info_no_auth_required(client):
    """Test that GET /server/info does not require authentication."""
    # No Authorization header provided
    response = client.get("/server/info")
    assert response.status_code == 200


def test_server_info_oauth_enabled(client):
    """Test that OAuth fields are populated when OAuth is enabled."""
    mock_settings = Settings(
        oauth_enabled=True,
        oauth_provider_name="casdoor",
        relay_public_url="wss://relay.test",
    )

    with patch("app.api.routers.server.get_settings", return_value=mock_settings):
        response = client.get("/server/info")
        assert response.status_code == 200

        data = response.json()
        features = data["features"]
        assert features["oauth_enabled"] is True
        assert features["oauth_provider"] == "casdoor"


def test_server_info_oauth_provider_hidden_when_disabled(client):
    """Test that oauth_provider is null when OAuth is disabled."""
    mock_settings = Settings(
        oauth_enabled=False,
        oauth_provider_name="keycloak",  # Provider name set but OAuth disabled
        relay_public_url="wss://relay.test",
    )

    with patch("app.api.routers.server.get_settings", return_value=mock_settings):
        response = client.get("/server/info")
        assert response.status_code == 200

        data = response.json()
        features = data["features"]
        assert features["oauth_enabled"] is False
        assert features["oauth_provider"] is None
