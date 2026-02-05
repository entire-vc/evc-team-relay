"""Tests for URL utility functions."""

from fastapi import Request
from starlette.datastructures import Headers

from app.utils.url import get_base_url


def test_get_base_url_with_forwarded_proto():
    """Test that X-Forwarded-Proto is respected."""
    # Mock request with X-Forwarded-Proto header
    headers = Headers(
        {
            "x-forwarded-proto": "https",
            "x-forwarded-host": "cp.example.com",
            "host": "cp.example.com",
        }
    )

    # Create mock request
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/test",
        "query_string": b"",
        "headers": headers.raw,
        "scheme": "http",  # Internal scheme
        "server": ("localhost", 8000),
    }

    request = Request(scope)
    base_url = get_base_url(request)

    # Should use HTTPS from forwarded headers
    assert base_url == "https://cp.example.com"


def test_get_base_url_without_forwarded_headers():
    """Test fallback when no forwarded headers are present."""
    headers = Headers({"host": "localhost:8000"})

    scope = {
        "type": "http",
        "method": "GET",
        "path": "/test",
        "query_string": b"",
        "headers": headers.raw,
        "scheme": "http",
        "server": ("localhost", 8000),
    }

    request = Request(scope)
    base_url = get_base_url(request)

    # Should use request.base_url
    assert base_url.startswith("http://")


def test_get_base_url_direct_https():
    """Test when request is already HTTPS (no proxy)."""
    headers = Headers({"host": "cp.example.com"})

    scope = {
        "type": "http",
        "method": "GET",
        "path": "/test",
        "query_string": b"",
        "headers": headers.raw,
        "scheme": "https",
        "server": ("cp.example.com", 443),
    }

    request = Request(scope)
    base_url = get_base_url(request)

    # Should use HTTPS from request scheme
    assert base_url.startswith("https://")
