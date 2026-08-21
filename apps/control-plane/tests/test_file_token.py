"""Tests for the attachment (CAS) file-token contract (TR-09).

POST /shares/{id}/file-token mints a short-lived, path-scoped token; the
three consuming routes (HEAD .../files/{path}, GET .../download-url, POST
.../upload-url) each independently re-check read/write access rather than
trusting a mode encoded in the token, since CAS.ts requests one token per
operation without stating read vs write intent.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from minio.error import S3Error
from sqlalchemy.orm import Session

from app.core import security as sec
from app.db import models

# ── helpers ──────────────────────────────────────────────────────────────────


def login(client: TestClient, email: str, password: str) -> str:
    r = client.post("/auth/login", json={"email": email, "password": password})
    assert r.status_code == 200, r.text
    return r.json()["access_token"]


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def make_folder_share(
    db: Session,
    user: models.User,
    slug: str = "file-token-test-share",
) -> models.Share:
    share = models.Share(
        kind=models.ShareKind.FOLDER,
        path="FileTokenTest/",
        visibility=models.ShareVisibility.PRIVATE,
        owner_user_id=user.id,
        web_published=True,
        web_slug=slug,
        web_folder_items=[],
    )
    db.add(share)
    db.commit()
    db.refresh(share)
    return share


def add_member(
    db: Session, share: models.Share, user: models.User, role: models.ShareMemberRole
) -> None:
    member = models.ShareMember(share_id=share.id, user_id=user.id, role=role)
    db.add(member)
    db.commit()


def s3_not_found() -> S3Error:
    return S3Error(
        response=None,
        code="NoSuchKey",
        message="not found",
        resource="/bucket/obj",
        request_id="req1",
        host_id="host1",
    )


def minio_object(content: bytes = b"fake-png-bytes", content_type: str = "image/png") -> MagicMock:
    """A MinIO get_object() response: .read() + .headers.get(...) + close/release_conn."""
    resp = MagicMock()
    resp.read.return_value = content
    resp.headers = {"Content-Type": content_type}
    return resp


def minio_patch(exists: bool = True):
    """Mocks shares.py's MinIO client — MinIO is a true external boundary."""
    mock_client = MagicMock()
    mock_client.bucket_exists.return_value = True
    if exists:
        mock_client.stat_object.return_value = MagicMock()
        mock_client.presigned_get_object.return_value = "https://minio.test/presigned-get"
        mock_client.presigned_put_object.return_value = "https://minio.test/presigned-put"
        mock_client.get_object.return_value = minio_object()
    else:
        mock_client.stat_object.side_effect = s3_not_found()
        mock_client.get_object.side_effect = s3_not_found()
    return patch("app.api.routers.shares._get_minio_client", return_value=mock_client)


# ── fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture
def owner(test_user: models.User) -> models.User:
    return test_user


@pytest.fixture
def viewer(db_session: Session) -> models.User:
    user = models.User(
        email="viewer@example.com",
        password_hash=sec.get_password_hash("test123456"),
        is_admin=False,
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    return user


@pytest.fixture
def editor(db_session: Session) -> models.User:
    user = models.User(
        email="editor@example.com",
        password_hash=sec.get_password_hash("test123456"),
        is_admin=False,
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    return user


@pytest.fixture
def stranger(db_session: Session) -> models.User:
    user = models.User(
        email="stranger@example.com",
        password_hash=sec.get_password_hash("test123456"),
        is_admin=False,
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    return user


FILE_TOKEN_BODY = {
    "path": "attachments/photo.png",
    "sha256": "a" * 64,
    "content_type": "image/png",
    "content_length": 12345,
}


def mint(client: TestClient, share_id, token: str, body: dict = FILE_TOKEN_BODY) -> dict:
    r = client.post(f"/shares/{share_id}/file-token", json=body, headers=bearer(token))
    assert r.status_code == 200, r.text
    return r.json()


# ── POST /shares/{id}/file-token ──────────────────────────────────────────────


class TestCreateFileToken:
    def test_no_auth_returns_401(self, client: TestClient, db_session: Session, owner):
        share = make_folder_share(db_session, owner, slug="ft-no-auth")
        r = client.post(f"/shares/{share.id}/file-token", json=FILE_TOKEN_BODY)
        assert r.status_code == 401

    def test_owner_can_mint(self, client: TestClient, db_session: Session, owner):
        share = make_folder_share(db_session, owner, slug="ft-owner")
        token = login(client, owner.email, "test123456")
        data = mint(client, share.id, token)
        assert data["token"]
        assert data["base_url"].endswith(f"/shares/{share.id}/files/attachments/photo.png")
        assert data["expires_at"]

    def test_viewer_can_mint_read_is_the_floor(
        self, client: TestClient, db_session: Session, owner, viewer
    ):
        share = make_folder_share(db_session, owner, slug="ft-viewer")
        add_member(db_session, share, viewer, models.ShareMemberRole.VIEWER)
        token = login(client, viewer.email, "test123456")
        data = mint(client, share.id, token)
        assert data["token"]

    def test_stranger_returns_403(self, client: TestClient, db_session: Session, owner, stranger):
        share = make_folder_share(db_session, owner, slug="ft-stranger")
        token = login(client, stranger.email, "test123456")
        r = client.post(
            f"/shares/{share.id}/file-token", json=FILE_TOKEN_BODY, headers=bearer(token)
        )
        assert r.status_code == 403

    def test_path_traversal_rejected(self, client: TestClient, db_session: Session, owner):
        share = make_folder_share(db_session, owner, slug="ft-traversal")
        token = login(client, owner.email, "test123456")
        body = {**FILE_TOKEN_BODY, "path": "../../etc/passwd"}
        r = client.post(f"/shares/{share.id}/file-token", json=body, headers=bearer(token))
        assert r.status_code == 400

    def test_minted_token_cannot_be_used_as_a_session_token(
        self, client: TestClient, db_session: Session, owner
    ):
        """Security guard: a file-token must not double as a general session
        credential on an unrelated get_current_user-gated endpoint."""
        share = make_folder_share(db_session, owner, slug="ft-not-session")
        token = login(client, owner.email, "test123456")
        data = mint(client, share.id, token)
        r = client.get(f"/shares/{share.id}", headers=bearer(data["token"]))
        assert r.status_code == 401


# ── HEAD /shares/{id}/files/{path} (CAS.ts verify) ────────────────────────────


class TestHeadFile:
    def test_exists_returns_200(self, client: TestClient, db_session: Session, owner):
        share = make_folder_share(db_session, owner, slug="head-exists")
        token = login(client, owner.email, "test123456")
        data = mint(client, share.id, token)
        with minio_patch(exists=True):
            r = client.head(
                f"/shares/{share.id}/files/attachments/photo.png", headers=bearer(data["token"])
            )
        assert r.status_code == 200

    def test_missing_returns_404(self, client: TestClient, db_session: Session, owner):
        share = make_folder_share(db_session, owner, slug="head-missing")
        token = login(client, owner.email, "test123456")
        data = mint(client, share.id, token)
        with minio_patch(exists=False):
            r = client.head(
                f"/shares/{share.id}/files/attachments/photo.png", headers=bearer(data["token"])
            )
        assert r.status_code == 404

    def test_wrong_share_id_returns_403(self, client: TestClient, db_session: Session, owner):
        share_a = make_folder_share(db_session, owner, slug="head-share-a")
        share_b = make_folder_share(db_session, owner, slug="head-share-b")
        token = login(client, owner.email, "test123456")
        data = mint(client, share_a.id, token)
        r = client.head(
            f"/shares/{share_b.id}/files/attachments/photo.png", headers=bearer(data["token"])
        )
        assert r.status_code == 403

    def test_wrong_path_returns_403(self, client: TestClient, db_session: Session, owner):
        share = make_folder_share(db_session, owner, slug="head-wrong-path")
        token = login(client, owner.email, "test123456")
        data = mint(client, share.id, token)
        r = client.head(
            f"/shares/{share.id}/files/some/other-path.png", headers=bearer(data["token"])
        )
        assert r.status_code == 403

    def test_session_jwt_rejected_not_a_file_token(
        self, client: TestClient, db_session: Session, owner
    ):
        share = make_folder_share(db_session, owner, slug="head-session-jwt")
        token = login(client, owner.email, "test123456")
        r = client.head(f"/shares/{share.id}/files/attachments/photo.png", headers=bearer(token))
        assert r.status_code == 401


# ── GET /shares/{id}/files/{path}/download-url (CAS.ts readFile) ─────────────


class TestDownloadUrl:
    def test_returns_control_plane_url_not_minio(
        self, client: TestClient, db_session: Session, owner
    ):
        """Regression for effef307: must NOT be a raw MinIO presigned URL —
        MinIO has no public endpoint, so a client outside the relay's own
        compose network can never reach it (DNS: minio, no published port).
        """
        share = make_folder_share(db_session, owner, slug="dlurl-ok")
        token = login(client, owner.email, "test123456")
        data = mint(client, share.id, token)
        with minio_patch(exists=True):
            r = client.get(
                f"/shares/{share.id}/files/attachments/photo.png/download-url",
                headers=bearer(data["token"]),
            )
        assert r.status_code == 200, r.text
        download_url = r.json()["downloadUrl"]
        assert "minio" not in download_url.lower()
        assert download_url.startswith(
            f"http://localhost:8000/shares/{share.id}/files/attachments/photo.png/content?token="
        )

    def test_missing_object_returns_404(self, client: TestClient, db_session: Session, owner):
        share = make_folder_share(db_session, owner, slug="dlurl-missing")
        token = login(client, owner.email, "test123456")
        data = mint(client, share.id, token)
        with minio_patch(exists=False):
            r = client.get(
                f"/shares/{share.id}/files/attachments/photo.png/download-url",
                headers=bearer(data["token"]),
            )
        assert r.status_code == 404

    def test_viewer_can_download(self, client: TestClient, db_session: Session, owner, viewer):
        share = make_folder_share(db_session, owner, slug="dlurl-viewer")
        add_member(db_session, share, viewer, models.ShareMemberRole.VIEWER)
        token = login(client, viewer.email, "test123456")
        data = mint(client, share.id, token)
        with minio_patch(exists=True):
            r = client.get(
                f"/shares/{share.id}/files/attachments/photo.png/download-url",
                headers=bearer(data["token"]),
            )
        assert r.status_code == 200, r.text


# ── GET /shares/{id}/files/{path}/content (byte-serving, effef307) ───────────


def relative_url(download_url: str) -> str:
    """download_url is absolute (control_plane_public_url); TestClient wants
    a path+query relative to its own base_url."""
    from urllib.parse import urlsplit

    parts = urlsplit(download_url)
    return f"{parts.path}?{parts.query}" if parts.query else parts.path


class TestFileContent:
    def test_end_to_end_returns_bytes(self, client: TestClient, db_session: Session, owner):
        share = make_folder_share(db_session, owner, slug="content-e2e")
        token = login(client, owner.email, "test123456")
        data = mint(client, share.id, token)
        with minio_patch(exists=True):
            dl = client.get(
                f"/shares/{share.id}/files/attachments/photo.png/download-url",
                headers=bearer(data["token"]),
            )
            assert dl.status_code == 200, dl.text
            # bare GET, no Authorization header — the credential is in the URL.
            r = client.get(relative_url(dl.json()["downloadUrl"]))
        assert r.status_code == 200, r.text
        assert r.content == b"fake-png-bytes"
        assert r.headers["content-type"] == "image/png"

    def test_viewer_can_fetch_content(self, client: TestClient, db_session: Session, owner, viewer):
        share = make_folder_share(db_session, owner, slug="content-viewer")
        add_member(db_session, share, viewer, models.ShareMemberRole.VIEWER)
        token = login(client, viewer.email, "test123456")
        data = mint(client, share.id, token)
        with minio_patch(exists=True):
            dl = client.get(
                f"/shares/{share.id}/files/attachments/photo.png/download-url",
                headers=bearer(data["token"]),
            )
            r = client.get(relative_url(dl.json()["downloadUrl"]))
        assert r.status_code == 200, r.text

    def test_missing_object_returns_404(self, client: TestClient, db_session: Session, owner):
        share = make_folder_share(db_session, owner, slug="content-missing")
        token = login(client, owner.email, "test123456")
        data = mint(client, share.id, token)
        with minio_patch(exists=True):
            dl = client.get(
                f"/shares/{share.id}/files/attachments/photo.png/download-url",
                headers=bearer(data["token"]),
            )
            assert dl.status_code == 200, dl.text
        with minio_patch(exists=False):
            r = client.get(relative_url(dl.json()["downloadUrl"]))
        assert r.status_code == 404

    def test_garbage_token_returns_401(self, client: TestClient, db_session: Session, owner):
        share = make_folder_share(db_session, owner, slug="content-bad-token")
        r = client.get(
            f"/shares/{share.id}/files/attachments/photo.png/content?token=not-a-real-token"
        )
        assert r.status_code == 401

    def test_no_token_returns_422(self, client: TestClient, db_session: Session, owner):
        """token is a required query param — FastAPI 422s before our code runs."""
        share = make_folder_share(db_session, owner, slug="content-no-token")
        r = client.get(f"/shares/{share.id}/files/attachments/photo.png/content")
        assert r.status_code == 422

    def test_wrong_share_id_returns_403(self, client: TestClient, db_session: Session, owner):
        share_a = make_folder_share(db_session, owner, slug="content-share-a")
        share_b = make_folder_share(db_session, owner, slug="content-share-b")
        token = login(client, owner.email, "test123456")
        data = mint(client, share_a.id, token)
        with minio_patch(exists=True):
            dl = client.get(
                f"/shares/{share_a.id}/files/attachments/photo.png/download-url",
                headers=bearer(data["token"]),
            )
            assert dl.status_code == 200, dl.text
            raw_token = relative_url(dl.json()["downloadUrl"]).split("token=")[-1]
            r = client.get(
                f"/shares/{share_b.id}/files/attachments/photo.png/content?token={raw_token}"
            )
        assert r.status_code == 403

    def test_session_jwt_rejected_not_a_file_token(
        self, client: TestClient, db_session: Session, owner
    ):
        share = make_folder_share(db_session, owner, slug="content-session-jwt")
        token = login(client, owner.email, "test123456")
        r = client.get(f"/shares/{share.id}/files/attachments/photo.png/content?token={token}")
        assert r.status_code == 401


# ── POST /shares/{id}/files/{path}/upload-url (CAS.ts writeFile) ─────────────


class TestUploadUrl:
    def test_owner_gets_upload_url_and_index_updated(
        self, client: TestClient, db_session: Session, owner
    ):
        share = make_folder_share(db_session, owner, slug="upurl-owner")
        token = login(client, owner.email, "test123456")
        data = mint(client, share.id, token)
        with minio_patch(exists=True):
            r = client.post(
                f"/shares/{share.id}/files/attachments/photo.png/upload-url",
                headers=bearer(data["token"]),
            )
        assert r.status_code == 200, r.text
        assert r.json()["uploadUrl"] == "https://minio.test/presigned-put"

        db_session.refresh(share)
        items = share.web_folder_items or []
        matches = [i for i in items if i.get("path") == "attachments/photo.png"]
        assert len(matches) == 1, items
        entry = matches[0]
        assert entry["source"] == "user-upload"
        assert entry["mime"] == "image/png"
        assert entry["sha256"] == "a" * 64
        assert entry["size"] == 12345
        assert entry["storage_key"] == f"web-assets/{share.id}/attachments/photo.png"

    def test_editor_member_can_upload(self, client: TestClient, db_session: Session, owner, editor):
        share = make_folder_share(db_session, owner, slug="upurl-editor")
        add_member(db_session, share, editor, models.ShareMemberRole.EDITOR)
        token = login(client, editor.email, "test123456")
        data = mint(client, share.id, token)
        with minio_patch(exists=True):
            r = client.post(
                f"/shares/{share.id}/files/attachments/photo.png/upload-url",
                headers=bearer(data["token"]),
            )
        assert r.status_code == 200, r.text

    def test_viewer_only_forbidden(self, client: TestClient, db_session: Session, owner, viewer):
        """Least-privilege: minting only required read access, but the
        write-only upload-url route independently 403s a read-only viewer."""
        share = make_folder_share(db_session, owner, slug="upurl-viewer")
        add_member(db_session, share, viewer, models.ShareMemberRole.VIEWER)
        token = login(client, viewer.email, "test123456")
        data = mint(client, share.id, token)
        with minio_patch(exists=True):
            r = client.post(
                f"/shares/{share.id}/files/attachments/photo.png/upload-url",
                headers=bearer(data["token"]),
            )
        assert r.status_code == 403

    def test_re_upload_updates_existing_index_entry(
        self, client: TestClient, db_session: Session, owner
    ):
        share = make_folder_share(db_session, owner, slug="upurl-reupload")
        token = login(client, owner.email, "test123456")
        with minio_patch(exists=True):
            for _ in range(2):
                data = mint(client, share.id, token)
                r = client.post(
                    f"/shares/{share.id}/files/attachments/photo.png/upload-url",
                    headers=bearer(data["token"]),
                )
                assert r.status_code == 200, r.text

        db_session.refresh(share)
        items = share.web_folder_items or []
        matches = [i for i in items if i.get("path") == "attachments/photo.png"]
        assert len(matches) == 1, "re-upload must update in place, not duplicate the index entry"
