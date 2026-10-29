"""Client-IP resolution behind our reverse proxies.

Every deployment (relay-vm, teamrelay-ru/alyssa) puts Caddy in front of the
control-plane container as the sole ingress. `request.client.host` in that
setup is Caddy's own container IP (`172.18.0.x`), not the real caller — so
every audit-log row, security email, agent-key IP-track and invite-redemption
record written from the raw `request.client.host` recorded the proxy, not the
client (incident: a login-notification email showed `<container-ip>` instead of
Pavel's own IP). Caddy forwards the real address in `X-Forwarded-For`.

Prod has two proxy hops: an edge Caddy terminates TLS and forwards to
the host's Caddy over a private network. Taking the FIRST
X-Forwarded-For entry is wrong there: the left end of the chain is whatever
the client chose to send, and a host Caddy that does not trust the edge
(no `trusted_proxies`) replaces the header with the edge's own address, so
every caller looked like the edge's private address. The rule now:

- X-Forwarded-For / X-Real-IP are believed only when the immediate peer is a
  trusted proxy (`settings.trusted_proxy_cidrs`, private ranges by default);
- the client is the right-most X-Forwarded-For hop that is NOT a trusted
  proxy — every hop to its right was appended by a proxy we trust, every hop
  to its left could have been written by the client.
"""

from __future__ import annotations

import ipaddress
from functools import lru_cache

from fastapi import Request

from app.core.config import get_settings

_Network = ipaddress.IPv4Network | ipaddress.IPv6Network


@lru_cache(maxsize=8)
def _parse_cidrs(raw: str) -> tuple[_Network, ...]:
    return tuple(
        ipaddress.ip_network(part.strip(), strict=False) for part in raw.split(",") if part.strip()
    )


def _is_trusted(host: str | None, networks: tuple[_Network, ...]) -> bool:
    if not host:
        return False
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    return any(address in network for network in networks)


def get_client_ip(request: Request) -> str | None:
    """Best-effort real client IP, trusting forwarding headers only from our proxies."""
    peer = request.client.host if request.client else None
    networks = _parse_cidrs(get_settings().trusted_proxy_cidrs)
    if not _is_trusted(peer, networks):
        return peer

    xff = request.headers.get("x-forwarded-for")
    if xff:
        hops = [hop.strip() for hop in xff.split(",") if hop.strip()]
        for hop in reversed(hops):
            if not _is_trusted(hop, networks):
                return hop
        if hops:
            # Every hop is one of our proxies: the left-most is the closest
            # we can get to the origin.
            return hops[0]
    xri = request.headers.get("x-real-ip")
    if xri:
        return xri.strip()
    return peer
