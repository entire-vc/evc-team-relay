from __future__ import annotations

from fastapi.testclient import TestClient


def auth_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def login(client: TestClient, email: str, password: str) -> str:
    response = client.post("/auth/login", json={"email": email, "password": password})
    assert response.status_code == 200, response.text
    return response.json()["access_token"]


class TestInviteCreation:
    """Tests for creating invite links."""

    def test_create_invite_as_owner(self, client: TestClient):
        """Test that share owner can create invite links."""
        admin_token = login(client, "bootstrap@example.com", "super-secret")

        # Create a share
        share_resp = client.post(
            "/shares",
            json={"kind": "doc", "path": "vault/test.md", "visibility": "private"},
            headers=auth_headers(admin_token),
        )
        assert share_resp.status_code == 201
        share_id = share_resp.json()["id"]

        # Create invite
        invite_resp = client.post(
            f"/shares/{share_id}/invites",
            json={"role": "viewer", "expires_in_days": 7, "max_uses": 10},
            headers=auth_headers(admin_token),
        )
        assert invite_resp.status_code == 201
        invite = invite_resp.json()
        assert invite["share_id"] == share_id
        assert invite["role"] == "viewer"
        assert invite["max_uses"] == 10
        assert invite["use_count"] == 0
        assert len(invite["token"]) == 64  # 32 bytes hex = 64 chars
        assert invite["revoked_at"] is None

    def test_create_invite_unauthorized(self, client: TestClient):
        """Test that non-owners cannot create invites."""
        admin_token = login(client, "bootstrap@example.com", "super-secret")

        # Create share
        share_resp = client.post(
            "/shares",
            json={"kind": "doc", "path": "vault/test.md", "visibility": "private"},
            headers=auth_headers(admin_token),
        )
        share_id = share_resp.json()["id"]

        # Create another user
        client.post(
            "/admin/users",
            json={
                "email": "user@test.com",
                "password": "password123",
                "is_admin": False,
                "is_active": True,
            },
            headers=auth_headers(admin_token),
        )
        user_token = login(client, "user@test.com", "password123")

        # Try to create invite as non-owner
        invite_resp = client.post(
            f"/shares/{share_id}/invites",
            json={"role": "viewer"},
            headers=auth_headers(user_token),
        )
        assert invite_resp.status_code == 403

    def test_create_invite_without_auth(self, client: TestClient):
        """Test that authentication is required to create invites."""
        invite_resp = client.post(
            "/shares/00000000-0000-0000-0000-000000000000/invites",
            json={"role": "viewer"},
        )
        assert invite_resp.status_code == 401

    def test_create_invite_with_defaults(self, client: TestClient):
        """Test creating invite with default parameters."""
        admin_token = login(client, "bootstrap@example.com", "super-secret")

        share_resp = client.post(
            "/shares",
            json={"kind": "doc", "path": "vault/test.md", "visibility": "private"},
            headers=auth_headers(admin_token),
        )
        share_id = share_resp.json()["id"]

        # Create invite with minimal params
        invite_resp = client.post(
            f"/shares/{share_id}/invites",
            json={},
            headers=auth_headers(admin_token),
        )
        assert invite_resp.status_code == 201
        invite = invite_resp.json()
        assert invite["role"] == "viewer"
        assert invite["max_uses"] is None  # Unlimited uses

    def test_create_invite_explicit_null_expires_in_days_gets_default(self, client: TestClient):
        """A caller sending expires_in_days: null bypasses the schema's own
        default of 7 (only applied when the field is absent) — this must
        still land on the config default, not on no expiry at all."""
        admin_token = login(client, "bootstrap@example.com", "super-secret")

        share_resp = client.post(
            "/shares",
            json={"kind": "doc", "path": "vault/test.md", "visibility": "private"},
            headers=auth_headers(admin_token),
        )
        share_id = share_resp.json()["id"]

        invite_resp = client.post(
            f"/shares/{share_id}/invites",
            json={"expires_in_days": None},
            headers=auth_headers(admin_token),
        )
        assert invite_resp.status_code == 201
        invite = invite_resp.json()
        assert invite["expires_at"] is not None


class TestInviteListing:
    """Tests for listing invite links."""

    def test_list_invites(self, client: TestClient):
        """Test listing all invites for a share."""
        admin_token = login(client, "bootstrap@example.com", "super-secret")

        # Create share
        share_resp = client.post(
            "/shares",
            json={"kind": "doc", "path": "vault/test.md", "visibility": "private"},
            headers=auth_headers(admin_token),
        )
        share_id = share_resp.json()["id"]

        # Create multiple invites
        for i in range(3):
            client.post(
                f"/shares/{share_id}/invites",
                json={"role": "viewer" if i % 2 == 0 else "editor"},
                headers=auth_headers(admin_token),
            )

        # List invites
        list_resp = client.get(
            f"/shares/{share_id}/invites",
            headers=auth_headers(admin_token),
        )
        assert list_resp.status_code == 200
        invites = list_resp.json()
        assert len(invites) == 3

    def test_list_invites_unauthorized(self, client: TestClient):
        """Test that non-owners cannot list invites."""
        admin_token = login(client, "bootstrap@example.com", "super-secret")

        share_resp = client.post(
            "/shares",
            json={"kind": "doc", "path": "vault/test.md", "visibility": "private"},
            headers=auth_headers(admin_token),
        )
        share_id = share_resp.json()["id"]

        # Create another user
        client.post(
            "/admin/users",
            json={
                "email": "user@test.com",
                "password": "password123",
                "is_admin": False,
                "is_active": True,
            },
            headers=auth_headers(admin_token),
        )
        user_token = login(client, "user@test.com", "password123")

        # Try to list invites as non-owner
        list_resp = client.get(
            f"/shares/{share_id}/invites",
            headers=auth_headers(user_token),
        )
        assert list_resp.status_code == 403


class TestInviteRevocation:
    """Tests for revoking invite links."""

    def test_revoke_invite(self, client: TestClient):
        """Test revoking an invite link."""
        admin_token = login(client, "bootstrap@example.com", "super-secret")

        # Create share and invite
        share_resp = client.post(
            "/shares",
            json={"kind": "doc", "path": "vault/test.md", "visibility": "private"},
            headers=auth_headers(admin_token),
        )
        share_id = share_resp.json()["id"]

        invite_resp = client.post(
            f"/shares/{share_id}/invites",
            json={"role": "viewer"},
            headers=auth_headers(admin_token),
        )
        invite_id = invite_resp.json()["id"]

        # Revoke invite
        revoke_resp = client.delete(
            f"/shares/{share_id}/invites/{invite_id}",
            headers=auth_headers(admin_token),
        )
        assert revoke_resp.status_code == 204

        # Verify invite is revoked
        list_resp = client.get(
            f"/shares/{share_id}/invites",
            headers=auth_headers(admin_token),
        )
        invites = list_resp.json()
        assert len(invites) == 1
        assert invites[0]["revoked_at"] is not None

    def test_revoke_nonexistent_invite(self, client: TestClient):
        """Test revoking a non-existent invite."""
        admin_token = login(client, "bootstrap@example.com", "super-secret")

        share_resp = client.post(
            "/shares",
            json={"kind": "doc", "path": "vault/test.md", "visibility": "private"},
            headers=auth_headers(admin_token),
        )
        share_id = share_resp.json()["id"]

        # Try to revoke non-existent invite
        revoke_resp = client.delete(
            f"/shares/{share_id}/invites/00000000-0000-0000-0000-000000000000",
            headers=auth_headers(admin_token),
        )
        assert revoke_resp.status_code == 404


class TestInvitePublicInfo:
    """Tests for getting public invite information (no auth required)."""

    def test_get_valid_invite_info(self, client: TestClient):
        """Test getting public info for a valid invite."""
        admin_token = login(client, "bootstrap@example.com", "super-secret")

        # Create share and invite
        share_resp = client.post(
            "/shares",
            json={"kind": "doc", "path": "vault/test.md", "visibility": "private"},
            headers=auth_headers(admin_token),
        )

        invite_resp = client.post(
            f"/shares/{share_resp.json()['id']}/invites",
            json={"role": "editor", "expires_in_days": 7},
            headers=auth_headers(admin_token),
        )
        token = invite_resp.json()["token"]

        # Get public info (no auth)
        info_resp = client.get(f"/invite/{token}")
        assert info_resp.status_code == 200
        info = info_resp.json()
        assert info["share_path"] == "vault/test.md"
        assert info["share_kind"] == "doc"
        assert info["owner_email"] == "bootstrap@example.com"
        assert info["role"] == "editor"
        assert info["is_valid"] is True
        assert info["error"] is None

    def test_get_invalid_invite_info(self, client: TestClient):
        """Test getting info for non-existent invite."""
        info_resp = client.get("/invite/invalid-token-12345")
        assert info_resp.status_code == 200
        info = info_resp.json()
        assert info["is_valid"] is False
        assert info["error"] == "Invalid invite link"

    def test_get_revoked_invite_info(self, client: TestClient):
        """Test getting info for revoked invite."""
        admin_token = login(client, "bootstrap@example.com", "super-secret")

        # Create share and invite
        share_resp = client.post(
            "/shares",
            json={"kind": "doc", "path": "vault/test.md", "visibility": "private"},
            headers=auth_headers(admin_token),
        )
        share_id = share_resp.json()["id"]

        invite_resp = client.post(
            f"/shares/{share_id}/invites",
            json={"role": "viewer"},
            headers=auth_headers(admin_token),
        )
        invite_id = invite_resp.json()["id"]
        token = invite_resp.json()["token"]

        # Revoke invite
        client.delete(
            f"/shares/{share_id}/invites/{invite_id}",
            headers=auth_headers(admin_token),
        )

        # Get public info
        info_resp = client.get(f"/invite/{token}")
        assert info_resp.status_code == 200
        info = info_resp.json()
        assert info["is_valid"] is False
        assert "revoked" in info["error"].lower()


class TestInviteRedemption:
    """Tests for redeeming invite links."""

    def test_redeem_invite_existing_user(self, client: TestClient):
        """Test existing user redeeming an invite."""
        admin_token = login(client, "bootstrap@example.com", "super-secret")

        # Create share and invite
        share_resp = client.post(
            "/shares",
            json={"kind": "doc", "path": "vault/test.md", "visibility": "private"},
            headers=auth_headers(admin_token),
        )
        share_id = share_resp.json()["id"]

        invite_resp = client.post(
            f"/shares/{share_id}/invites",
            json={"role": "editor", "max_uses": 5},
            headers=auth_headers(admin_token),
        )
        token = invite_resp.json()["token"]

        # Create another user
        client.post(
            "/admin/users",
            json={
                "email": "user@test.com",
                "password": "password123",
                "is_admin": False,
                "is_active": True,
            },
            headers=auth_headers(admin_token),
        )
        user_token = login(client, "user@test.com", "password123")

        # Redeem invite as existing user
        redeem_resp = client.post(
            f"/invite/{token}/redeem",
            headers=auth_headers(user_token),
        )
        assert redeem_resp.status_code == 200
        result = redeem_resp.json()
        assert result["user_email"] == "user@test.com"
        assert result["share_path"] == "vault/test.md"
        assert result["role"] == "editor"
        assert result["access_token"] is None  # Existing user doesn't get new token

        # Verify user is now a member
        members_resp = client.get(
            f"/shares/{share_id}/members",
            headers=auth_headers(admin_token),
        )
        members = members_resp.json()
        assert len(members) == 1
        assert members[0]["user_email"] == "user@test.com"
        assert members[0]["role"] == "editor"

    def test_redeem_invite_new_user(self, client: TestClient):
        """Test new user registration via invite redemption."""
        admin_token = login(client, "bootstrap@example.com", "super-secret")

        # Create share and invite
        share_resp = client.post(
            "/shares",
            json={"kind": "doc", "path": "vault/test.md", "visibility": "private"},
            headers=auth_headers(admin_token),
        )
        share_id = share_resp.json()["id"]

        invite_resp = client.post(
            f"/shares/{share_id}/invites",
            json={"role": "viewer"},
            headers=auth_headers(admin_token),
        )
        token = invite_resp.json()["token"]

        # Redeem invite as new user
        redeem_resp = client.post(
            f"/invite/{token}/redeem",
            json={"email": "newuser@test.com", "password": "newpass123"},
        )
        assert redeem_resp.status_code == 200
        result = redeem_resp.json()
        assert result["user_email"] == "newuser@test.com"
        assert result["share_path"] == "vault/test.md"
        assert result["role"] == "viewer"
        assert result["access_token"] is not None  # New user gets access token

        # Verify new user can login
        login_resp = client.post(
            "/auth/login",
            json={"email": "newuser@test.com", "password": "newpass123"},
        )
        assert login_resp.status_code == 200

        # Verify user is a member
        members_resp = client.get(
            f"/shares/{share_id}/members",
            headers=auth_headers(admin_token),
        )
        members = members_resp.json()
        assert len(members) == 1
        assert members[0]["user_email"] == "newuser@test.com"

    def test_redeem_invite_duplicate_email(self, client: TestClient):
        """Test that redeeming with existing email returns proper error."""
        admin_token = login(client, "bootstrap@example.com", "super-secret")

        # Create share and invite
        share_resp = client.post(
            "/shares",
            json={"kind": "doc", "path": "vault/test.md", "visibility": "private"},
            headers=auth_headers(admin_token),
        )

        invite_resp = client.post(
            f"/shares/{share_resp.json()['id']}/invites",
            json={"role": "viewer"},
            headers=auth_headers(admin_token),
        )
        token = invite_resp.json()["token"]

        # Try to register with admin's email
        redeem_resp = client.post(
            f"/invite/{token}/redeem",
            json={"email": "bootstrap@example.com", "password": "somepass123"},
        )
        assert redeem_resp.status_code == 400
        response_body = redeem_resp.json()
        # Handle multiple error formats
        error_str = str(response_body).lower()
        assert "already" in error_str and ("exist" in error_str or "registered" in error_str)

    def test_redeem_invite_email_mismatch_rejected(self, client: TestClient, db_session):
        """TR-46: an invite targeted at a specific email must reject a different
        email. Before the fix, invite.email was stored but never checked at
        redeem — any bearer of the link could join regardless.
        """
        from sqlalchemy import select

        from app.db import models

        admin_token = login(client, "bootstrap@example.com", "super-secret")

        share_resp = client.post(
            "/shares",
            json={"kind": "doc", "path": "vault/email-targeted.md", "visibility": "private"},
            headers=auth_headers(admin_token),
        )
        share_id = share_resp.json()["id"]

        invite_resp = client.post(
            f"/shares/{share_id}/invites",
            json={"role": "viewer"},
            headers=auth_headers(admin_token),
        )
        token = invite_resp.json()["token"]

        # invite.email isn't settable via the create API yet (this task is
        # redeem-side only, per Daedalus's scoping) -- set it directly, the way
        # an admin backfill or a future create-path extension would.
        invite = db_session.execute(
            select(models.ShareInvite).where(models.ShareInvite.token == token)
        ).scalar_one()
        invite.email = "intended@test.com"
        db_session.commit()

        # A different email is rejected — new-user-registration path.
        wrong_resp = client.post(
            f"/invite/{token}/redeem",
            json={"email": "wrong-person@test.com", "password": "somepass123"},
        )
        assert wrong_resp.status_code == 403, wrong_resp.text
        assert "email" in str(wrong_resp.json()).lower()

        # No user or membership must have been created on the rejected attempt.
        assert (
            db_session.execute(
                select(models.User).where(models.User.email == "wrong-person@test.com")
            ).scalar_one_or_none()
            is None
        )
        members_resp = client.get(
            f"/shares/{share_id}/members",
            headers=auth_headers(admin_token),
        )
        assert members_resp.json() == []

        # The intended email still succeeds.
        ok_resp = client.post(
            f"/invite/{token}/redeem",
            json={"email": "intended@test.com", "password": "somepass123"},
        )
        assert ok_resp.status_code == 200, ok_resp.text
        assert ok_resp.json()["user_email"] == "intended@test.com"

    def test_redeem_invite_email_mismatch_rejected_existing_user(
        self, client: TestClient, db_session
    ):
        """TR-46: the email-targeting check must also apply to an already
        authenticated existing user, not just the new-registration path.
        """
        from sqlalchemy import select

        from app.db import models

        admin_token = login(client, "bootstrap@example.com", "super-secret")

        share_resp = client.post(
            "/shares",
            json={"kind": "doc", "path": "vault/email-existing-user.md", "visibility": "private"},
            headers=auth_headers(admin_token),
        )
        share_id = share_resp.json()["id"]

        invite_resp = client.post(
            f"/shares/{share_id}/invites",
            json={"role": "viewer"},
            headers=auth_headers(admin_token),
        )
        token = invite_resp.json()["token"]

        invite = db_session.execute(
            select(models.ShareInvite).where(models.ShareInvite.token == token)
        ).scalar_one()
        invite.email = "someone-else@test.com"
        db_session.commit()

        client.post(
            "/admin/users",
            json={
                "email": "wrong-existing-user@test.com",
                "password": "password123",
                "is_admin": False,
                "is_active": True,
            },
            headers=auth_headers(admin_token),
        )
        wrong_user_token = login(client, "wrong-existing-user@test.com", "password123")

        redeem_resp = client.post(
            f"/invite/{token}/redeem",
            headers=auth_headers(wrong_user_token),
        )
        assert redeem_resp.status_code == 403, redeem_resp.text

        members_resp = client.get(
            f"/shares/{share_id}/members",
            headers=auth_headers(admin_token),
        )
        assert members_resp.json() == []

    def test_redeem_invite_email_match_is_case_insensitive(self, client: TestClient, db_session):
        """TR-46: the email targeting check must not be case-sensitive."""
        from sqlalchemy import select

        from app.db import models

        admin_token = login(client, "bootstrap@example.com", "super-secret")

        share_resp = client.post(
            "/shares",
            json={"kind": "doc", "path": "vault/email-case.md", "visibility": "private"},
            headers=auth_headers(admin_token),
        )
        invite_resp = client.post(
            f"/shares/{share_resp.json()['id']}/invites",
            json={"role": "viewer"},
            headers=auth_headers(admin_token),
        )
        token = invite_resp.json()["token"]

        invite = db_session.execute(
            select(models.ShareInvite).where(models.ShareInvite.token == token)
        ).scalar_one()
        invite.email = "Mixed.Case@Test.com"
        db_session.commit()

        redeem_resp = client.post(
            f"/invite/{token}/redeem",
            json={"email": "mixed.case@test.com", "password": "somepass123"},
        )
        assert redeem_resp.status_code == 200, redeem_resp.text

    def test_redeem_invite_no_email_set_unaffected(self, client: TestClient, db_session):
        """TR-46 regression guard: an invite with no email set (the status quo
        for every invite in prod today) must keep working for any redeemer,
        exactly as before this fix.
        """
        from sqlalchemy import select

        from app.db import models

        admin_token = login(client, "bootstrap@example.com", "super-secret")

        share_resp = client.post(
            "/shares",
            json={"kind": "doc", "path": "vault/no-email.md", "visibility": "private"},
            headers=auth_headers(admin_token),
        )
        invite_resp = client.post(
            f"/shares/{share_resp.json()['id']}/invites",
            json={"role": "viewer"},
            headers=auth_headers(admin_token),
        )
        token = invite_resp.json()["token"]

        invite = db_session.execute(
            select(models.ShareInvite).where(models.ShareInvite.token == token)
        ).scalar_one()
        assert invite.email is None

        redeem_resp = client.post(
            f"/invite/{token}/redeem",
            json={"email": "anyone@test.com", "password": "somepass123"},
        )
        assert redeem_resp.status_code == 200, redeem_resp.text

    def test_redeem_invite_owner_cannot_join(self, client: TestClient):
        """Test that share owner cannot redeem their own invite."""
        admin_token = login(client, "bootstrap@example.com", "super-secret")

        # Create share and invite
        share_resp = client.post(
            "/shares",
            json={"kind": "doc", "path": "vault/test.md", "visibility": "private"},
            headers=auth_headers(admin_token),
        )

        invite_resp = client.post(
            f"/shares/{share_resp.json()['id']}/invites",
            json={"role": "viewer"},
            headers=auth_headers(admin_token),
        )
        token = invite_resp.json()["token"]

        # Try to redeem own invite
        redeem_resp = client.post(
            f"/invite/{token}/redeem",
            headers=auth_headers(admin_token),
        )
        assert redeem_resp.status_code == 400
        response_body = redeem_resp.json()
        error_msg = (
            response_body.get("detail", "")
            if isinstance(response_body.get("detail"), str)
            else str(response_body)
        )
        assert "owner" in error_msg.lower()

    def test_redeem_invalid_token(self, client: TestClient):
        """Test redeeming non-existent invite token."""
        admin_token = login(client, "bootstrap@example.com", "super-secret")

        redeem_resp = client.post(
            "/invite/invalid-token-123/redeem",
            headers=auth_headers(admin_token),
        )
        assert redeem_resp.status_code == 404

    def test_redeem_revoked_invite(self, client: TestClient):
        """Test redeeming a revoked invite."""
        admin_token = login(client, "bootstrap@example.com", "super-secret")

        # Create share and invite
        share_resp = client.post(
            "/shares",
            json={"kind": "doc", "path": "vault/test.md", "visibility": "private"},
            headers=auth_headers(admin_token),
        )
        share_id = share_resp.json()["id"]

        invite_resp = client.post(
            f"/shares/{share_id}/invites",
            json={"role": "viewer"},
            headers=auth_headers(admin_token),
        )
        invite_id = invite_resp.json()["id"]
        token = invite_resp.json()["token"]

        # Revoke invite
        client.delete(
            f"/shares/{share_id}/invites/{invite_id}",
            headers=auth_headers(admin_token),
        )

        # Try to redeem
        redeem_resp = client.post(
            f"/invite/{token}/redeem",
            json={"email": "newuser@test.com", "password": "password123"},
        )
        assert redeem_resp.status_code == 410
        response_body = redeem_resp.json()
        error_msg = (
            response_body.get("detail", "")
            if isinstance(response_body.get("detail"), str)
            else str(response_body)
        )
        assert "revoked" in error_msg.lower()

    def test_redeem_invite_idempotent(self, client: TestClient):
        """Test that redeeming invite twice is idempotent for existing members."""
        admin_token = login(client, "bootstrap@example.com", "super-secret")

        # Create share and invite
        share_resp = client.post(
            "/shares",
            json={"kind": "doc", "path": "vault/test.md", "visibility": "private"},
            headers=auth_headers(admin_token),
        )
        share_id = share_resp.json()["id"]

        invite_resp = client.post(
            f"/shares/{share_id}/invites",
            json={"role": "viewer"},
            headers=auth_headers(admin_token),
        )
        token = invite_resp.json()["token"]

        # Create user
        client.post(
            "/admin/users",
            json={
                "email": "user@test.com",
                "password": "password123",
                "is_admin": False,
                "is_active": True,
            },
            headers=auth_headers(admin_token),
        )
        user_token = login(client, "user@test.com", "password123")

        # Redeem twice
        redeem_resp1 = client.post(
            f"/invite/{token}/redeem",
            headers=auth_headers(user_token),
        )
        assert redeem_resp1.status_code == 200

        redeem_resp2 = client.post(
            f"/invite/{token}/redeem",
            headers=auth_headers(user_token),
        )
        assert redeem_resp2.status_code == 200

        # Should still be only one member
        members_resp = client.get(
            f"/shares/{share_id}/members",
            headers=auth_headers(admin_token),
        )
        members = members_resp.json()
        assert len(members) == 1

    def test_redeem_invite_max_uses_enforced(self, client: TestClient):
        """Test that max_uses limit is enforced."""
        admin_token = login(client, "bootstrap@example.com", "super-secret")

        # Create share and invite with max_uses=1
        share_resp = client.post(
            "/shares",
            json={"kind": "doc", "path": "vault/test.md", "visibility": "private"},
            headers=auth_headers(admin_token),
        )

        invite_resp = client.post(
            f"/shares/{share_resp.json()['id']}/invites",
            json={"role": "viewer", "max_uses": 1},
            headers=auth_headers(admin_token),
        )
        token = invite_resp.json()["token"]

        # First redemption should succeed
        redeem_resp1 = client.post(
            f"/invite/{token}/redeem",
            json={"email": "user1@test.com", "password": "password123"},
        )
        assert redeem_resp1.status_code == 200

        # Second redemption should fail
        redeem_resp2 = client.post(
            f"/invite/{token}/redeem",
            json={"email": "user2@test.com", "password": "password123"},
        )
        assert redeem_resp2.status_code == 410
        response_body = redeem_resp2.json()
        error_msg = (
            response_body.get("detail", "")
            if isinstance(response_body.get("detail"), str)
            else str(response_body)
        )
        assert "usage limit" in error_msg.lower()


class TestInviteAuditLogs:
    """Tests for audit logging of invite operations."""

    def test_invite_created_audit_log(self, client: TestClient):
        """Test that invite creation is logged."""
        admin_token = login(client, "bootstrap@example.com", "super-secret")

        # Create share and invite
        share_resp = client.post(
            "/shares",
            json={"kind": "doc", "path": "vault/test.md", "visibility": "private"},
            headers=auth_headers(admin_token),
        )

        invite_resp = client.post(
            f"/shares/{share_resp.json()['id']}/invites",
            json={"role": "editor", "expires_in_days": 7, "max_uses": 10},
            headers=auth_headers(admin_token),
        )
        assert invite_resp.status_code == 201

        # Check audit logs (endpoint returns a list directly)
        logs_resp = client.get("/admin/audit-logs", headers=auth_headers(admin_token))
        logs = logs_resp.json()

        invite_created_logs = [log for log in logs if log["action"] == "invite_created"]
        assert len(invite_created_logs) >= 1

        log = invite_created_logs[0]
        assert log["details"]["role"] == "editor"
        assert log["details"]["max_uses"] == 10

    def test_invite_redeemed_audit_log(self, client: TestClient):
        """Test that invite redemption is logged."""
        admin_token = login(client, "bootstrap@example.com", "super-secret")

        # Create share and invite
        share_resp = client.post(
            "/shares",
            json={"kind": "doc", "path": "vault/test.md", "visibility": "private"},
            headers=auth_headers(admin_token),
        )

        invite_resp = client.post(
            f"/shares/{share_resp.json()['id']}/invites",
            json={"role": "viewer"},
            headers=auth_headers(admin_token),
        )
        token = invite_resp.json()["token"]

        # Redeem invite
        client.post(
            f"/invite/{token}/redeem",
            json={"email": "newuser@test.com", "password": "password123"},
        )

        # Check audit logs (endpoint returns a list directly)
        logs_resp = client.get("/admin/audit-logs", headers=auth_headers(admin_token))
        logs = logs_resp.json()

        redeemed_logs = [log for log in logs if log["action"] == "invite_redeemed"]
        assert len(redeemed_logs) >= 1

        log = redeemed_logs[0]
        assert log["details"]["role"] == "viewer"
        assert log["details"]["is_new_user"] is True


class TestInviteSSRPage:
    """Regression tests for the SSR /invite/{token}/page + /accept flow.

    #69689ba1: both endpoints called `templates.TemplateResponse(name, context)`
    (the pre-Starlette-2.x positional form). Once the installed starlette version
    stopped accepting it, `name` bound to the context dict and Jinja2 tried to use
    it as an unhashable template-cache key -> 500 for every token, including
    nonexistent ones (the handler never got as far as validating the token).
    """

    def test_invite_page_valid_token_renders(self, client: TestClient):
        """A real invite token must render the page, not 500."""
        admin_token = login(client, "bootstrap@example.com", "super-secret")

        share_resp = client.post(
            "/shares",
            json={"kind": "doc", "path": "vault/test.md", "visibility": "private"},
            headers=auth_headers(admin_token),
        )
        invite_resp = client.post(
            f"/shares/{share_resp.json()['id']}/invites",
            json={"role": "viewer"},
            headers=auth_headers(admin_token),
        )
        token = invite_resp.json()["token"]

        page_resp = client.get(f"/invite/{token}/page")
        assert page_resp.status_code == 200
        assert "text/html" in page_resp.headers["content-type"]
        # Behavioral assert on content, not just status (§1n) — the accept
        # form must actually be in the rendered markup, not an error page.
        assert f'action="/invite/{token}/accept"' in page_resp.text or "accept" in page_resp.text.lower()

    def test_invite_page_nonexistent_token_is_not_500(self, client: TestClient):
        """A made-up token must not 500 — the handler must reach token validation."""
        page_resp = client.get(
            "/invite/deadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeef/page"
        )
        assert page_resp.status_code < 500, page_resp.text

    def test_invite_accept_register_renders_success(self, client: TestClient):
        """POST /accept (register action) must render the success page, not 500."""
        admin_token = login(client, "bootstrap@example.com", "super-secret")

        share_resp = client.post(
            "/shares",
            json={"kind": "doc", "path": "vault/test.md", "visibility": "private"},
            headers=auth_headers(admin_token),
        )
        invite_resp = client.post(
            f"/shares/{share_resp.json()['id']}/invites",
            json={"role": "viewer"},
            headers=auth_headers(admin_token),
        )
        token = invite_resp.json()["token"]

        accept_resp = client.post(
            f"/invite/{token}/accept",
            data={
                "action": "register",
                "email": "ssr-newuser@test.com",
                "password": "password123",
                "confirm_password": "password123",
            },
        )
        assert accept_resp.status_code == 200, accept_resp.text
        assert "text/html" in accept_resp.headers["content-type"]
