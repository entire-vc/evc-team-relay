"""Integration tests for POST /tokens/relay endpoint.

Level 2: Uses TestClient with in-memory SQLite — verifies the full token
issuance flow including permissions, CWT structure, and error cases.
"""

from __future__ import annotations

import base64

import cbor2
from fastapi.testclient import TestClient

from app.core.security import CWT_CLAIM_IAT, CWT_CLAIM_ISS, CWT_CLAIM_SCOPE, CWT_TAG


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

        assert claims["iss"] == "relay-control-plane"
        assert claims["scope"] == f"doc:{share_id}:rw"
        assert "iat" in claims
        # y-sweet requirements: no exp, no aud
        assert "exp" not in claims
        assert "aud" not in claims

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
