"""Rate-limit buckets are keyed by caller, not by the proxy's address.

Context: every `Limiter` used slowapi's `get_remote_address`
— `request.client.host`, which behind Caddy is the proxy, identical for every
client. "30/minute per IP" on POST /tokens/relay was therefore 30 tokens per
minute for the WHOLE production instance, and background pulls of large vaults
starved on 429. These tests pin the per-caller key: two users (or two agent
keys) arriving through the same proxy address get separate buckets, and a
single caller over its own budget still gets 429.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace

from fastapi.testclient import TestClient

from app.api.routers import tokens as tokens_router
from app.core.ratelimit import rate_limit_key, relay_token_rate_limit_key
from app.core.security import create_access_token
from tests.test_relay_token_endpoint import (
    add_member,
    auth_headers,
    create_share,
    create_user,
    login,
    make_agent_key,
)

# Same edge address for everyone: what prod sees today for every caller.
PROXY_XFF = {"X-Forwarded-For": "10.0.0.1"}


def _relay_token(client: TestClient, share_id: str, headers: dict[str, str]) -> int:
    resp = client.post(
        "/tokens/relay",
        json={"share_id": share_id, "doc_id": share_id, "mode": "read"},
        headers={**headers, **PROXY_XFF},
    )
    return resp.status_code


def test_two_users_behind_one_proxy_do_not_share_relay_token_bucket(
    client: TestClient,
) -> None:
    admin = login(client, "bootstrap@example.com", "super-secret")
    share_id = create_share(client, admin)
    member_id = create_user(client, admin, "member@example.com", "member-secret-1")
    add_member(client, admin, share_id, member_id, "viewer")
    member = login(client, "member@example.com", "member-secret-1")

    budget = tokens_router.RELAY_TOKEN_LIMIT_PER_CALLER
    statuses = [_relay_token(client, share_id, auth_headers(admin)) for _ in range(budget)]
    assert statuses == [200] * budget, statuses

    # Admin is over its own budget ...
    assert _relay_token(client, share_id, auth_headers(admin)) == 429
    # ... the other user behind the same proxy address is not.
    assert _relay_token(client, share_id, auth_headers(member)) == 200


def test_two_agent_keys_behind_one_proxy_do_not_share_relay_token_bucket(
    client: TestClient, db_session
) -> None:
    admin = login(client, "bootstrap@example.com", "super-secret")
    share_id = create_share(client, admin)
    admin_id = client.get("/auth/me", headers=auth_headers(admin)).json()["id"]
    key_a = make_agent_key(db_session, share_id, uuid.UUID(admin_id), scopes="read")
    key_b = make_agent_key(db_session, share_id, uuid.UUID(admin_id), scopes="read")

    budget = tokens_router.RELAY_TOKEN_LIMIT_PER_CALLER
    for _ in range(budget):
        _relay_token(client, share_id, {"X-Agent-Key": key_a})

    assert _relay_token(client, share_id, {"X-Agent-Key": key_a}) == 429
    assert _relay_token(client, share_id, {"X-Agent-Key": key_b}) != 429


def test_anonymous_relay_token_keeps_low_per_ip_limit(client: TestClient) -> None:
    admin = login(client, "bootstrap@example.com", "super-secret")
    resp = client.post(
        "/shares",
        json={"kind": "doc", "path": "vault/public.md", "visibility": "public"},
        headers=auth_headers(admin),
    )
    share_id = resp.json()["id"]

    def anon(ip: str) -> int:
        return client.post(
            "/tokens/relay",
            json={"share_id": share_id, "doc_id": "vault/public.md", "mode": "read"},
            headers={"X-Forwarded-For": ip},
        ).status_code

    budget = tokens_router.RELAY_TOKEN_LIMIT_ANONYMOUS
    statuses = [anon("203.0.113.20") for _ in range(budget)]
    assert statuses == [200] * budget, statuses
    assert anon("203.0.113.20") == 429
    assert anon("203.0.113.21") == 200


def test_anonymous_requests_fall_back_to_client_ip(client: TestClient) -> None:
    # Login is anonymous: it must stay keyed per real client address, and two
    # different addresses must not share it.
    def attempt(ip: str) -> int:
        return client.post(
            "/auth/login",
            json={"email": "nobody@example.com", "password": "wrong-password-1"},
            headers={"X-Forwarded-For": ip},
        ).status_code

    first = [attempt("203.0.113.10") for _ in range(10)]
    assert 429 not in first, first
    assert attempt("203.0.113.10") == 429
    assert attempt("203.0.113.11") != 429


# ── key functions in isolation ───────────────────────────────


class _FakeClient:
    host = "172.18.0.14"


class _FakeRequest:
    def __init__(self, headers=None, cookies=None, body: bytes | None = None):
        self.headers = {k.lower(): v for k, v in (headers or {}).items()}
        self.cookies = cookies or {}
        self.client = _FakeClient()
        self.state = SimpleNamespace()
        if body is not None:
            self._body = body


def test_key_uses_user_for_valid_bearer() -> None:
    uid = str(uuid.uuid4())
    token = create_access_token(uid)
    req = _FakeRequest({"Authorization": f"Bearer {token}", **PROXY_XFF})
    assert rate_limit_key(req) == f"user:{uid}"


def test_forged_bearer_does_not_get_its_own_bucket() -> None:
    # An unsigned/garbage token must not mint a fresh bucket per request:
    # it falls back to the caller's address like any anonymous request.
    req = _FakeRequest({"Authorization": "Bearer not-a-jwt", "X-Forwarded-For": "203.0.113.7"})
    assert rate_limit_key(req) == "ip:203.0.113.7"


def test_key_uses_access_token_cookie() -> None:
    uid = str(uuid.uuid4())
    req = _FakeRequest(cookies={"access_token": create_access_token(uid)})
    assert rate_limit_key(req) == f"user:{uid}"


def test_live_agent_key_is_keyed_by_row_never_raw(client: TestClient, db_session) -> None:
    admin = login(client, "bootstrap@example.com", "super-secret")
    share_id = create_share(client, admin)
    admin_id = client.get("/auth/me", headers=auth_headers(admin)).json()["id"]
    raw = make_agent_key(db_session, share_id, uuid.UUID(admin_id), scopes="read")
    key = rate_limit_key(_FakeRequest({"X-Agent-Key": raw}))
    assert key.startswith("agentkey:")
    assert raw not in key


def test_unknown_or_revoked_agent_key_falls_back_to_ip(client: TestClient, db_session) -> None:
    admin = login(client, "bootstrap@example.com", "super-secret")
    share_id = create_share(client, admin)
    admin_id = client.get("/auth/me", headers=auth_headers(admin)).json()["id"]
    revoked = make_agent_key(db_session, share_id, uuid.UUID(admin_id), revoked=True)
    xff = {"X-Forwarded-For": "203.0.113.7"}
    assert rate_limit_key(_FakeRequest({"X-Agent-Key": "tr_agent_made_up", **xff})) == (
        "ip:203.0.113.7"
    )
    assert rate_limit_key(_FakeRequest({"X-Agent-Key": revoked, **xff})) == "ip:203.0.113.7"


def test_rotating_fake_agent_keys_cannot_dodge_login_limit(client: TestClient) -> None:
    def attempt(i: int) -> int:
        return client.post(
            "/auth/login",
            json={"email": "nobody@example.com", "password": "wrong-password-1"},
            headers={"X-Agent-Key": f"tr_agent_fake_{i}", "X-Forwarded-For": "203.0.113.30"},
        ).status_code

    statuses = [attempt(i) for i in range(11)]
    assert statuses[-1] == 429, statuses


def test_relay_token_key_scopes_to_share() -> None:
    uid = str(uuid.uuid4())
    share = str(uuid.uuid4())
    req = _FakeRequest(
        {"Authorization": f"Bearer {create_access_token(uid)}"},
        body=f'{{"share_id": "{share}", "doc_id": "x"}}'.encode(),
    )
    assert relay_token_rate_limit_key(req) == f"user:{uid}:share:{share}"


def test_relay_token_key_ignores_unparseable_body() -> None:
    uid = str(uuid.uuid4())
    req = _FakeRequest({"Authorization": f"Bearer {create_access_token(uid)}"}, body=b"{oops")
    assert relay_token_rate_limit_key(req) == f"user:{uid}"
