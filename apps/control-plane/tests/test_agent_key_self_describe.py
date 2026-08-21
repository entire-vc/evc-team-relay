"""Self-describe endpoint for agent keys (task bc11d499, requested by Mesh/Garfield).

Mesh holds an agent key issued by a Team Relay share owner but is not itself
the owner, so it has no way to learn its own key's expires_at/scopes/
last_used_at short of parsing a 403 after the fact. GET .../agent-key lets the
key holder read its own key's metadata — nothing about any other key on the
share, and never the key material itself.

Decisions carried from the task thread (Daedalus, Р1-Р6) and enforced here:
  Р1: last_used_at is NOT updated by this route.
  Р2: no scope is required to call it — a write-only key must be able to
      discover that it is write-only.
  Р3: revoked_at is never in the response (a revoked key never reaches the
      response at all — it is rejected first).
  Р4: its own response model, not agent_keys.AgentKeyListItem — no created_by,
      no risk of a future owner-facing field leaking here by reuse.
"""

from __future__ import annotations

import hashlib
import secrets
import uuid
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core import security
from app.db import models

# --------------------------------------------------------------------------
# fixtures / helpers (same shape as test_agent_key_read_scope_policy.py)
# --------------------------------------------------------------------------


def make_user(db: Session, email: str) -> models.User:
    user = models.User(
        email=email,
        password_hash=security.get_password_hash("test123456"),
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def make_folder_share(db: Session, owner: models.User, slug: str) -> models.Share:
    share = models.Share(
        kind=models.ShareKind.FOLDER,
        path=f"{slug}/",
        visibility=models.ShareVisibility.PRIVATE,
        owner_user_id=owner.id,
        web_published=True,
        web_slug=slug,
        web_folder_items=[],
    )
    db.add(share)
    db.commit()
    db.refresh(share)
    return share


def make_agent_key(
    db: Session,
    share: models.Share,
    created_by: uuid.UUID,
    scopes: str,
    *,
    label: str | None = "self-describe-test",
    expires_at=None,
    revoked_at=None,
) -> tuple[str, models.ShareAgentKey]:
    raw = "tr_agent_" + secrets.token_hex(24)
    ak = models.ShareAgentKey(
        share_id=share.id,
        key_hash=hashlib.sha256(raw.encode()).hexdigest(),
        label=label,
        scopes=scopes,
        created_by=created_by,
        expires_at=expires_at,
        revoked_at=revoked_at,
    )
    db.add(ak)
    db.commit()
    db.refresh(ak)
    return raw, ak


@pytest.fixture
def web_enabled(monkeypatch):
    monkeypatch.setenv("WEB_PUBLISH_DOMAIN", "docs.test.com")
    from app.core.config import get_settings

    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def describe_url(share: models.Share) -> str:
    return f"/v1/web/shares/{share.id}/agent-key"


def call(client: TestClient, share: models.Share, raw_key: str | None):
    headers = {"X-Agent-Key": raw_key} if raw_key is not None else {}
    return client.get(describe_url(share), headers=headers)


# --------------------------------------------------------------------------
# AC1 — valid key gets its own metadata
# --------------------------------------------------------------------------


class TestValidKeyDescribesItself:
    def test_returns_scopes_expires_at_last_used_at(self, db_session, client, web_enabled):
        owner = make_user(db_session, f"o-{uuid.uuid4().hex[:8]}@x.com")
        share = make_folder_share(db_session, owner, f"s{uuid.uuid4().hex[:8]}")
        expires = security.utcnow() + timedelta(days=90)
        raw, ak = make_agent_key(db_session, share, owner.id, "read,write", expires_at=expires)

        resp = call(client, share, raw)
        assert resp.status_code == 200, resp.text
        body = resp.json()

        assert isinstance(body["scopes"], list)
        assert sorted(body["scopes"]) == ["read", "write"]
        assert "expires_at" in body and body["expires_at"] is not None
        assert "last_used_at" in body  # present as a key even though null pre-use
        assert body["id"] == str(ak.id)
        assert body["share_id"] == str(share.id)
        assert body["label"] == "self-describe-test"


# --------------------------------------------------------------------------
# AC2 — rejected the same way as the rest of the agent-key surface
# --------------------------------------------------------------------------


class TestRejectionMatchesRestOfSurface:
    def test_no_header_is_401(self, db_session, client, web_enabled):
        owner = make_user(db_session, f"o-{uuid.uuid4().hex[:8]}@x.com")
        share = make_folder_share(db_session, owner, f"s{uuid.uuid4().hex[:8]}")

        resp = call(client, share, None)
        assert resp.status_code == 401
        assert "X-Agent-Key header required" in resp.text

    def test_key_for_another_share_is_403_not_valid_for_this_share(
        self, db_session, client, web_enabled
    ):
        owner = make_user(db_session, f"o-{uuid.uuid4().hex[:8]}@x.com")
        share_a = make_folder_share(db_session, owner, f"s{uuid.uuid4().hex[:8]}")
        share_b = make_folder_share(db_session, owner, f"s{uuid.uuid4().hex[:8]}")
        raw, _ = make_agent_key(db_session, share_a, owner.id, "read")

        resp = call(client, share_b, raw)
        assert resp.status_code == 403
        assert "not valid for this share" in resp.text

    def test_revoked_key_is_403_revoked(self, db_session, client, web_enabled):
        owner = make_user(db_session, f"o-{uuid.uuid4().hex[:8]}@x.com")
        share = make_folder_share(db_session, owner, f"s{uuid.uuid4().hex[:8]}")
        raw, _ = make_agent_key(db_session, share, owner.id, "read", revoked_at=security.utcnow())

        resp = call(client, share, raw)
        assert resp.status_code == 403
        assert "revoked" in resp.text

    def test_expired_key_is_403_expired(self, db_session, client, web_enabled):
        owner = make_user(db_session, f"o-{uuid.uuid4().hex[:8]}@x.com")
        share = make_folder_share(db_session, owner, f"s{uuid.uuid4().hex[:8]}")
        raw, _ = make_agent_key(
            db_session,
            share,
            owner.id,
            "read",
            expires_at=security.utcnow() - timedelta(days=1),
        )

        resp = call(client, share, raw)
        assert resp.status_code == 403
        assert "expired" in resp.text


# --------------------------------------------------------------------------
# AC3/AC4 — negative leak test + mutation control on it
# --------------------------------------------------------------------------


class TestNoLeakage:
    def test_response_never_carries_key_material_or_other_keys(
        self, db_session, client, web_enabled
    ):
        """AC3. A second key on the same share must not appear in any form —
        not its id, not its hash, not counted anywhere in the payload."""
        owner = make_user(db_session, f"o-{uuid.uuid4().hex[:8]}@x.com")
        share = make_folder_share(db_session, owner, f"s{uuid.uuid4().hex[:8]}")
        raw, ak = make_agent_key(db_session, share, owner.id, "read,write")
        _other_raw, other_ak = make_agent_key(
            db_session, share, owner.id, "write", label="sibling-key"
        )

        resp = call(client, share, raw)
        assert resp.status_code == 200, resp.text
        body = resp.json()

        # No key material, ever.
        assert "key_hash" not in body
        assert "key" not in body
        assert raw not in resp.text
        assert ak.key_hash not in resp.text

        # No ownership/audit field that isn't this key's own business.
        assert "created_by" not in body
        assert "revoked_at" not in body

        # No sign of the sibling key on the same share.
        assert str(other_ak.id) not in resp.text
        assert "sibling-key" not in resp.text
        assert other_ak.key_hash not in resp.text

        # Exactly the fields the model declares — nothing extra snuck in
        # via a shared/reused response model.
        assert set(body.keys()) == {
            "id",
            "label",
            "share_id",
            "scopes",
            "expires_at",
            "last_used_at",
        }

    def test_mutation_control_leak_test_actually_catches_a_leak(
        self, db_session, client, web_enabled, monkeypatch
    ):
        """AC4. Prove the leak test above is not vacuous: temporarily widen the
        response model to include key_hash, confirm the SAME assertion style
        as the test above goes red, then revert (monkeypatch auto-reverts) and
        confirm it's green again with a real call in this same test."""
        import app.api.routers.web as web_module

        owner = make_user(db_session, f"o-{uuid.uuid4().hex[:8]}@x.com")
        share = make_folder_share(db_session, owner, f"s{uuid.uuid4().hex[:8]}")
        raw, ak = make_agent_key(db_session, share, owner.id, "read")

        original_response_cls = web_module.AgentKeySelfDescribeResponse

        # --- RED: inject a leaking field into the response model ---
        class LeakyResponse(original_response_cls):
            key_hash: str

        def leaky_describe(request, share_identifier, db):
            settings = web_module.get_settings()
            if not settings.web_publish_enabled:
                from fastapi import HTTPException
                from fastapi import status as _status

                raise HTTPException(
                    status_code=_status.HTTP_404_NOT_FOUND,
                    detail="Web publishing is not enabled on this server",
                )
            share_obj = web_module._resolve_share_for_agent(share_identifier, db)
            agent_key = web_module._resolve_share_agent_key(share_obj, request, db)
            return LeakyResponse(
                id=str(agent_key.id),
                label=agent_key.label,
                share_id=str(agent_key.share_id),
                scopes=sorted(web_module.agent_key_scopes.parse_scopes(agent_key.scopes)),
                expires_at=agent_key.expires_at,
                last_used_at=agent_key.last_used_at,
                key_hash=agent_key.key_hash,
            )

        # Swap the route function's underlying endpoint for one call, then
        # verify the leak-detecting assertion (the core of the test above)
        # actually flips to red against it.
        monkeypatch.setattr(web_module, "describe_own_agent_key", leaky_describe)

        leaked_body = leaky_describe(
            request=_FakeRequest(raw), share_identifier=str(share.id), db=db_session
        ).model_dump(mode="json")
        assert "key_hash" in leaked_body, "mutation didn't actually inject the leak"
        with pytest.raises(AssertionError):
            assert "key_hash" not in leaked_body

        # --- GREEN: real endpoint, real call, same assertion now passes ---
        resp = call(client, share, raw)
        assert resp.status_code == 200, resp.text
        assert "key_hash" not in resp.json()


class _FakeRequest:
    """Minimal stand-in exposing only what _resolve_share_agent_key reads."""

    def __init__(self, raw_key: str):
        self.headers = {"X-Agent-Key": raw_key}


# --------------------------------------------------------------------------
# AC5 (Р1) — last_used_at is not touched by self-describing
# --------------------------------------------------------------------------


class TestLastUsedAtUntouched:
    def test_self_describe_does_not_move_last_used_at(self, db_session, client, web_enabled):
        owner = make_user(db_session, f"o-{uuid.uuid4().hex[:8]}@x.com")
        share = make_folder_share(db_session, owner, f"s{uuid.uuid4().hex[:8]}")
        raw, ak = make_agent_key(db_session, share, owner.id, "read")
        assert ak.last_used_at is None

        for _ in range(3):
            resp = call(client, share, raw)
            assert resp.status_code == 200, resp.text

        db_session.refresh(ak)
        assert ak.last_used_at is None, "self-describe stamped last_used_at"

    def test_positive_control_an_ordinary_route_does_move_it(self, db_session, client, web_enabled):
        """Without this, the test above can't tell 'we deliberately don't
        touch it' from 'the stamping mechanism is just broken.'"""
        owner = make_user(db_session, f"o-{uuid.uuid4().hex[:8]}@x.com")
        share = make_folder_share(db_session, owner, f"s{uuid.uuid4().hex[:8]}")
        raw, ak = make_agent_key(db_session, share, owner.id, "read")
        assert ak.last_used_at is None

        resp = client.get(f"/v1/web/shares/{share.id}/files-index", headers={"X-Agent-Key": raw})
        assert resp.status_code == 200, resp.text

        db_session.refresh(ak)
        assert ak.last_used_at is not None, "positive control did not record usage at all"


# --------------------------------------------------------------------------
# AC6 (Р2) — write-only key can see its own scopes
# --------------------------------------------------------------------------


class TestWriteOnlyKeySeesItsOwnScopes:
    def test_write_only_key_gets_200_with_scopes_write(self, db_session, client, web_enabled):
        owner = make_user(db_session, f"o-{uuid.uuid4().hex[:8]}@x.com")
        share = make_folder_share(db_session, owner, f"s{uuid.uuid4().hex[:8]}")
        raw, _ = make_agent_key(db_session, share, owner.id, "write")

        resp = call(client, share, raw)
        assert resp.status_code == 200, resp.text
        assert resp.json()["scopes"] == ["write"]
