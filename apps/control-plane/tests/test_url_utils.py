"""Tests for URL utility functions."""

from fastapi import Request
from starlette.datastructures import Headers

from app.core.config import get_settings
from app.utils.url import build_invite_oauth_urls, get_base_url


def _make_request(headers: dict, scheme: str = "http", server=("localhost", 8000)) -> Request:
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/test",
        "query_string": b"",
        "headers": Headers(headers).raw,
        "scheme": scheme,
        "server": server,
    }
    return Request(scope)


def test_get_base_url_ignores_forwarded_headers(monkeypatch):
    """Client-supplied X-Forwarded-*/Host must NOT steer the base URL — trusting
    them would let an attacker point an emailed invite link at their own domain
    (Host-header-injection / password-reset-poisoning pattern)."""
    monkeypatch.setenv("CONTROL_PLANE_PUBLIC_URL", "https://cp.trusted.example")
    get_settings.cache_clear()
    try:
        request = _make_request(
            {
                "x-forwarded-proto": "https",
                "x-forwarded-host": "evil.example",
                "host": "evil.example",
            }
        )
        assert get_base_url(request) == "https://cp.trusted.example"
    finally:
        get_settings.cache_clear()


def test_get_base_url_uses_configured_public_url_by_default():
    """No forwarded headers at all — still returns the configured public URL,
    never request.base_url (which would reflect the internal container address)."""
    get_settings.cache_clear()
    try:
        request = _make_request({"host": "localhost:8000"})
        assert get_base_url(request) == get_settings().control_plane_public_url.rstrip("/")
    finally:
        get_settings.cache_clear()


def test_get_base_url_falls_back_to_request_when_unconfigured(monkeypatch):
    """Only when control_plane_public_url is explicitly left empty do we fall
    back to request.base_url — matches pre-fix behavior for that one edge case."""
    monkeypatch.setenv("CONTROL_PLANE_PUBLIC_URL", "")
    get_settings.cache_clear()
    try:
        request = _make_request(
            {"host": "cp.example.com"}, scheme="https", server=("cp.example.com", 443)
        )
        assert get_base_url(request).startswith("https://")
    finally:
        get_settings.cache_clear()


def test_build_invite_oauth_urls_quotes_token_with_query_special_chars():
    """Regression test for the semgrep var-in-href finding on invite.html:
    `token` reaches invite_page() as a raw, unvalidated path parameter
    (get_invite_public_info returns is_valid=False rather than 404ing on an
    unknown token) and used to flow straight into oauth_authorize_url as both
    a path segment and a nested query value — an attacker-chosen token
    containing `&`/`#`/`/` could desync the query string it ends up embedded
    in if not quoted first."""
    malicious_token = "abc&x=evil#frag"
    urls = build_invite_oauth_urls("https://cp.example.com", malicious_token, "casdoor")

    # The raw token must never appear un-encoded in any built URL — that
    # would let it inject an extra query param or fragment.
    assert malicious_token not in urls["invite_page_url"]
    assert malicious_token not in urls["oauth_authorize_url"]
    assert "abc%26x%3Devil%23frag" in urls["invite_page_url"]

    # And the authorize URL must still have exactly one redirect_uri and one
    # return_url param — proof the injected `&` didn't split the query string.
    assert urls["oauth_authorize_url"].count("redirect_uri=") == 1
    assert urls["oauth_authorize_url"].count("return_url=") == 1


def test_build_invite_oauth_urls_normal_token_round_trips():
    """A normal secrets.token_hex() invite token still produces a working,
    unmangled set of URLs."""
    token = "a" * 64  # shape of secrets.token_hex(32)
    urls = build_invite_oauth_urls("https://cp.example.com", token, "casdoor")

    assert urls["invite_page_url"] == f"https://cp.example.com/invite/{token}/page"
    assert urls["oauth_callback_url"] == "https://cp.example.com/v1/auth/oauth/casdoor/callback"
    assert urls["oauth_authorize_url"].startswith(
        "https://cp.example.com/v1/auth/oauth/casdoor/authorize?redirect_uri="
    )
    expected_return_url = f"return_url=https%3A%2F%2Fcp.example.com%2Finvite%2F{token}%2Fpage"
    assert expected_return_url in urls["oauth_authorize_url"]
