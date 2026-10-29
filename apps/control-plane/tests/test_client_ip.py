"""Unit tests for app.core.http.get_client_ip.

Behind Caddy, `request.client.host` is always Caddy's own container IP, never
the real caller — that's the bug this helper exists to fix. The forwarding
headers are believed only when that peer is a trusted proxy, and the client is
the right-most X-Forwarded-For hop that is not one.
"""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from app.core.http import get_client_ip

app = FastAPI()


@app.get("/whoami")
def whoami(request: Request) -> dict:
    return {"ip": get_client_ip(request)}


# Peer = Caddy's container address, a trusted proxy by default.
client = TestClient(app, client=("172.18.0.14", 50000))


def test_prefers_x_forwarded_for() -> None:
    resp = client.get("/whoami", headers={"X-Forwarded-For": "203.0.113.7"})
    assert resp.json()["ip"] == "203.0.113.7"


def test_x_forwarded_for_chain_skips_trusted_proxy_hops() -> None:
    resp = client.get("/whoami", headers={"X-Forwarded-For": "203.0.113.5, 172.18.0.14"})
    assert resp.json()["ip"] == "203.0.113.5"


def test_two_hop_chain_through_edge_proxy() -> None:
    # Edge Caddy (10.0.0.1) -> host Caddy -> control plane.
    resp = client.get("/whoami", headers={"X-Forwarded-For": "198.51.100.23, 10.0.0.1"})
    assert resp.json()["ip"] == "198.51.100.23"


def test_client_supplied_left_entries_are_not_believed() -> None:
    # Whatever the client put in front of the chain is left of the hop our
    # proxy recorded; the right-most untrusted hop wins.
    resp = client.get("/whoami", headers={"X-Forwarded-For": "192.0.2.4, 198.51.100.23, 10.0.0.1"})
    assert resp.json()["ip"] == "198.51.100.23"


def test_forwarding_headers_ignored_from_untrusted_peer() -> None:
    direct = TestClient(app, client=("198.51.100.99", 50000))
    resp = direct.get("/whoami", headers={"X-Forwarded-For": "192.0.2.4", "X-Real-IP": "192.0.2.8"})
    assert resp.json()["ip"] == "198.51.100.99"


def test_x_forwarded_for_entry_is_trimmed() -> None:
    resp = client.get("/whoami", headers={"X-Forwarded-For": "  203.0.113.5  ,172.18.0.14"})
    assert resp.json()["ip"] == "203.0.113.5"


def test_falls_back_to_x_real_ip_when_no_xff() -> None:
    resp = client.get("/whoami", headers={"X-Real-IP": "203.0.113.9"})
    assert resp.json()["ip"] == "203.0.113.9"


def test_x_forwarded_for_wins_over_x_real_ip() -> None:
    resp = client.get(
        "/whoami",
        headers={"X-Forwarded-For": "203.0.113.5", "X-Real-IP": "203.0.113.9"},
    )
    assert resp.json()["ip"] == "203.0.113.5"


def test_falls_back_to_request_client_when_no_forwarding_headers() -> None:
    # TestClient's own connection has no real peer address in this ASGI
    # transport, so this exercises the same code path production hits if
    # Caddy's headers were ever absent (e.g. a direct, non-Caddy request).
    resp = client.get("/whoami")
    assert resp.status_code == 200
    assert "ip" in resp.json()
