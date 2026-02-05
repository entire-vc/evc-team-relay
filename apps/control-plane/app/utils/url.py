"""URL utility functions for handling reverse proxy scenarios."""

from __future__ import annotations

from fastapi import Request


def get_base_url(request: Request) -> str:
    """Get base URL from request, respecting X-Forwarded-Proto header.

    When behind a reverse proxy (like Caddy), the internal connection may be HTTP
    while the external connection is HTTPS. This function checks the X-Forwarded-Proto
    header to determine the correct scheme.

    Args:
        request: FastAPI request object

    Returns:
        Base URL with correct scheme (e.g., "https://cp.example.com")
    """
    # Check if we're behind a proxy
    forwarded_proto = request.headers.get("x-forwarded-proto")
    forwarded_host = request.headers.get("x-forwarded-host") or request.headers.get("host")

    if forwarded_proto and forwarded_host:
        # Use forwarded headers (external URL)
        return f"{forwarded_proto}://{forwarded_host}"

    # Fallback to request.base_url (may be HTTP in proxy scenarios)
    return str(request.base_url).rstrip("/")
