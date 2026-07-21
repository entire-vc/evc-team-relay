"""Tests for TR-03 (#ae52ba05): an agent key must stop authenticating once its
creator loses standing authority over the share (removed as member, deleted,
or demoted from admin) — not just when explicitly revoked or expired.

Covers both auth paths that validate X-Agent-Key:
  - _auth_agent_key (web.py) — used by /shares/{id}/sync-upload, /upload,
    /files-index, /download.
  - _require_private_web_auth's agent-key branch (web.py) — used by the
    web-publish endpoints (/shares/{slug}/files, /content).

And the two service-layer cascade-revoke triggers:
  - share_service.remove_member
  - user_service.delete_user
"""

from __future__ import annotations

import hashlib
import secrets
import uuid
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core import security
from app.db import models


def auth_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def login(client: TestClient, email: str, password: str) -> str:
    response = client.post("/auth/login", json={"email": email, "password": password})
    assert response.status_code == 200, response.text
    return response.json()["access_token"]


def make_user(
    db: Session, email: str, is_admin: bool = False, is_active: bool = True
) -> models.User:
    user = models.User(
        email=email,
        password_hash=security.get_password_hash("test123456"),
        is_admin=is_admin,
        is_active=is_active,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def make_folder_share(
    db: Session,
    owner: models.User,
    slug: str,
    published: bool = True,
) -> models.Share:
    share = models.Share(
        kind=models.ShareKind.FOLDER,
        path=f"{slug}/",
        visibility=models.ShareVisibility.PRIVATE,
        owner_user_id=owner.id,
        web_published=published,
        web_slug=slug if published else None,
        web_folder_items=[],
    )
    db.add(share)
    db.commit()
    db.refresh(share)
    return share


def add_member(db: Session, share: models.Share, user: models.User) -> models.ShareMember:
    member = models.ShareMember(
        share_id=share.id, user_id=user.id, role=models.ShareMemberRole.EDITOR
    )
    db.add(member)
    db.commit()
    db.refresh(member)
    return member


def make_agent_key(
    db: Session,
    share: models.Share,
    created_by: uuid.UUID | None,
    scopes: str = "write",
) -> tuple[str, models.ShareAgentKey]:
    raw_key = "tr_agent_" + secrets.token_hex(24)
    key_hash = hashlib.sha256(raw_key.encode()).hexdigest()
    ak = models.ShareAgentKey(
        share_id=share.id,
        key_hash=key_hash,
        label="tr03-test-key",
        scopes=scopes,
        created_by=created_by,
    )
    db.add(ak)
    db.commit()
    db.refresh(ak)
    return raw_key, ak


@pytest.fixture
def web_enabled(monkeypatch):
    monkeypatch.setenv("WEB_PUBLISH_DOMAIN", "docs.test.com")
    from app.core.config import get_settings

    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def minio_patch():
    mock_client = MagicMock()
    mock_client.bucket_exists.return_value = True
    mock_client.put_object.return_value = None
    return patch("app.api.routers.web._get_minio_client", return_value=mock_client)


SYNC_UPLOAD = "/v1/web/shares/{id}/sync-upload"


class TestAuthAgentKeyCreatorStanding:
    """_auth_agent_key path (sync-upload) — the endpoint named in the audit repro."""

    def test_owner_created_key_still_works(self, db_session, client, web_enabled):
        owner = make_user(db_session, "owner1@x.com")
        share = make_folder_share(db_session, owner, "s1")
        raw_key, _ = make_agent_key(db_session, share, created_by=owner.id)

        with minio_patch():
            resp = client.post(
                SYNC_UPLOAD.format(id=share.id) + "?path=note.md",
                content=b"hi",
                headers={"X-Agent-Key": raw_key, "Content-Type": "text/plain"},
            )
        assert resp.status_code == 200, resp.text

    def test_active_member_created_key_still_works(self, db_session, client, web_enabled):
        owner = make_user(db_session, "owner2@x.com")
        member = make_user(db_session, "member2@x.com")
        share = make_folder_share(db_session, owner, "s2")
        add_member(db_session, share, member)
        raw_key, _ = make_agent_key(db_session, share, created_by=member.id)

        with minio_patch():
            resp = client.post(
                SYNC_UPLOAD.format(id=share.id) + "?path=note.md",
                content=b"hi",
                headers={"X-Agent-Key": raw_key, "Content-Type": "text/plain"},
            )
        assert resp.status_code == 200, resp.text

    def test_REGRESSION_removed_member_key_is_rejected(self, db_session, client, web_enabled):
        """The audit's literal repro: create key -> remove_member creator -> upload -> 403."""
        owner = make_user(db_session, "owner3@x.com")
        member = make_user(db_session, "member3@x.com")
        share = make_folder_share(db_session, owner, "s3")
        add_member(db_session, share, member)
        raw_key, agent_key = make_agent_key(db_session, share, created_by=member.id)

        # Sanity: works while still a member.
        with minio_patch():
            resp = client.post(
                SYNC_UPLOAD.format(id=share.id) + "?path=before.md",
                content=b"hi",
                headers={"X-Agent-Key": raw_key, "Content-Type": "text/plain"},
            )
        assert resp.status_code == 200, resp.text

        from app.services import share_service

        share_service.remove_member(db_session, share, member.id)

        with minio_patch():
            resp = client.post(
                SYNC_UPLOAD.format(id=share.id) + "?path=after.md",
                content=b"hi",
                headers={"X-Agent-Key": raw_key, "Content-Type": "text/plain"},
            )
        assert resp.status_code == 403, resp.text

        # remove_member must have revoked it explicitly too (defense in depth,
        # not just "auth check happens to reject it").
        db_session.refresh(agent_key)
        assert agent_key.revoked_at is not None

    def test_REGRESSION_deleted_user_key_is_rejected(self, db_session, client, web_enabled):
        owner = make_user(db_session, "owner4@x.com")
        member = make_user(db_session, "member4@x.com")
        share = make_folder_share(db_session, owner, "s4")
        add_member(db_session, share, member)
        raw_key, agent_key = make_agent_key(db_session, share, created_by=member.id)

        from app.services import user_service

        user_service.delete_user(db_session, member.id)

        with minio_patch():
            resp = client.post(
                SYNC_UPLOAD.format(id=share.id) + "?path=after.md",
                content=b"hi",
                headers={"X-Agent-Key": raw_key, "Content-Type": "text/plain"},
            )
        assert resp.status_code == 403, resp.text
        db_session.refresh(agent_key)
        assert agent_key.revoked_at is not None

    def test_key_with_null_created_by_is_rejected(self, db_session, client, web_enabled):
        """Simulates the pre-existing prod state: created_by already NULL
        (SET NULL fired before this fix existed), revoked_at still NULL."""
        owner = make_user(db_session, "owner5@x.com")
        share = make_folder_share(db_session, owner, "s5")
        raw_key, _ = make_agent_key(db_session, share, created_by=None)

        with minio_patch():
            resp = client.post(
                SYNC_UPLOAD.format(id=share.id) + "?path=note.md",
                content=b"hi",
                headers={"X-Agent-Key": raw_key, "Content-Type": "text/plain"},
            )
        assert resp.status_code == 403, resp.text

    def test_REGRESSION_active_admin_key_for_nonmember_share_still_works(
        self, db_session, client, web_enabled
    ):
        """The subtle case: key creation is owner-OR-ADMIN gated
        (_require_share_owner_or_admin), so an admin routinely creates keys
        for shares they don't own or belong to — that must NOT be treated as
        orphaned. This is the scenario a naive owner-or-member-only check
        would have broken for a large share of real fleet-issued keys."""
        owner = make_user(db_session, "owner6@x.com")
        admin = make_user(db_session, "admin6@x.com", is_admin=True)
        share = make_folder_share(db_session, owner, "s6")
        # admin is deliberately NOT a member and NOT the owner.
        raw_key, _ = make_agent_key(db_session, share, created_by=admin.id)

        with minio_patch():
            resp = client.post(
                SYNC_UPLOAD.format(id=share.id) + "?path=note.md",
                content=b"hi",
                headers={"X-Agent-Key": raw_key, "Content-Type": "text/plain"},
            )
        assert resp.status_code == 200, resp.text

    def test_demoted_admin_key_for_nonmember_share_is_rejected(
        self, db_session, client, web_enabled
    ):
        owner = make_user(db_session, "owner7@x.com")
        admin = make_user(db_session, "admin7@x.com", is_admin=True)
        share = make_folder_share(db_session, owner, "s7")
        raw_key, agent_key = make_agent_key(db_session, share, created_by=admin.id)

        admin.is_admin = False
        db_session.add(admin)
        db_session.commit()

        with minio_patch():
            resp = client.post(
                SYNC_UPLOAD.format(id=share.id) + "?path=note.md",
                content=b"hi",
                headers={"X-Agent-Key": raw_key, "Content-Type": "text/plain"},
            )
        assert resp.status_code == 403, resp.text


class TestPrivateWebAuthAgentKeyCreatorStanding:
    """_require_private_web_auth's agent-key branch — a SEPARATE implementation
    from _auth_agent_key (web.py's GET /shares/{slug}/files uses this one).
    Both had the same gap; both must be fixed."""

    def test_REGRESSION_removed_member_key_rejected_on_files_get(
        self, db_session, client, web_enabled
    ):
        owner = make_user(db_session, "owner8@x.com")
        member = make_user(db_session, "member8@x.com")
        share = make_folder_share(db_session, owner, "s8")
        share.web_folder_items = [
            {"path": "note.md", "name": "note.md", "type": "doc", "content": "hi"}
        ]
        db_session.add(share)
        db_session.commit()
        add_member(db_session, share, member)
        raw_key, _ = make_agent_key(db_session, share, created_by=member.id, scopes="read")

        from app.services import share_service

        share_service.remove_member(db_session, share, member.id)

        resp = client.get(
            "/v1/web/shares/s8/files?path=note.md",
            headers={"X-Agent-Key": raw_key},
        )
        assert resp.status_code in (401, 403), resp.text


class TestRemoveMemberCascadeRevoke:
    def test_only_revokes_keys_on_the_share_the_member_was_removed_from(
        self, db_session, web_enabled
    ):
        """Scope check: a member removed from share A must not have their
        (still-valid) key on share B revoked too."""
        owner_a = make_user(db_session, "ownerA@x.com")
        owner_b = make_user(db_session, "ownerB@x.com")
        shared_member = make_user(db_session, "sharedmember@x.com")
        share_a = make_folder_share(db_session, owner_a, "sa", published=False)
        share_b = make_folder_share(db_session, owner_b, "sb", published=False)
        add_member(db_session, share_a, shared_member)
        add_member(db_session, share_b, shared_member)
        _, key_a = make_agent_key(db_session, share_a, created_by=shared_member.id)
        _, key_b = make_agent_key(db_session, share_b, created_by=shared_member.id)

        from app.services import share_service

        share_service.remove_member(db_session, share_a, shared_member.id)

        db_session.refresh(key_a)
        db_session.refresh(key_b)
        assert key_a.revoked_at is not None
        assert key_b.revoked_at is None


class TestDeleteUserCascadeRevoke:
    def test_revokes_keys_across_all_shares_the_user_created_keys_for(
        self, db_session, web_enabled
    ):
        owner_a = make_user(db_session, "ownerA2@x.com")
        owner_b = make_user(db_session, "ownerB2@x.com")
        member = make_user(db_session, "member2b@x.com")
        share_a = make_folder_share(db_session, owner_a, "sa2", published=False)
        share_b = make_folder_share(db_session, owner_b, "sb2", published=False)
        add_member(db_session, share_a, member)
        add_member(db_session, share_b, member)
        _, key_a = make_agent_key(db_session, share_a, created_by=member.id)
        _, key_b = make_agent_key(db_session, share_b, created_by=member.id)

        from app.services import user_service

        user_service.delete_user(db_session, member.id)

        db_session.refresh(key_a)
        db_session.refresh(key_b)
        assert key_a.revoked_at is not None
        assert key_b.revoked_at is not None

    def test_does_not_revoke_an_already_revoked_key_timestamp(self, db_session, web_enabled):
        """revoked_at should not be clobbered/reset for a key that was
        already revoked for a different reason before the user was deleted."""
        owner = make_user(db_session, "owner9@x.com")
        member = make_user(db_session, "member9@x.com")
        share = make_folder_share(db_session, owner, "s9", published=False)
        add_member(db_session, share, member)
        _, key = make_agent_key(db_session, share, created_by=member.id)
        original_revoked_at = security.utcnow()
        key.revoked_at = original_revoked_at
        db_session.add(key)
        db_session.commit()

        from app.services import user_service

        user_service.delete_user(db_session, member.id)

        db_session.refresh(key)
        # SQLite stores naive datetimes; compare on value, not tz-awareness.
        assert key.revoked_at.replace(tzinfo=None) == original_revoked_at.replace(tzinfo=None)


class TestDeleteShareCascade:
    def test_deleting_a_share_removes_its_agent_keys(self, db_session, web_enabled):
        """Regression-proof for the AC's third cascade trigger: delete_share
        already hard-deletes agent keys via the Share.agent_keys ORM cascade
        (cascade='all, delete-orphan') — no revoked_at needed, the rows are
        gone. This locks that existing behavior in."""
        owner = make_user(db_session, "owner10@x.com")
        share = make_folder_share(db_session, owner, "s10", published=False)
        _, key = make_agent_key(db_session, share, created_by=owner.id)
        key_id = key.id

        from app.services import share_service

        share_service.delete_share(db_session, share)

        assert db_session.get(models.ShareAgentKey, key_id) is None
