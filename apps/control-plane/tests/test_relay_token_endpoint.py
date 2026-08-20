"""Integration tests for POST /tokens/relay endpoint.

Level 2: Uses TestClient with in-memory SQLite — verifies the full token
issuance flow including permissions, CWT structure, and error cases.
"""

from __future__ import annotations

import base64
import hashlib
import secrets
import uuid

import cbor2
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.security import utcnow
from app.db import models

# ── Helpers ──────────────────────────────────────────────────


def auth_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def login(client: TestClient, email: str, password: str) -> str:
    resp = client.post("/auth/login", json={"email": email, "password": password})
    assert resp.status_code == 200, resp.text
    return resp.json()["access_token"]


def create_user(client: TestClient, admin_token: str, email: str, password: str) -> str:
    resp = client.post(
        "/admin/users",
        json={"email": email, "password": password, "is_admin": False, "is_active": True},
        headers=auth_headers(admin_token),
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def create_share(
    client: TestClient,
    admin_token: str,
    kind: str = "doc",
    path: str = "vault/note.md",
    visibility: str = "private",
    password: str | None = None,
) -> str:
    payload: dict = {"kind": kind, "path": path, "visibility": visibility}
    if password:
        payload["password"] = password
    resp = client.post("/shares", json=payload, headers=auth_headers(admin_token))
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def add_member(client: TestClient, admin_token: str, share_id: str, user_id: str, role: str):
    resp = client.post(
        f"/shares/{share_id}/members",
        json={"user_id": user_id, "role": role},
        headers=auth_headers(admin_token),
    )
    assert resp.status_code == 201, resp.text


def make_agent_key(
    db_session: Session,
    share_id: str,
    created_by: uuid.UUID,
    scopes: str = "write",
    revoked: bool = False,
) -> str:
    """Insert a ShareAgentKey row directly (mirrors test_agent_key_authorization.py)
    and return the raw key string to send as X-Agent-Key."""
    raw_key = "tr_agent_" + secrets.token_hex(24)
    key_hash = hashlib.sha256(raw_key.encode()).hexdigest()
    ak = models.ShareAgentKey(
        share_id=uuid.UUID(share_id),
        key_hash=key_hash,
        label="tr07-test-key",
        scopes=scopes,
        created_by=created_by,
        revoked_at=(utcnow() if revoked else None),
    )
    db_session.add(ak)
    db_session.commit()
    return raw_key


def get_share_owner_id(db_session: Session, share_id: str) -> uuid.UUID:
    share = db_session.execute(
        select(models.Share).where(models.Share.id == uuid.UUID(share_id))
    ).scalar_one()
    return share.owner_user_id


def decode_cwt_claims(token_b64: str) -> dict:
    """Decode CWT token and return named claims."""
    padding = 4 - len(token_b64) % 4
    if padding != 4:
        token_b64 += "=" * padding
    raw = base64.urlsafe_b64decode(token_b64)
    outer = cbor2.loads(raw)
    inner = outer.value
    cose = inner.value if isinstance(inner, cbor2.CBORTag) else inner
    claims_raw = cbor2.loads(cose[2])
    names = {1: "iss", 2: "sub", 3: "aud", 4: "exp", 6: "iat", -80201: "scope"}
    return {names.get(k, k): v for k, v in claims_raw.items()}


# ── Happy Path ───────────────────────────────────────────────


class TestRelayTokenHappyPath:
    def test_owner_gets_write_token(self, client: TestClient):
        """Owner of a private share can request write token."""
        token = login(client, "bootstrap@example.com", "super-secret")
        share_id = create_share(client, token)

        resp = client.post(
            "/tokens/relay",
            json={"share_id": share_id, "doc_id": share_id, "mode": "write"},
            headers=auth_headers(token),
        )
        assert resp.status_code == 200
        data = resp.json()

        assert data["relay_url"].startswith("wss://")
        # #b1e88884: must land on relay-server's non-deprecated per-doc route
        # (/d/:doc_id/ws/:doc_id2), not the flat /doc/ws/:doc_id it deprecated.
        assert data["relay_url"].endswith(f"/d/{share_id}/ws")
        assert "/doc/ws" not in data["relay_url"]
        assert data["token"]
        assert data["expires_at"]

    def test_owner_gets_read_token(self, client: TestClient):
        token = login(client, "bootstrap@example.com", "super-secret")
        share_id = create_share(client, token)

        resp = client.post(
            "/tokens/relay",
            json={"share_id": share_id, "doc_id": share_id, "mode": "read"},
            headers=auth_headers(token),
        )
        assert resp.status_code == 200

    def test_token_is_valid_cwt(self, client: TestClient):
        """Returned token must be a valid CWT with correct structure."""
        admin_token = login(client, "bootstrap@example.com", "super-secret")
        share_id = create_share(client, admin_token)

        resp = client.post(
            "/tokens/relay",
            json={"share_id": share_id, "doc_id": share_id, "mode": "write"},
            headers=auth_headers(admin_token),
        )
        data = resp.json()
        claims = decode_cwt_claims(data["token"])

        # #f975dd60: our evc-relay-server fork requires iss in its VALID_ISSUERS
        # allowlist and requires aud — "relay-control-plane" + no-aud (the old
        # expectations here) is exactly the pair of defects that left collab
        # broken in prod for 6 months. See Settings.relay_token_issuer /
        # effective_relay_audience.
        assert claims["iss"] == "relay-server"
        assert claims["scope"] == f"doc:{share_id}:rw"
        assert "iat" in claims
        # H6: exp is now required for TTL enforcement
        assert "exp" in claims, "exp missing — H6 security regression"
        assert claims["exp"] > claims["iat"]
        # aud MUST appear — relay-server rejects tokens missing it. Derived from
        # RELAY_PUBLIC_URL=wss://relay.test (conftest.py) -> https://relay.test.
        assert (
            claims.get("aud") == "https://relay.test"
        ), "aud missing or wrong — relay-server requires it (#f975dd60)"

    def test_issuance_increments_relay_tokens_issued_metric(self, client: TestClient):
        """relay_tokens_issued_total must actually increment on issuance (#188f683c).

        The metric was declared in metrics.py but never incremented anywhere —
        the "Relay Tokens Issued (hourly)" Grafana panel could never show data.
        """
        import re

        def _read_metric(mode: str) -> float:
            body = client.get("/metrics").text
            m = re.search(
                rf'^relay_tokens_issued_total\{{mode="{mode}"\}} ([\d.]+)$', body, re.MULTILINE
            )
            return float(m.group(1)) if m else 0.0

        admin_token = login(client, "bootstrap@example.com", "super-secret")
        share_id = create_share(client, admin_token)
        before = _read_metric("write")

        resp = client.post(
            "/tokens/relay",
            json={"share_id": share_id, "doc_id": share_id, "mode": "write"},
            headers=auth_headers(admin_token),
        )
        assert resp.status_code == 200

        after = _read_metric("write")
        assert after == before + 1, f"expected +1 write token issued, got {before} -> {after}"

    def test_read_mode_scope(self, client: TestClient):
        admin_token = login(client, "bootstrap@example.com", "super-secret")
        share_id = create_share(client, admin_token)

        resp = client.post(
            "/tokens/relay",
            json={"share_id": share_id, "doc_id": share_id, "mode": "read"},
            headers=auth_headers(admin_token),
        )
        claims = decode_cwt_claims(resp.json()["token"])
        assert claims["scope"] == f"doc:{share_id}:r"

    def test_token_verifiable_with_public_key(self, client: TestClient):
        """Token from /tokens/relay can be verified with key from /keys/public."""
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

        from app.core.security import verify_relay_token_cwt

        admin_token = login(client, "bootstrap@example.com", "super-secret")
        share_id = create_share(client, admin_token)

        # Get public key
        key_resp = client.get("/keys/public")
        assert key_resp.status_code == 200
        pub_b64 = key_resp.json()["public_key"]
        pub_bytes = base64.b64decode(pub_b64)
        public_key = Ed25519PublicKey.from_public_bytes(pub_bytes)

        # Get token
        token_resp = client.post(
            "/tokens/relay",
            json={"share_id": share_id, "doc_id": share_id, "mode": "write"},
            headers=auth_headers(admin_token),
        )
        cwt_token = token_resp.json()["token"]

        # Verify — must not raise
        claims = verify_relay_token_cwt(public_key, cwt_token)
        assert claims["scope"] == f"doc:{share_id}:rw"


# ── Permission Tests ─────────────────────────────────────────


class TestRelayTokenPermissions:
    def test_editor_can_write(self, client: TestClient):
        admin_token = login(client, "bootstrap@example.com", "super-secret")
        share_id = create_share(client, admin_token)
        user_id = create_user(client, admin_token, "editor@example.com", "pass12345")
        add_member(client, admin_token, share_id, user_id, "editor")

        user_token = login(client, "editor@example.com", "pass12345")
        resp = client.post(
            "/tokens/relay",
            json={"share_id": share_id, "doc_id": share_id, "mode": "write"},
            headers=auth_headers(user_token),
        )
        assert resp.status_code == 200

        claims = decode_cwt_claims(resp.json()["token"])
        assert claims["scope"].endswith(":rw")

    def test_viewer_can_read(self, client: TestClient):
        admin_token = login(client, "bootstrap@example.com", "super-secret")
        share_id = create_share(client, admin_token)
        user_id = create_user(client, admin_token, "viewer@example.com", "pass12345")
        add_member(client, admin_token, share_id, user_id, "viewer")

        user_token = login(client, "viewer@example.com", "pass12345")
        resp = client.post(
            "/tokens/relay",
            json={"share_id": share_id, "doc_id": share_id, "mode": "read"},
            headers=auth_headers(user_token),
        )
        assert resp.status_code == 200

        claims = decode_cwt_claims(resp.json()["token"])
        assert claims["scope"].endswith(":r")

    def test_viewer_cannot_write(self, client: TestClient):
        admin_token = login(client, "bootstrap@example.com", "super-secret")
        share_id = create_share(client, admin_token)
        user_id = create_user(client, admin_token, "viewer2@example.com", "pass12345")
        add_member(client, admin_token, share_id, user_id, "viewer")

        user_token = login(client, "viewer2@example.com", "pass12345")
        resp = client.post(
            "/tokens/relay",
            json={"share_id": share_id, "doc_id": share_id, "mode": "write"},
            headers=auth_headers(user_token),
        )
        assert resp.status_code == 403

    def test_public_share_read_without_auth(self, client: TestClient):
        """Public shares allow unauthenticated read access."""
        admin_token = login(client, "bootstrap@example.com", "super-secret")
        share_id = create_share(client, admin_token, visibility="public")

        resp = client.post(
            "/tokens/relay",
            json={"share_id": share_id, "doc_id": share_id, "mode": "read"},
        )
        assert resp.status_code == 200

    def test_public_share_write_requires_auth(self, client: TestClient):
        """Public shares still require auth for write."""
        admin_token = login(client, "bootstrap@example.com", "super-secret")
        share_id = create_share(client, admin_token, visibility="public")

        resp = client.post(
            "/tokens/relay",
            json={"share_id": share_id, "doc_id": share_id, "mode": "write"},
        )
        assert resp.status_code == 403

    def test_protected_share_requires_password(self, client: TestClient):
        admin_token = login(client, "bootstrap@example.com", "super-secret")
        share_id = create_share(client, admin_token, visibility="protected", password="secret-pass")

        # Without password → 403
        resp = client.post(
            "/tokens/relay",
            json={"share_id": share_id, "doc_id": share_id, "mode": "read"},
        )
        assert resp.status_code == 403

        # With correct password → 200
        resp = client.post(
            "/tokens/relay",
            json={
                "share_id": share_id,
                "doc_id": share_id,
                "mode": "read",
                "password": "secret-pass",
            },
        )
        assert resp.status_code == 200

    def test_stranger_cannot_access_private_share(self, client: TestClient):
        admin_token = login(client, "bootstrap@example.com", "super-secret")
        share_id = create_share(client, admin_token)
        create_user(client, admin_token, "stranger@example.com", "pass12345")

        stranger_token = login(client, "stranger@example.com", "pass12345")
        resp = client.post(
            "/tokens/relay",
            json={"share_id": share_id, "doc_id": share_id, "mode": "read"},
            headers=auth_headers(stranger_token),
        )
        assert resp.status_code == 403


# ── Folder Share Tests ───────────────────────────────────────


class TestRelayTokenFolderShares:
    def test_folder_share_with_file_path(self, client: TestClient):
        admin_token = login(client, "bootstrap@example.com", "super-secret")
        share_id = create_share(client, admin_token, kind="folder", path="vault/project")

        resp = client.post(
            "/tokens/relay",
            json={
                "share_id": share_id,
                "doc_id": "some-file-id",
                "mode": "write",
                "file_path": "vault/project/notes.md",
            },
            headers=auth_headers(admin_token),
        )
        assert resp.status_code == 200

    def test_folder_share_any_doc_id_accepted(self, client: TestClient):
        """Folder shares accept any doc_id — authorization is via membership, not path."""
        admin_token = login(client, "bootstrap@example.com", "super-secret")
        share_id = create_share(client, admin_token, kind="folder", path="vault/project")

        resp = client.post(
            "/tokens/relay",
            json={
                "share_id": share_id,
                "doc_id": "some-file-id",
                "mode": "read",
            },
            headers=auth_headers(admin_token),
        )
        assert resp.status_code == 200

    def test_folder_share_sync_folder_itself(self, client: TestClient):
        """When doc_id == share_id, it's syncing the folder itself."""
        admin_token = login(client, "bootstrap@example.com", "super-secret")
        share_id = create_share(client, admin_token, kind="folder", path="vault/project")

        resp = client.post(
            "/tokens/relay",
            json={"share_id": share_id, "doc_id": share_id, "mode": "write"},
            headers=auth_headers(admin_token),
        )
        assert resp.status_code == 200


# ── H6 Confused-Deputy Tests ─────────────────────────────────


class TestRelayTokenConfusedDeputy:
    """A client authorized for share A must not be able to obtain a
    write-scoped token for a DIFFERENT share's real document by supplying
    that other share's own id as doc_id — the documented convention for
    "sync the whole share" is doc_id == share_id, so a client that only has
    access to share A cannot use share B's id as a skeleton-key doc_id."""

    def test_cross_share_doc_id_rejected_without_foreign_access(self, client: TestClient):
        """Editor of share A + doc_id = share B's id (no access to B) -> 403."""
        admin_token = login(client, "bootstrap@example.com", "super-secret")
        share_a = create_share(client, admin_token, kind="doc", path="vault/a.md")
        share_b = create_share(client, admin_token, kind="doc", path="vault/b.md")

        user_id = create_user(client, admin_token, "attacker@example.com", "pass12345")
        add_member(client, admin_token, share_a, user_id, "editor")

        attacker_token = login(client, "attacker@example.com", "pass12345")
        resp = client.post(
            "/tokens/relay",
            json={"share_id": share_a, "doc_id": share_b, "mode": "write"},
            headers=auth_headers(attacker_token),
        )
        assert resp.status_code == 403

    def test_cross_share_doc_id_allowed_with_foreign_access(self, client: TestClient):
        """Owner has access to both shares -> requesting B's id as doc_id under A is fine."""
        admin_token = login(client, "bootstrap@example.com", "super-secret")
        share_a = create_share(client, admin_token, kind="doc", path="vault/a.md")
        share_b = create_share(client, admin_token, kind="doc", path="vault/b.md")

        resp = client.post(
            "/tokens/relay",
            json={"share_id": share_a, "doc_id": share_b, "mode": "write"},
            headers=auth_headers(admin_token),
        )
        assert resp.status_code == 200

    def test_doc_share_own_id_as_doc_id_still_works(self, client: TestClient):
        """Sanity: the fix doesn't break the whole-share sync happy path (doc_id == share_id)."""
        admin_token = login(client, "bootstrap@example.com", "super-secret")
        share_id = create_share(client, admin_token, kind="doc", path="vault/note.md")

        resp = client.post(
            "/tokens/relay",
            json={"share_id": share_id, "doc_id": share_id, "mode": "write"},
            headers=auth_headers(admin_token),
        )
        assert resp.status_code == 200

    def test_arbitrary_non_share_doc_id_still_accepted(self, client: TestClient):
        """A doc_id that isn't any real share's id (path or per-file UUID) is unaffected."""
        admin_token = login(client, "bootstrap@example.com", "super-secret")
        share_id = create_share(client, admin_token, kind="doc", path="vault/note.md")

        resp = client.post(
            "/tokens/relay",
            json={"share_id": share_id, "doc_id": "attacker-chosen-doc-id", "mode": "read"},
            headers=auth_headers(admin_token),
        )
        assert resp.status_code == 200


# ── Error Cases ──────────────────────────────────────────────


class TestRelayTokenErrors:
    def test_nonexistent_share_404(self, client: TestClient):
        admin_token = login(client, "bootstrap@example.com", "super-secret")
        fake_id = "00000000-0000-0000-0000-000000000000"

        resp = client.post(
            "/tokens/relay",
            json={"share_id": fake_id, "doc_id": fake_id, "mode": "read"},
            headers=auth_headers(admin_token),
        )
        assert resp.status_code == 404

    def test_invalid_share_id_format(self, client: TestClient):
        admin_token = login(client, "bootstrap@example.com", "super-secret")

        resp = client.post(
            "/tokens/relay",
            json={"share_id": "not-a-uuid", "doc_id": "x", "mode": "read"},
            headers=auth_headers(admin_token),
        )
        assert resp.status_code == 422  # Pydantic validation error

    def test_missing_share_id(self, client: TestClient):
        admin_token = login(client, "bootstrap@example.com", "super-secret")

        resp = client.post(
            "/tokens/relay",
            json={"doc_id": "x", "mode": "read"},
            headers=auth_headers(admin_token),
        )
        assert resp.status_code == 422


# ── Agent Key Auth (TR-07, #cecd6baf) ───────────────────────
#
# A ShareAgentKey should be able to obtain a relay-token scoped to its own
# share WITHOUT any User row/JWT — this is what lets the fleet stop logging
# in as the shared admin (in@entire.vc) just to reach a live CRDT doc.


class TestRelayTokenAgentKey:
    def test_write_key_gets_write_token_no_user_at_all(self, client: TestClient, db_session):
        """Core AC: X-Agent-Key alone (no Authorization header) is sufficient."""
        admin_token = login(client, "bootstrap@example.com", "super-secret")
        share_id = create_share(client, admin_token)
        owner_id = get_share_owner_id(db_session, share_id)
        raw_key = make_agent_key(db_session, share_id, created_by=owner_id, scopes="write")

        resp = client.post(
            "/tokens/relay",
            json={"share_id": share_id, "doc_id": share_id, "mode": "write"},
            headers={"X-Agent-Key": raw_key},
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["relay_url"].startswith("wss://")
        claims = decode_cwt_claims(data["token"])
        assert claims["scope"] == f"doc:{share_id}:rw"

    def test_read_only_key_gets_read_token(self, client: TestClient, db_session):
        admin_token = login(client, "bootstrap@example.com", "super-secret")
        share_id = create_share(client, admin_token)
        owner_id = get_share_owner_id(db_session, share_id)
        raw_key = make_agent_key(db_session, share_id, created_by=owner_id, scopes="read")

        resp = client.post(
            "/tokens/relay",
            json={"share_id": share_id, "doc_id": share_id, "mode": "read"},
            headers={"X-Agent-Key": raw_key},
        )
        assert resp.status_code == 200, resp.text
        claims = decode_cwt_claims(resp.json()["token"])
        assert claims["scope"] == f"doc:{share_id}:r"

    def test_read_only_key_cannot_get_write_token(self, client: TestClient, db_session):
        admin_token = login(client, "bootstrap@example.com", "super-secret")
        share_id = create_share(client, admin_token)
        owner_id = get_share_owner_id(db_session, share_id)
        raw_key = make_agent_key(db_session, share_id, created_by=owner_id, scopes="read")

        resp = client.post(
            "/tokens/relay",
            json={"share_id": share_id, "doc_id": share_id, "mode": "write"},
            headers={"X-Agent-Key": raw_key},
        )
        assert resp.status_code == 403

    def test_key_rejected_for_a_different_share(self, client: TestClient, db_session):
        admin_token = login(client, "bootstrap@example.com", "super-secret")
        share_a = create_share(client, admin_token, path="vault/a.md")
        share_b = create_share(client, admin_token, path="vault/b.md")
        owner_id = get_share_owner_id(db_session, share_a)
        raw_key = make_agent_key(db_session, share_a, created_by=owner_id, scopes="write")

        resp = client.post(
            "/tokens/relay",
            json={"share_id": share_b, "doc_id": share_b, "mode": "write"},
            headers={"X-Agent-Key": raw_key},
        )
        assert resp.status_code == 403

    def test_revoked_key_rejected(self, client: TestClient, db_session):
        admin_token = login(client, "bootstrap@example.com", "super-secret")
        share_id = create_share(client, admin_token)
        owner_id = get_share_owner_id(db_session, share_id)
        raw_key = make_agent_key(
            db_session, share_id, created_by=owner_id, scopes="write", revoked=True
        )

        resp = client.post(
            "/tokens/relay",
            json={"share_id": share_id, "doc_id": share_id, "mode": "write"},
            headers={"X-Agent-Key": raw_key},
        )
        assert resp.status_code == 403

    def test_garbage_key_rejected(self, client: TestClient):
        admin_token = login(client, "bootstrap@example.com", "super-secret")
        share_id = create_share(client, admin_token)

        resp = client.post(
            "/tokens/relay",
            json={"share_id": share_id, "doc_id": share_id, "mode": "read"},
            headers={"X-Agent-Key": "not-a-real-key"},
        )
        assert resp.status_code == 401

    def test_key_creator_removed_from_share_rejected(self, client: TestClient, db_session):
        """TR-03 standing-authority check applies here too (shared helper)."""
        admin_token = login(client, "bootstrap@example.com", "super-secret")
        share_id = create_share(client, admin_token)
        editor_id = create_user(client, admin_token, "editor-tr07@example.com", "pass12345")
        add_member(client, admin_token, share_id, editor_id, "editor")
        raw_key = make_agent_key(
            db_session, share_id, created_by=uuid.UUID(editor_id), scopes="write"
        )

        # Remove the creator from the share — remove_member's cascade-revoke
        # (TR-03) already handles keys created via the real endpoint; this
        # key was inserted directly, so exercise the standing-authority
        # check itself rather than the cascade.
        resp = client.delete(
            f"/shares/{share_id}/members/{editor_id}",
            headers=auth_headers(admin_token),
        )
        assert resp.status_code in (200, 204), resp.text

        resp = client.post(
            "/tokens/relay",
            json={"share_id": share_id, "doc_id": share_id, "mode": "write"},
            headers={"X-Agent-Key": raw_key},
        )
        assert resp.status_code == 403

    def test_agent_key_cannot_use_foreign_share_as_doc_id(self, client: TestClient, db_session):
        """H6 confused-deputy check applies to agent keys: a key is bound to
        exactly one share, so it can never authorize a DIFFERENT share via
        doc_id — always reject, there's no "recheck access" path for a key."""
        admin_token = login(client, "bootstrap@example.com", "super-secret")
        share_a = create_share(client, admin_token, kind="doc", path="vault/a.md")
        share_b = create_share(client, admin_token, kind="doc", path="vault/b.md")
        owner_id = get_share_owner_id(db_session, share_a)
        raw_key = make_agent_key(db_session, share_a, created_by=owner_id, scopes="write")

        resp = client.post(
            "/tokens/relay",
            json={"share_id": share_a, "doc_id": share_b, "mode": "write"},
            headers={"X-Agent-Key": raw_key},
        )
        assert resp.status_code == 403

    def test_agent_key_scoped_folder_share_any_doc_id(self, client: TestClient, db_session):
        """Sanity: folder-share whole-sync + per-file doc_id both still work
        for an agent key, mirroring the user-based folder-share tests."""
        admin_token = login(client, "bootstrap@example.com", "super-secret")
        share_id = create_share(client, admin_token, kind="folder", path="vault/project")
        owner_id = get_share_owner_id(db_session, share_id)
        raw_key = make_agent_key(db_session, share_id, created_by=owner_id, scopes="write")

        resp = client.post(
            "/tokens/relay",
            json={
                "share_id": share_id,
                "doc_id": "some-file-id",
                "mode": "write",
                "file_path": "vault/project/notes.md",
            },
            headers={"X-Agent-Key": raw_key},
        )
        assert resp.status_code == 200, resp.text
