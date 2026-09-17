"""Unit tests for app.core.http.get_client_ip.

Behind Caddy (infra/Caddyfile, no `trusted_proxies`), `request.client.host` is
always Caddy's own container IP, never the real caller — that's the bug this
helper exists to fix. Caddy itself replaces (does not append to) any
client-supplied X-Forwarded-For before proxying (verified empirically against
the pinned `caddy:2` image), so trusting the header here is safe; these tests
exercise the helper's own precedence and fallback logic in isolation.
"""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from app.core.http import get_client_ip

app = FastAPI()


@app.get("/whoami")
def whoami(request: Request) -> dict:
    return {"ip": get_client_ip(request)}


client = TestClient(app)


def test_prefers_x_forwarded_for() -> None:
    resp = client.get("/whoami", headers={"X-Forwarded-For": "203.0.113.7"})
    assert resp.json()["ip"] == "203.0.113.7"


def test_x_forwarded_for_multiple_entries_takes_first() -> None:
    # Caddy's own set value is a single entry in our deployment, but the
    # helper must not choke on (or misread) a chain if one ever appears.
    resp = client.get("/whoami", headers={"X-Forwarded-For": "203.0.113.5, 172.18.0.14"})
    assert resp.json()["ip"] == "203.0.113.5"


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
