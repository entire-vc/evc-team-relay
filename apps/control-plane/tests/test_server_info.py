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
    assert data["edition"] == "enterprise"  # TR·edition/billing (#f75f04bb)
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
    # Billing disabled by default (BILLING_ENABLED unset in test env)
    assert features["billing_enabled"] is False

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


# ---------------------------------------------------------------------------
# billing_enabled AND NOT billing_stub_mode (Mesh #fa109ff5)
#
# A stubbed catalog is not real billing. Reporting billing_enabled=true for an
# instance that's actually serving BILLING_STUB_MODE=true is exactly what let
# tr-ru-vm run for days indistinguishable from a genuinely billing-enabled
# instance from the outside -- including to scripts/deploy.sh's own smoke gate,
# which reads this exact field to decide whether to auto-rollback.
# ---------------------------------------------------------------------------


def test_server_info_billing_enabled_false_when_stub_mode_on(client):
    """The actual incident shape: BILLING_ENABLED=true + BILLING_STUB_MODE=true
    (defaulted, in tr-ru-vm's case) must externally report billing_enabled=false,
    not true -- a stub catalog is not real billing."""
    mock_settings = Settings(
        billing_enabled=True,
        billing_stub_mode=True,
        relay_public_url="wss://relay.test",
    )

    with patch("app.api.routers.server.get_settings", return_value=mock_settings):
        response = client.get("/server/info")
        assert response.status_code == 200
        assert response.json()["features"]["billing_enabled"] is False


def test_server_info_billing_enabled_true_only_when_both_conditions_hold(client):
    """Positive control: billing_enabled=true requires BOTH billing_enabled=True
    AND billing_stub_mode=False -- the genuinely-live shape (relay-host today)."""
    mock_settings = Settings(
        billing_enabled=True,
        billing_stub_mode=False,
        relay_public_url="wss://relay.test",
    )

    with patch("app.api.routers.server.get_settings", return_value=mock_settings):
        response = client.get("/server/info")
        assert response.status_code == 200
        assert response.json()["features"]["billing_enabled"] is True


def test_server_info_billing_enabled_false_when_billing_itself_off(client):
    """Stub mode being off doesn't matter if billing_enabled is False outright."""
    mock_settings = Settings(
        billing_enabled=False,
        billing_stub_mode=False,
        relay_public_url="wss://relay.test",
    )

    with patch("app.api.routers.server.get_settings", return_value=mock_settings):
        response = client.get("/server/info")
        assert response.status_code == 200
        assert response.json()["features"]["billing_enabled"] is False
