"""GET /v1/web/agent-key — a key describes itself and its share, no share in the path.

Mesh holds only the agent key; it has no Team Relay user token and so cannot
list shares. The key is bound to exactly one share, so the key alone must be
enough to learn which share that is.
"""

from __future__ import annotations

import uuid
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from app.core import security
from tests.test_agent_key_self_describe import make_agent_key, make_folder_share, make_user


@pytest.fixture
def web_enabled(monkeypatch):
    monkeypatch.setenv("WEB_PUBLISH_DOMAIN", "docs.test.com")
    from app.core.config import get_settings

    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


URL = "/v1/web/agent-key"


def call(client: TestClient, raw_key: str | None):
    headers = {"X-Agent-Key": raw_key} if raw_key is not None else {}
    return client.get(URL, headers=headers)


def _owner_and_share(db_session):
    owner = make_user(db_session, f"o-{uuid.uuid4().hex[:8]}@x.com")
    share = make_folder_share(db_session, owner, f"s{uuid.uuid4().hex[:8]}")
    return owner, share


class TestValidKey:
    def test_returns_key_metadata_and_its_share(self, db_session, client, web_enabled):
        owner, share = _owner_and_share(db_session)
        # A second share with its own key: the lookup must pick the right one.
        other = make_folder_share(db_session, owner, f"s{uuid.uuid4().hex[:8]}")
        make_agent_key(db_session, other, owner.id, "read")
        raw, ak = make_agent_key(db_session, share, owner.id, "write,read")

        resp = call(client, raw)
        assert resp.status_code == 200, resp.text
        body = resp.json()

        assert body["id"] == str(ak.id)
        assert body["share_id"] == str(share.id)
        assert body["scopes"] == ["read", "write"]
        assert body["share"] == {
            "id": str(share.id),
            "kind": "folder",
            "path": share.path,
            "web_slug": share.web_slug,
            "web_published": True,
        }

    def test_write_only_key_may_call_it(self, db_session, client, web_enabled):
        owner, share = _owner_and_share(db_session)
        raw, _ = make_agent_key(db_session, share, owner.id, "write")

        resp = call(client, raw)
        assert resp.status_code == 200, resp.text
        assert resp.json()["scopes"] == ["write"]

    def test_does_not_touch_last_used_at(self, db_session, client, web_enabled):
        owner, share = _owner_and_share(db_session)
        raw, ak = make_agent_key(db_session, share, owner.id, "read")

        assert call(client, raw).status_code == 200
        db_session.refresh(ak)
        assert ak.last_used_at is None

    def test_never_leaks_key_material_or_owner(self, db_session, client, web_enabled):
        owner, share = _owner_and_share(db_session)
        raw, ak = make_agent_key(db_session, share, owner.id, "read")

        text = call(client, raw).text
        for secret in (raw, ak.key_hash, owner.email, str(owner.id)):
            assert secret not in text
        assert "created_by" not in text
        assert "revoked_at" not in text


class TestRejected:
    def test_no_header_is_401(self, db_session, client, web_enabled):
        resp = call(client, None)
        assert resp.status_code == 401

    def test_unknown_key_is_401(self, db_session, client, web_enabled):
        resp = call(client, "tr_agent_" + "0" * 48)
        assert resp.status_code == 401
        assert "Invalid agent key" in resp.text

    def test_revoked_key_is_403(self, db_session, client, web_enabled):
        owner, share = _owner_and_share(db_session)
        raw, _ = make_agent_key(db_session, share, owner.id, "read", revoked_at=security.utcnow())

        resp = call(client, raw)
        assert resp.status_code == 403
        assert "revoked" in resp.text

    def test_expired_key_is_403(self, db_session, client, web_enabled):
        owner, share = _owner_and_share(db_session)
        raw, _ = make_agent_key(
            db_session, share, owner.id, "read", expires_at=security.utcnow() - timedelta(days=1)
        )

        resp = call(client, raw)
        assert resp.status_code == 403
        assert "expired" in resp.text

    def test_creator_without_standing_is_403(self, db_session, client, web_enabled):
        owner, share = _owner_and_share(db_session)
        stranger = make_user(db_session, f"x-{uuid.uuid4().hex[:8]}@x.com")
        raw, _ = make_agent_key(db_session, share, stranger.id, "read")

        resp = call(client, raw)
        assert resp.status_code == 403
        assert "no longer an owner or member" in resp.text

    def test_web_publish_disabled_is_404(self, db_session, client, monkeypatch):
        from app.core.config import get_settings

        owner, share = _owner_and_share(db_session)
        raw, _ = make_agent_key(db_session, share, owner.id, "read")
        monkeypatch.delenv("WEB_PUBLISH_DOMAIN", raising=False)
        get_settings.cache_clear()
        try:
            assert call(client, raw).status_code == 404
        finally:
            get_settings.cache_clear()
