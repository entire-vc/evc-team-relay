"""URL utility functions for handling reverse proxy scenarios."""

from __future__ import annotations

from urllib.parse import quote

from fastapi import Request

from app.core.config import get_settings


def get_base_url(request: Request) -> str:
    """Get the externally-reachable base URL for building links (invites, etc).

    Deliberately does NOT trust X-Forwarded-Proto/X-Forwarded-Host/Host from
    the request: those are client-supplied and, if this path is reachable
    without going through a proxy that overwrites them, an attacker could
    point a victim's invite email at an attacker-controlled domain (the
    classic Host-header-injection / password-reset-poisoning pattern) —
    semgrep's directly-returned-format-string finding on the old header-built
    f-string flagged exactly this. Same fix already used in shares.py for the
    same reason: settings.control_plane_public_url is a fixed,
    deploy-time-configured value nothing in the request can steer.

    Args:
        request: FastAPI request object (kept for call-site compatibility and
            as the request.base_url fallback below).

    Returns:
        Base URL with no trailing slash (e.g. "https://cp.example.com").
    """
    settings = get_settings()
    if settings.control_plane_public_url:
        return settings.control_plane_public_url.rstrip("/")

    # No public URL configured — fall back to what uvicorn saw directly
    # (correct only when not behind a reverse proxy).
    return str(request.base_url).rstrip("/")


def build_invite_oauth_urls(base_url: str, token: str, oauth_provider_name: str) -> dict[str, str]:
    """Build the invite-page + OAuth authorize/callback URLs for a given invite token.

    `token` is a raw, unvalidated URL path parameter by the time it reaches
    here (get_invite_public_info() returns is_valid=False rather than 404ing
    on an unknown token), so it's attacker-influenced. base_url is trusted
    (get_base_url()), but quote() the token before re-embedding it in a URL
    path/query segment so it can't smuggle in an extra `&`/`#`/`/` and desync
    the query string oauth_authorize_url nests it inside (semgrep var-in-href
    on invite.html's oauth_authorize_url link).

    Returns: {"invite_page_url", "oauth_callback_url", "oauth_authorize_url"}.
    """
    safe_token = quote(token, safe="")
    invite_page_url = f"{base_url}/invite/{safe_token}/page"
    oauth_callback_url = f"{base_url}/v1/auth/oauth/{oauth_provider_name}/callback"
    oauth_authorize_url = (
        f"{base_url}/v1/auth/oauth/{oauth_provider_name}/authorize"
        f"?redirect_uri={quote(oauth_callback_url, safe='')}"
        f"&return_url={quote(invite_page_url, safe='')}"
    )
    return {
        "invite_page_url": invite_page_url,
        "oauth_callback_url": oauth_callback_url,
        "oauth_authorize_url": oauth_authorize_url,
    }


def build_admin_oauth_urls(base_url: str, oauth_provider_name: str) -> dict[str, str]:
    """Build the OAuth authorize/callback/return URLs for the admin-ui login page.

    Same shape as build_invite_oauth_urls(): the API's existing
    /v1/auth/oauth/{provider}/callback does the token exchange and sets the
    invite_token cookie, then redirects to return_url. Here return_url points
    at /admin-ui/login/oauth/complete, which turns that cookie into an admin
    session (or bounces to the 2FA step) — see admin_ui.py.

    Returns: {"oauth_authorize_url"}.
    """
    oauth_callback_url = f"{base_url}/v1/auth/oauth/{oauth_provider_name}/callback"
    admin_return_url = f"{base_url}/admin-ui/login/oauth/complete"
    oauth_authorize_url = (
        f"{base_url}/v1/auth/oauth/{oauth_provider_name}/authorize"
        f"?redirect_uri={quote(oauth_callback_url, safe='')}"
        f"&return_url={quote(admin_return_url, safe='')}"
    )
    return {"oauth_authorize_url": oauth_authorize_url}
