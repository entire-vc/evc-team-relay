"""Client-IP resolution behind our reverse proxy.

Every deployment (relay-vm, teamrelay-ru/alyssa) puts Caddy in front of the
control-plane container as the sole ingress. `request.client.host` in that
setup is Caddy's own container IP (`172.18.0.x`), not the real caller — so
every audit-log row, security email, agent-key IP-track and invite-redemption
record written from the raw `request.client.host` recorded the proxy, not the
client (incident: a login-notification email showed `<container-ip>` instead of
Pavel's own IP). Caddy forwards the real address in `X-Forwarded-For`; this
just reads it.

We trust `X-Forwarded-For`/`X-Real-IP` unconditionally because Caddy is the
only ingress in every deployment (infra/Caddyfile) and, empirically verified
against `caddy:2` (the pinned image tag, v2.11.4 at time of writing) with no
`trusted_proxies` configured: Caddy REPLACES any client-supplied
`X-Forwarded-For` with its own view of the immediate peer rather than
appending to it — a spoofed header sent straight at Caddy from outside does
not survive the hop. So whatever this code reads back out was set by Caddy,
not by whoever made the request. A bare deploy without Caddy in front (or a
second proxy hop between Caddy and this container) would need this revisited.
"""

from __future__ import annotations

from fastapi import Request


def get_client_ip(request: Request) -> str | None:
    """Best-effort real client IP, preferring Caddy's forwarding headers."""
    xff = request.headers.get("x-forwarded-for")
    if xff:
        return xff.split(",")[0].strip()
    xri = request.headers.get("x-real-ip")
    if xri:
        return xri.strip()
    return request.client.host if request.client else None
