"""Tests for inbound sync endpoints: GET /shares/{id}/files-index and /download.

These endpoints are the plugin-facing (Bearer JWT) counterparts to the agent-key
files-index/download on the /v1/web/ router. They fix Bug 1/Bug 2/Bug 3 from
the s5 E2E regression (task 64bc925e).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.db import models


def login(client: TestClient, email: str, password: str) -> str:
    response = client.post("/auth/login", json={"email": email, "password": password})
    assert response.status_code == 200, response.text
    return response.json()["access_token"]


def auth_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def make_folder_share(
    db: Session,
    user: models.User,
    visibility: models.ShareVisibility = models.ShareVisibility.PRIVATE,
    folder_items: list | None = None,
) -> models.Share:
    share = models.Share(
        kind=models.ShareKind.FOLDER,
        path="InboundFolder/",
        visibility=visibility,
        owner_user_id=user.id,
        web_published=True,
        web_slug=f"ib-{uuid.uuid4().hex[:8]}",
        web_folder_items=folder_items or [],
        web_content_updated_at=datetime(2026, 6, 7, 23, 0, 0, tzinfo=timezone.utc),
    )
    db.add(share)
    db.commit()
    db.refresh(share)
    return share


def sync_artifact_item(
    path: str = "notes/hello.md",
    sha256: str = "abc123",
    size: int = 100,
    content: str = "# Hello",
) -> dict:
    return {
        "path": path,
        "name": path.split("/")[-1],
        "type": "doc",
        "source": "sync-artifact",
        "mime": "text/markdown",
        "size": size,
        "sha256": sha256,
        "modified_at": "2026-06-07T23:00:00+00:00",
        "storage_key": f"sync-uploads/fakeshare/{sha256}",
        "content": content,
    }


def mesh_artifact_item(path: str = "agent/report.md") -> dict:
    return {
        "path": path,
        "name": path.split("/")[-1],
        "type": "doc",
        "source": "mesh-artifact",
        "mime": "text/markdown",
        "size": 50,
        "modified_at": "2026-06-07T22:00:00+00:00",
        "storage_key": "web-assets/fakeshare/report.md",
        "content": "# Agent report",
    }


# ── GET /shares/{id}/files-index ─────────────────────────────────────────────


class TestFilesIndexEndpoint:
    def test_owner_gets_sync_artifact_items(
        self, client: TestClient, test_user: models.User, db_session: Session
    ):
        item = sync_artifact_item()
        share = make_folder_share(db_session, test_user, folder_items=[item])
        token = login(client, "testuser@example.com", "test123456")

        resp = client.get(f"/v1/shares/{share.id}/files-index", headers=auth_headers(token))

        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert isinstance(data, list)
        assert len(data) == 1
        assert data[0]["path"] == "notes/hello.md"
        assert data[0]["sha256"] == "abc123"
        assert data[0]["size"] == 100
        assert data[0]["updated_at"] == "2026-06-07T23:00:00+00:00"
        assert data[0]["type"] == "sync-artifact"

    def test_only_sync_artifacts_returned_not_mesh_artifacts(
        self, client: TestClient, test_user: models.User, db_session: Session
    ):
        items = [sync_artifact_item(), mesh_artifact_item()]
        share = make_folder_share(db_session, test_user, folder_items=items)
        token = login(client, "testuser@example.com", "test123456")

        resp = client.get(f"/v1/shares/{share.id}/files-index", headers=auth_headers(token))

        assert resp.status_code == 200, resp.text
        paths = [i["path"] for i in resp.json()]
        assert "notes/hello.md" in paths
        assert "agent/report.md" not in paths

    def test_items_without_sha256_excluded(
        self, client: TestClient, test_user: models.User, db_session: Session
    ):
        no_hash = {
            "path": "old.md",
            "name": "old.md",
            "type": "doc",
            "source": "sync-artifact",
            "size": 10,
            "modified_at": "2026-06-07T00:00:00+00:00",
        }
        share = make_folder_share(db_session, test_user, folder_items=[no_hash])
        token = login(client, "testuser@example.com", "test123456")

        resp = client.get(f"/v1/shares/{share.id}/files-index", headers=auth_headers(token))

        assert resp.status_code == 200, resp.text
        assert resp.json() == []

    def test_empty_share_returns_empty_list(
        self, client: TestClient, test_user: models.User, db_session: Session
    ):
        share = make_folder_share(db_session, test_user, folder_items=[])
        token = login(client, "testuser@example.com", "test123456")

        resp = client.get(f"/v1/shares/{share.id}/files-index", headers=auth_headers(token))

        assert resp.status_code == 200, resp.text
        assert resp.json() == []

    def test_unauthenticated_returns_401(
        self, client: TestClient, test_user: models.User, db_session: Session
    ):
        share = make_folder_share(db_session, test_user)

        resp = client.get(f"/v1/shares/{share.id}/files-index")

        assert resp.status_code == 401

    def test_non_member_on_private_share_returns_403(
        self, client: TestClient, test_user: models.User, db_session: Session
    ):
        from app.core import security as sec

        share = make_folder_share(db_session, test_user, folder_items=[sync_artifact_item()])

        other = models.User(
            email="other@example.com",
            password_hash=sec.get_password_hash("pass123456"),
            is_active=True,
        )
        db_session.add(other)
        db_session.commit()

        token = login(client, "other@example.com", "pass123456")
        resp = client.get(f"/v1/shares/{share.id}/files-index", headers=auth_headers(token))

        assert resp.status_code == 403

    def test_member_can_read_files_index(
        self, client: TestClient, test_user: models.User, db_session: Session
    ):
        from app.core import security as sec

        share = make_folder_share(db_session, test_user, folder_items=[sync_artifact_item()])

        member_user = models.User(
            email="member@example.com",
            password_hash=sec.get_password_hash("pass123456"),
            is_active=True,
        )
        db_session.add(member_user)
        db_session.commit()
        db_session.refresh(member_user)

        db_session.add(
            models.ShareMember(
                share_id=share.id,
                user_id=member_user.id,
                role=models.ShareMemberRole.VIEWER,
            )
        )
        db_session.commit()

        token = login(client, "member@example.com", "pass123456")
        resp = client.get(f"/v1/shares/{share.id}/files-index", headers=auth_headers(token))

        assert resp.status_code == 200, resp.text
        assert len(resp.json()) == 1

    def test_legacy_path_without_v1_works(
        self, client: TestClient, test_user: models.User, db_session: Session
    ):
        share = make_folder_share(db_session, test_user, folder_items=[sync_artifact_item()])
        token = login(client, "testuser@example.com", "test123456")

        resp = client.get(f"/shares/{share.id}/files-index", headers=auth_headers(token))

        assert resp.status_code == 200, resp.text

    def test_unknown_share_returns_404(
        self, client: TestClient, test_user: models.User, db_session: Session
    ):
        token = login(client, "testuser@example.com", "test123456")

        resp = client.get(f"/v1/shares/{uuid.uuid4()}/files-index", headers=auth_headers(token))

        assert resp.status_code == 404


# ── GET /shares/{id}/download ─────────────────────────────────────────────────


class TestDownloadEndpoint:
    def test_owner_can_download_inline_content(
        self, client: TestClient, test_user: models.User, db_session: Session
    ):
        item = sync_artifact_item(path="notes/hello.md", content="# Hello world")
        share = make_folder_share(db_session, test_user, folder_items=[item])
        token = login(client, "testuser@example.com", "test123456")

        resp = client.get(
            f"/v1/shares/{share.id}/download?path=notes/hello.md",
            headers=auth_headers(token),
        )

        assert resp.status_code == 200, resp.text
        assert b"# Hello world" in resp.content

    def test_file_not_found_returns_404(
        self, client: TestClient, test_user: models.User, db_session: Session
    ):
        share = make_folder_share(db_session, test_user, folder_items=[sync_artifact_item()])
        token = login(client, "testuser@example.com", "test123456")

        resp = client.get(
            f"/v1/shares/{share.id}/download?path=nonexistent.md",
            headers=auth_headers(token),
        )

        assert resp.status_code == 404

    def test_unauthenticated_returns_401(
        self, client: TestClient, test_user: models.User, db_session: Session
    ):
        share = make_folder_share(db_session, test_user, folder_items=[sync_artifact_item()])

        resp = client.get(f"/v1/shares/{share.id}/download?path=notes/hello.md")

        assert resp.status_code == 401

    def test_non_member_on_private_share_returns_403(
        self, client: TestClient, test_user: models.User, db_session: Session
    ):
        from app.core import security as sec

        share = make_folder_share(db_session, test_user, folder_items=[sync_artifact_item()])

        other = models.User(
            email="other2@example.com",
            password_hash=sec.get_password_hash("pass123456"),
            is_active=True,
        )
        db_session.add(other)
        db_session.commit()

        token = login(client, "other2@example.com", "pass123456")
        resp = client.get(
            f"/v1/shares/{share.id}/download?path=notes/hello.md",
            headers=auth_headers(token),
        )

        assert resp.status_code == 403

    def test_legacy_path_without_v1_works(
        self, client: TestClient, test_user: models.User, db_session: Session
    ):
        item = sync_artifact_item(path="doc.md", content="data")
        share = make_folder_share(db_session, test_user, folder_items=[item])
        token = login(client, "testuser@example.com", "test123456")

        resp = client.get(
            f"/shares/{share.id}/download?path=doc.md",
            headers=auth_headers(token),
        )

        assert resp.status_code == 200, resp.text


# ── Bug 2: web_content_updated_at in ShareRead ────────────────────────────────


class TestShareReadIncludesWebContentUpdatedAt:
    def test_share_detail_includes_web_content_updated_at(
        self, client: TestClient, test_user: models.User, db_session: Session
    ):
        share = make_folder_share(db_session, test_user)
        token = login(client, "testuser@example.com", "test123456")

        resp = client.get(f"/v1/shares/{share.id}", headers=auth_headers(token))

        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert "web_content_updated_at" in data
        assert data["web_content_updated_at"] is not None

    def test_share_detail_web_content_updated_at_none_when_not_bumped(
        self, client: TestClient, test_user: models.User, db_session: Session
    ):
        share = models.Share(
            kind=models.ShareKind.FOLDER,
            path="Fresh/",
            visibility=models.ShareVisibility.PRIVATE,
            owner_user_id=test_user.id,
            web_published=False,
            web_folder_items=[],
        )
        db_session.add(share)
        db_session.commit()
        db_session.refresh(share)

        token = login(client, "testuser@example.com", "test123456")
        resp = client.get(f"/v1/shares/{share.id}", headers=auth_headers(token))

        assert resp.status_code == 200, resp.text
        assert resp.json()["web_content_updated_at"] is None


# ── Bug 3: sync-artifact lost after vault PATCH (race condition fix) ──────────


class TestSyncArtifactPreservedDuringVaultPatch:
    """Regression: sync_upload commits artifact, plugin sends _initialFullSync PATCH
    with vault files — db.refresh(share) must reload DB state before merge so the
    artifact is not lost when the SQLAlchemy identity map was stale."""

    def test_artifact_survives_vault_patch(
        self, client: TestClient, test_user: models.User, db_session: Session
    ):
        """Sync-artifact already in web_folder_items must not be dropped when plugin
        patches with a full vault-file list that does not include the artifact path."""
        artifact = sync_artifact_item()
        share = make_folder_share(db_session, test_user, folder_items=[artifact])
        token = login(client, "testuser@example.com", "test123456")

        # Plugin sends _initialFullSync: vault-only list, artifact path absent
        resp = client.patch(
            f"/v1/shares/{share.id}",
            headers=auth_headers(token),
            json={
                "web_folder_items": [
                    {"path": "notes/vault-file.md", "name": "vault-file.md", "type": "doc"}
                ]
            },
        )

        assert resp.status_code == 200, resp.text

        # Verify via files-index (returns sync-artifacts) that the artifact survived
        idx_resp = client.get(f"/v1/shares/{share.id}/files-index", headers=auth_headers(token))
        assert idx_resp.status_code == 200, idx_resp.text
        paths = {item["path"] for item in idx_resp.json()}
        assert artifact["path"] in paths, "sync-artifact must be preserved after vault PATCH"
