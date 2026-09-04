"""Tests for instance branding functionality."""

from __future__ import annotations

import os

from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.services import instance_settings_service


def auth_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def login(client: TestClient, email: str, password: str) -> str:
    response = client.post("/auth/login", json={"email": email, "password": password})
    assert response.status_code == 200, response.text
    return response.json()["access_token"]


def _set_origin(cors_origin: str) -> None:
    """Set the instance's own origin explicitly for a test, via
    CORS_ALLOWED_ORIGINS (the fallback source _this_instance_origin() reads
    once CONTROL_PLANE_PUBLIC_URL is confirmed absent-or-placeholder).

    A test asserting against an origin it named itself is testing behavior;
    a test asserting against whatever config.py's Field default happens to
    be is testing that default, not the resolution logic — flagged by
    Daedalus's review on #5f51a2dd MR !269. Also clears
    CONTROL_PLANE_PUBLIC_URL so the test genuinely exercises the fallback
    path rather than incidentally reading a real value some other test (or
    the host environment) left behind.
    """
    os.environ.pop("CONTROL_PLANE_PUBLIC_URL", None)
    os.environ["CORS_ALLOWED_ORIGINS"] = cors_origin
    get_settings.cache_clear()


def _clear_origin() -> None:
    os.environ.pop("CORS_ALLOWED_ORIGINS", None)
    os.environ.pop("CONTROL_PLANE_PUBLIC_URL", None)
    get_settings.cache_clear()


def test_server_info_includes_branding(client):
    """Test that GET /server/info includes branding information."""
    _set_origin("https://cp.tr.entire.vc")
    try:
        response = client.get("/server/info")
        assert response.status_code == 200

        data = response.json()
        assert "branding" in data

        # Check branding structure
        branding = data["branding"]
        assert "name" in branding
        assert "logo_url" in branding
        assert "favicon_url" in branding

        # Default values should be present, and absolutized against this
        # instance's own origin (#5f51a2dd) — never bare relative paths,
        # which a client with no page to resolve against (the Obsidian
        # plugin) can't use at all.
        assert branding["name"] == "Relay Server"
        assert branding["logo_url"] == "https://cp.tr.entire.vc/static/img/evc-ava.png"
        assert branding["favicon_url"] == "https://cp.tr.entire.vc/static/img/evc-ava.svg"
    finally:
        _clear_origin()


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
    _set_origin("https://cp.tr.entire.vc")
    try:
        admin_token = login(client, "bootstrap@example.com", "super-secret")
        response = client.get("/admin/settings/branding", headers=auth_headers(admin_token))
        assert response.status_code == 200

        data = response.json()
        assert data["name"] == "Relay Server"
        assert data["logo_url"] == "https://cp.tr.entire.vc/static/img/evc-ava.png"
        assert data["favicon_url"] == "https://cp.tr.entire.vc/static/img/evc-ava.svg"
    finally:
        _clear_origin()


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

    # set_branding() itself stores exactly what was sent, unmodified — the
    # PATCH endpoint's own response is the raw stored value, not absolutized.
    data = response.json()
    assert data["logo_url"] == "/custom-logo.svg"

    # Check server info — get_branding() DOES absolutize a stored relative
    # URL (#5f51a2dd), since this is the read path an external client
    # (the Obsidian plugin, no page of its own) actually consumes.
    response = client.get("/server/info")
    assert response.status_code == 200

    data = response.json()
    branding = data["branding"]
    assert branding["name"] == "Custom Instance"
    assert branding["logo_url"] == "https://cp.tr.entire.vc/custom-logo.svg"
    assert branding["favicon_url"] == "https://cp.tr.entire.vc/custom-favicon.ico"


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


# ---------------------------------------------------------------------------
# #5f51a2dd — logo_url/favicon_url must be absolute, and per-host: an
# instance's branding URLs must point at ITS OWN origin, not whichever
# origin happened to be the default. This is the actual reported bug: a
# second instance (teamrelay.ru) that never explicitly set branding URLs
# served bare relative paths ("/static/img/evc-ava.png"), which the
# Obsidian plugin's server list — no page of its own to resolve against —
# could not render.
# ---------------------------------------------------------------------------


class TestBrandingAbsoluteUrls:
    def _set_cors_origin(self, origin: str):
        os.environ["CORS_ALLOWED_ORIGINS"] = origin
        get_settings.cache_clear()

    def teardown_method(self):
        os.environ.pop("CORS_ALLOWED_ORIGINS", None)
        get_settings.cache_clear()

    def test_default_relative_logo_is_absolutized_to_this_instances_own_origin(self, client):
        """The actual bug shape: an instance whose CORS origin is NOT the
        default cp.tr.entire.vc must get ITS OWN origin on the default
        (never-explicitly-set) logo/favicon — not the other instance's."""
        self._set_cors_origin("https://cp.teamrelay.ru")

        response = client.get("/server/info")
        assert response.status_code == 200
        branding = response.json()["branding"]
        assert branding["logo_url"] == "https://cp.teamrelay.ru/static/img/evc-ava.png"
        assert branding["favicon_url"] == "https://cp.teamrelay.ru/static/img/evc-ava.svg"
        # Must NOT silently be the other (default) instance's origin.
        assert "cp.tr.entire.vc" not in branding["logo_url"]

    def test_REGRESSION_relative_url_is_no_longer_served_bare(self, db_session, client):
        """Must fail on the pre-fix code: get_branding() used to return
        DEFAULT_BRANDING's relative paths completely unresolved."""
        self._set_cors_origin("https://cp.teamrelay.ru")
        branding = instance_settings_service.get_branding(db_session)
        assert not branding["logo_url"].startswith(
            "/"
        ), f"logo_url is still a bare relative path: {branding['logo_url']!r}"
        assert branding["logo_url"].startswith("https://cp.teamrelay.ru/")

    def test_already_absolute_stored_url_is_never_rewritten(self, db_session, client):
        """An admin-set absolute URL (any host, even a CDN) passes through
        untouched — this only fills in a MISSING origin, never overrides one
        that's already there."""
        self._set_cors_origin("https://cp.teamrelay.ru")
        instance_settings_service.set_branding(
            db_session,
            name="Custom",
            logo_url="https://cdn.example.com/logo.png",
            favicon_url="https://cdn.example.com/favicon.svg",
        )
        branding = instance_settings_service.get_branding(db_session)
        assert branding["logo_url"] == "https://cdn.example.com/logo.png"
        assert branding["favicon_url"] == "https://cdn.example.com/favicon.svg"

    def test_wildcard_cors_origin_does_not_produce_a_garbage_url(self, db_session, client):
        """CORS_ALLOWED_ORIGINS='*' (a real documented dev-only value, per
        infra/env.example) is not a URL — must not get concatenated into
        something like '*/static/img/...'. Falls back to leaving the path
        relative rather than fabricating a bogus absolute URL."""
        self._set_cors_origin("*")
        branding = instance_settings_service.get_branding(db_session)
        assert not branding["logo_url"].startswith("*")

    def test_comma_separated_cors_origins_uses_the_first(self, db_session, client):
        self._set_cors_origin("https://cp.teamrelay.ru,https://staging.teamrelay.ru")
        branding = instance_settings_service.get_branding(db_session)
        assert branding["logo_url"].startswith("https://cp.teamrelay.ru/")


# ---------------------------------------------------------------------------
# #5f51a2dd, Daedalus's MR !269 review: CORS_ALLOWED_ORIGINS-only was wrong.
# It "worked" on tr.entire.vc only because config.py's Field default happens
# to equal that host's real domain — tr-relay-vm's actual .env has no CORS/
# ORIGIN key at all (same live measurement as #08e44245). The correct primary
# source is settings.control_plane_public_url, confirmed live inside both
# running containers (`docker compose exec control-plane env`) to hold the
# real per-host value on tr-relay-vm AND tr-ru-vm, contradicting this file's
# own earlier claim (in the first version of this fix) that the field is
# "never wired" — it IS, via docker-compose.yml's `env_file: ./.env`, which
# loads the whole file, not just keys also listed under `environment:`.
# ---------------------------------------------------------------------------


class TestBrandingControlPlanePublicUrlPriority:
    def _set(self, *, control_plane_public_url: str | None = None, cors_origin: str | None = None):
        if control_plane_public_url is None:
            os.environ.pop("CONTROL_PLANE_PUBLIC_URL", None)
        else:
            os.environ["CONTROL_PLANE_PUBLIC_URL"] = control_plane_public_url
        if cors_origin is None:
            os.environ.pop("CORS_ALLOWED_ORIGINS", None)
        else:
            os.environ["CORS_ALLOWED_ORIGINS"] = cors_origin
        get_settings.cache_clear()

    def teardown_method(self):
        os.environ.pop("CONTROL_PLANE_PUBLIC_URL", None)
        os.environ.pop("CORS_ALLOWED_ORIGINS", None)
        get_settings.cache_clear()

    def test_control_plane_public_url_wins_when_it_disagrees_with_cors(self, db_session, client):
        """The actual regression this fix closes: when the two sources
        disagree, control_plane_public_url must win — it's the field that's
        semantically "my own public address"; cors_allowed_origins is a
        fallback that happened to coincide with the right answer on one
        host only by the accident of a matching code default."""
        self._set(
            control_plane_public_url="https://cp.teamrelay.ru",
            cors_origin="https://some-other-origin.example",
        )
        branding = instance_settings_service.get_branding(db_session)
        assert branding["logo_url"].startswith("https://cp.teamrelay.ru/")
        assert "some-other-origin" not in branding["logo_url"]

    def test_unset_control_plane_public_url_falls_back_to_cors(self, db_session, client):
        """control_plane_public_url left unset (its Pydantic default,
        "http://localhost:8000") must NOT be treated as a real value —
        fall through to cors_allowed_origins instead of absolutizing to a
        URL that resolves nowhere real."""
        self._set(control_plane_public_url=None, cors_origin="https://cp.teamrelay.ru")
        branding = instance_settings_service.get_branding(db_session)
        assert branding["logo_url"].startswith("https://cp.teamrelay.ru/")
        assert "localhost" not in branding["logo_url"]

    def test_neither_source_set_leaves_url_relative(self, db_session, client):
        """cors_allowed_origins itself defaults to a real-looking URL in
        config.py, so exercise the true "nothing usable" case directly
        against _this_instance_origin() rather than through get_branding(),
        which would otherwise inherit that unrelated default."""
        from app.services.instance_settings_service import _this_instance_origin

        self._set(control_plane_public_url=None, cors_origin="")
        assert _this_instance_origin() is None
