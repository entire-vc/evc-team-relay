"""Regression tests for agent key CRUD endpoints.

Bug: PR #36 added the rate-limit decorator that reads
``get_settings().agent_key_creation_rate_per_hour``, but the field was
missing from Settings → every POST raised AttributeError → 500.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient


def auth_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def login(client: TestClient, email: str, password: str) -> str:
    response = client.post("/auth/login", json={"email": email, "password": password})
    assert response.status_code == 200, response.text
    return response.json()["access_token"]


def create_share(client: TestClient, token: str, path: str = "vault/test.md") -> str:
    resp = client.post(
        "/shares",
        json={"kind": "doc", "path": path, "visibility": "private"},
        headers=auth_headers(token),
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


# ---------------------------------------------------------------------------
# Settings unit test (regression guard for the missing-field bug)
# ---------------------------------------------------------------------------


def test_settings_has_agent_key_creation_rate_per_hour() -> None:
    """Settings must expose agent_key_creation_rate_per_hour so the rate limiter works."""
    from app.core.config import get_settings

    settings = get_settings()
    assert hasattr(settings, "agent_key_creation_rate_per_hour")
    assert isinstance(settings.agent_key_creation_rate_per_hour, int)
    assert settings.agent_key_creation_rate_per_hour > 0


def test_settings_has_agent_key_default_ttl_days() -> None:
    """Settings must expose agent_key_default_ttl_days (TR-45 default-expiry fix)."""
    from app.core.config import get_settings

    settings = get_settings()
    assert hasattr(settings, "agent_key_default_ttl_days")
    assert isinstance(settings.agent_key_default_ttl_days, int)
    assert settings.agent_key_default_ttl_days > 0


# ---------------------------------------------------------------------------
# POST /v1/web/shares/{share_id}/agent-keys  — the regressed 500 endpoint
# ---------------------------------------------------------------------------


def test_create_agent_key_returns_201(client: TestClient) -> None:
    """POST with valid payload must return 201, not 500 (the original bug)."""
    token = login(client, "bootstrap@example.com", "super-secret")
    share_id = create_share(client, token)

    resp = client.post(
        f"/v1/web/shares/{share_id}/agent-keys",
        json={"label": "ci-test-key"},
        headers=auth_headers(token),
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["share_id"] == share_id
    assert body["key"].startswith("tr_agent_")
    assert body["label"] == "ci-test-key"
    assert "write" in body["scopes"]


def test_create_agent_key_no_label(client: TestClient) -> None:
    """Label is optional — omitting it must still return 201."""
    token = login(client, "bootstrap@example.com", "super-secret")
    share_id = create_share(client, token, path="vault/nolabel.md")

    resp = client.post(
        f"/v1/web/shares/{share_id}/agent-keys",
        json={},
        headers=auth_headers(token),
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["label"] is None


def test_create_agent_key_with_expiry(client: TestClient) -> None:
    """expires_at in the future must be accepted."""
    token = login(client, "bootstrap@example.com", "super-secret")
    share_id = create_share(client, token, path="vault/expiry.md")

    future = (datetime.now(timezone.utc) + timedelta(days=30)).isoformat()
    resp = client.post(
        f"/v1/web/shares/{share_id}/agent-keys",
        json={"label": "expiring-key", "expires_at": future},
        headers=auth_headers(token),
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["expires_at"] is not None


def test_create_agent_key_omitted_expiry_gets_default_ttl(client: TestClient) -> None:
    """TR-45: omitting expires_at must apply the default TTL, not create an
    unbounded key. Before the fix this asserted `expires_at is None` (a stale
    access grant that never expires); the audit found 37 such keys in prod.
    """
    from app.core.config import get_settings

    token = login(client, "bootstrap@example.com", "super-secret")
    share_id = create_share(client, token, path="vault/default-ttl.md")

    before = datetime.now(timezone.utc)
    resp = client.post(
        f"/v1/web/shares/{share_id}/agent-keys",
        json={"label": "no-expiry-specified"},
        headers=auth_headers(token),
    )
    after = datetime.now(timezone.utc)
    assert resp.status_code == 201, resp.text

    expires_at = datetime.fromisoformat(resp.json()["expires_at"])
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)

    ttl_days = get_settings().agent_key_default_ttl_days
    assert before + timedelta(days=ttl_days) <= expires_at <= after + timedelta(days=ttl_days)

    # Must show up as active in the list, same as an explicit-future-expiry key.
    list_resp = client.get(
        f"/v1/web/shares/{share_id}/agent-keys",
        headers=auth_headers(token),
    )
    key_in_list = next(k for k in list_resp.json() if k["label"] == "no-expiry-specified")
    assert key_in_list["is_active"]


def test_create_agent_key_explicit_expiry_not_overridden_by_default(client: TestClient) -> None:
    """An explicitly-supplied expires_at must be used exactly as given, not
    silently replaced by the default TTL (non-breaking for existing callers).
    """
    token = login(client, "bootstrap@example.com", "super-secret")
    share_id = create_share(client, token, path="vault/explicit-not-overridden.md")

    # Deliberately shorter than the default TTL so the two are distinguishable.
    explicit = datetime.now(timezone.utc) + timedelta(days=1)
    resp = client.post(
        f"/v1/web/shares/{share_id}/agent-keys",
        json={"label": "explicit-short-lived", "expires_at": explicit.isoformat()},
        headers=auth_headers(token),
    )
    assert resp.status_code == 201, resp.text

    expires_at = datetime.fromisoformat(resp.json()["expires_at"])
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    assert abs((expires_at - explicit).total_seconds()) < 5


def test_create_agent_key_empty_label_rejected(client: TestClient) -> None:
    """Empty string label must be rejected with 422."""
    token = login(client, "bootstrap@example.com", "super-secret")
    share_id = create_share(client, token, path="vault/empty-label.md")

    resp = client.post(
        f"/v1/web/shares/{share_id}/agent-keys",
        json={"label": ""},
        headers=auth_headers(token),
    )
    assert resp.status_code == 422, resp.text


def test_create_agent_key_blank_label_rejected(client: TestClient) -> None:
    """Whitespace-only label must be rejected with 422."""
    token = login(client, "bootstrap@example.com", "super-secret")
    share_id = create_share(client, token, path="vault/blank-label.md")

    resp = client.post(
        f"/v1/web/shares/{share_id}/agent-keys",
        json={"label": "   "},
        headers=auth_headers(token),
    )
    assert resp.status_code == 422, resp.text


def test_create_agent_key_past_expiry_rejected(client: TestClient) -> None:
    token = login(client, "bootstrap@example.com", "super-secret")
    share_id = create_share(client, token, path="vault/past.md")

    past = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    resp = client.post(
        f"/v1/web/shares/{share_id}/agent-keys",
        json={"expires_at": past},
        headers=auth_headers(token),
    )
    assert resp.status_code == 422, resp.text


def test_create_agent_key_requires_auth(client: TestClient) -> None:
    token = login(client, "bootstrap@example.com", "super-secret")
    share_id = create_share(client, token, path="vault/noauth.md")

    resp = client.post(f"/v1/web/shares/{share_id}/agent-keys", json={})
    assert resp.status_code == 401


def test_create_agent_key_share_not_found(client: TestClient) -> None:
    token = login(client, "bootstrap@example.com", "super-secret")
    fake_id = "00000000-0000-0000-0000-000000000000"

    resp = client.post(
        f"/v1/web/shares/{fake_id}/agent-keys",
        json={},
        headers=auth_headers(token),
    )
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# GET /v1/web/shares/{share_id}/agent-keys
# ---------------------------------------------------------------------------


def test_list_agent_keys(client: TestClient) -> None:
    token = login(client, "bootstrap@example.com", "super-secret")
    share_id = create_share(client, token, path="vault/list.md")

    # Create two keys
    for i in range(2):
        r = client.post(
            f"/v1/web/shares/{share_id}/agent-keys",
            json={"label": f"key-{i}"},
            headers=auth_headers(token),
        )
        assert r.status_code == 201

    resp = client.get(
        f"/v1/web/shares/{share_id}/agent-keys",
        headers=auth_headers(token),
    )
    assert resp.status_code == 200
    keys = resp.json()
    assert len(keys) == 2
    assert all(k["is_active"] for k in keys)
    # raw key must never appear in list response
    assert all("key" not in k for k in keys)


# ---------------------------------------------------------------------------
# DELETE /v1/web/shares/{share_id}/agent-keys/{key_id}
# ---------------------------------------------------------------------------


def test_revoke_agent_key(client: TestClient) -> None:
    token = login(client, "bootstrap@example.com", "super-secret")
    share_id = create_share(client, token, path="vault/revoke.md")

    create_resp = client.post(
        f"/v1/web/shares/{share_id}/agent-keys",
        json={"label": "to-revoke"},
        headers=auth_headers(token),
    )
    assert create_resp.status_code == 201
    key_id = create_resp.json()["id"]

    revoke_resp = client.delete(
        f"/v1/web/shares/{share_id}/agent-keys/{key_id}",
        headers=auth_headers(token),
    )
    assert revoke_resp.status_code == 200
    assert revoke_resp.json()["id"] == key_id
    assert revoke_resp.json()["revoked_at"] is not None

    # Key should appear as inactive in the list
    list_resp = client.get(
        f"/v1/web/shares/{share_id}/agent-keys",
        headers=auth_headers(token),
    )
    assert list_resp.status_code == 200
    key_in_list = next(k for k in list_resp.json() if k["id"] == key_id)
    assert not key_in_list["is_active"]
