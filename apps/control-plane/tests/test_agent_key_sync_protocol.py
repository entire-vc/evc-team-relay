"""Agent-key access to the SYNC PROTOCOL (task #ee1745ce).

The /shares/{id}/files-index + /download + /sync-write trio is the only surface
that carries `sha256` and `updated_at`, i.e. the only one on which two copies of
a document can be reconciled rather than blindly overwritten. It used to accept
user JWTs only, so the agent key a Mesh project is configured with could write
bytes through /v1/web/.../sync-upload but could not learn a document's hash —
and therefore could not write safely.

These tests pin four things:
  1. the SAME agent key now reads hash+mtime and writes through this protocol;
  2. a write whose If-Match does not match the stored sha256 is refused AND
     leaves the stored bytes untouched — asserted on the document body, not on
     the status code, because a 412 returned after a write that actually landed
     is indistinguishable from an honest one by status alone;
  3. the scope boundary did not widen: read-only cannot write, write-only cannot
     read, a key is still confined to its own share, and it still buys nothing
     on the user-only share-management routes;
  4. a read-scoped key can also mint an attachment file-token (POST
     .../file-token — symmetric extension, #d4c851af finding 3) and the whole
     chain down to a presigned URL works off that token; a key without read
     scope still cannot.
"""

from __future__ import annotations

import hashlib
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.db import models

BASE = "/v1/shares"


# ── helpers ──────────────────────────────────────────────────────────────────


def make_folder_share(
    db: Session,
    user: models.User,
    folder_items: list | None = None,
) -> models.Share:
    share = models.Share(
        kind=models.ShareKind.FOLDER,
        path="SyncFolder/",
        visibility=models.ShareVisibility.PRIVATE,
        owner_user_id=user.id,
        web_published=False,
        web_slug=None,
        web_folder_items=folder_items or [],
    )
    db.add(share)
    db.commit()
    db.refresh(share)
    return share


def make_agent_key(
    db: Session,
    share: models.Share,
    scopes: str = "read,write",
    expires_at: datetime | None = None,
    revoked: bool = False,
) -> str:
    raw_key = "tr_agent_" + secrets.token_hex(24)
    ak = models.ShareAgentKey(
        share_id=share.id,
        key_hash=hashlib.sha256(raw_key.encode()).hexdigest(),
        label="sync-protocol test key",
        scopes=scopes,
        expires_at=expires_at,
        revoked_at=datetime.now(timezone.utc) if revoked else None,
        created_by=share.owner_user_id,
    )
    db.add(ak)
    db.commit()
    return raw_key


def sync_item(path: str, content: str) -> dict:
    """An index entry shaped exactly like one written by sync-upload."""
    body = content.encode("utf-8")
    return {
        "path": path,
        "name": path.split("/")[-1],
        "type": "doc",
        "source": "sync-artifact",
        "mime": "text/markdown",
        "size": len(body),
        "sha256": hashlib.sha256(body).hexdigest(),
        "modified_at": "2026-08-19T12:00:00+00:00",
        "storage_key": f"sync-uploads/x/{hashlib.sha256(body).hexdigest()}",
        "content": content,
    }


@pytest.fixture
def minio():
    """Patch the MinIO client and expose it so tests can assert on writes."""
    mock_client = MagicMock()
    mock_client.bucket_exists.return_value = True
    mock_client.put_object.return_value = None
    with patch("app.api.routers.shares._get_minio_client", return_value=mock_client):
        yield mock_client


def err_message(resp) -> str:
    """The API wraps errors as {"error": {"code", "message"}} (middleware/errors.py)."""
    body = resp.json()
    if isinstance(body, dict) and "error" in body:
        return str(body["error"].get("message", ""))
    return str(body.get("detail", ""))


def key_headers(raw_key: str, **extra: str) -> dict[str, str]:
    return {"X-Agent-Key": raw_key, **extra}


def read_body(client: TestClient, share_id, raw_key: str, path: str) -> str:
    """Fetch the document as the server currently holds it."""
    resp = client.get(
        f"{BASE}/{share_id}/download",
        params={"path": path},
        headers=key_headers(raw_key),
    )
    assert resp.status_code == 200, resp.text
    return resp.text


# ── 1. read with the agent key ───────────────────────────────────────────────


class TestAgentKeyRead:
    def test_files_index_returns_sha256_and_updated_at(
        self, client: TestClient, test_user: models.User, db_session: Session
    ):
        item = sync_item("notes/hello.md", "# Hello")
        share = make_folder_share(db_session, test_user, folder_items=[item])
        raw_key = make_agent_key(db_session, share, scopes="read")

        resp = client.get(f"{BASE}/{share.id}/files-index", headers=key_headers(raw_key))

        assert resp.status_code == 200, resp.text
        entries = resp.json()
        assert len(entries) == 1
        assert entries[0]["path"] == "notes/hello.md"
        assert entries[0]["sha256"] == item["sha256"]
        assert entries[0]["updated_at"] == "2026-08-19T12:00:00+00:00"

    def test_download_returns_content_and_etag(
        self, client: TestClient, test_user: models.User, db_session: Session
    ):
        item = sync_item("notes/hello.md", "# Hello")
        share = make_folder_share(db_session, test_user, folder_items=[item])
        raw_key = make_agent_key(db_session, share, scopes="read")

        resp = client.get(
            f"{BASE}/{share.id}/download",
            params={"path": "notes/hello.md"},
            headers=key_headers(raw_key),
        )

        assert resp.status_code == 200, resp.text
        assert resp.text == "# Hello"
        assert resp.headers["etag"] == f'"{item["sha256"]}"'
        assert resp.headers["x-updated-at"] == "2026-08-19T12:00:00+00:00"

    def test_read_stamps_last_used_at(
        self, client: TestClient, test_user: models.User, db_session: Session
    ):
        share = make_folder_share(db_session, test_user, folder_items=[sync_item("a.md", "a")])
        raw_key = make_agent_key(db_session, share, scopes="read")

        client.get(f"{BASE}/{share.id}/files-index", headers=key_headers(raw_key))

        db_session.expire_all()
        ak = db_session.query(models.ShareAgentKey).filter_by(share_id=share.id).one()
        assert ak.last_used_at is not None

    def test_revoked_key_cannot_read(
        self, client: TestClient, test_user: models.User, db_session: Session
    ):
        share = make_folder_share(db_session, test_user, folder_items=[sync_item("a.md", "a")])
        raw_key = make_agent_key(db_session, share, scopes="read,write", revoked=True)

        resp = client.get(f"{BASE}/{share.id}/files-index", headers=key_headers(raw_key))
        assert resp.status_code == 403
        assert "revoked" in err_message(resp).lower()

    def test_expired_key_cannot_read(
        self, client: TestClient, test_user: models.User, db_session: Session
    ):
        share = make_folder_share(db_session, test_user, folder_items=[sync_item("a.md", "a")])
        raw_key = make_agent_key(
            db_session,
            share,
            scopes="read,write",
            expires_at=datetime.now(timezone.utc) - timedelta(days=1),
        )

        resp = client.get(f"{BASE}/{share.id}/files-index", headers=key_headers(raw_key))
        assert resp.status_code == 403
        assert "expired" in err_message(resp).lower()

    def test_no_credential_still_401(
        self, client: TestClient, test_user: models.User, db_session: Session
    ):
        share = make_folder_share(db_session, test_user, folder_items=[sync_item("a.md", "a")])
        resp = client.get(f"{BASE}/{share.id}/files-index")
        assert resp.status_code == 401


# ── 2. write with the agent key, conditional on sha256 ───────────────────────


class TestAgentKeyConditionalWrite:
    def test_write_with_matching_sha256_succeeds(
        self, client: TestClient, test_user: models.User, db_session: Session, minio
    ):
        item = sync_item("notes/hello.md", "# Hello")
        share = make_folder_share(db_session, test_user, folder_items=[item])
        raw_key = make_agent_key(db_session, share, scopes="read,write")

        resp = client.put(
            f"{BASE}/{share.id}/sync-write",
            params={"path": "notes/hello.md"},
            content=b"# Hello, edited",
            headers=key_headers(
                raw_key,
                **{"Content-Type": "text/markdown", "If-Match": f'"{item["sha256"]}"'},
            ),
        )

        assert resp.status_code == 200, resp.text
        expected_sha = hashlib.sha256(b"# Hello, edited").hexdigest()
        assert resp.json()["sha256"] == expected_sha
        assert resp.json()["size"] == len(b"# Hello, edited")
        assert resp.headers["etag"] == f'"{expected_sha}"'
        # The bytes the server now serves are the new ones.
        assert read_body(client, share.id, raw_key, "notes/hello.md") == "# Hello, edited"
        minio.put_object.assert_called_once()

    def test_bare_unquoted_sha256_is_accepted(
        self, client: TestClient, test_user: models.User, db_session: Session, minio
    ):
        item = sync_item("a.md", "one")
        share = make_folder_share(db_session, test_user, folder_items=[item])
        raw_key = make_agent_key(db_session, share)

        resp = client.put(
            f"{BASE}/{share.id}/sync-write",
            params={"path": "a.md"},
            content=b"two",
            headers=key_headers(
                raw_key, **{"Content-Type": "text/markdown", "If-Match": item["sha256"]}
            ),
        )
        assert resp.status_code == 200, resp.text

    def test_create_only_with_if_none_match(
        self, client: TestClient, test_user: models.User, db_session: Session, minio
    ):
        share = make_folder_share(db_session, test_user)
        raw_key = make_agent_key(db_session, share)

        resp = client.put(
            f"{BASE}/{share.id}/sync-write",
            params={"path": "fresh.md"},
            content=b"brand new",
            headers=key_headers(raw_key, **{"Content-Type": "text/markdown", "If-None-Match": "*"}),
        )
        assert resp.status_code == 200, resp.text
        assert read_body(client, share.id, raw_key, "fresh.md") == "brand new"

    def test_created_document_appears_in_files_index(
        self, client: TestClient, test_user: models.User, db_session: Session, minio
    ):
        share = make_folder_share(db_session, test_user)
        raw_key = make_agent_key(db_session, share)

        client.put(
            f"{BASE}/{share.id}/sync-write",
            params={"path": "fresh.md"},
            content=b"brand new",
            headers=key_headers(raw_key, **{"Content-Type": "text/markdown", "If-None-Match": "*"}),
        )

        index = client.get(f"{BASE}/{share.id}/files-index", headers=key_headers(raw_key)).json()
        entry = next(e for e in index if e["path"] == "fresh.md")
        assert entry["sha256"] == hashlib.sha256(b"brand new").hexdigest()
        assert entry["updated_at"]

    def test_round_trip_read_then_write_with_returned_hash(
        self, client: TestClient, test_user: models.User, db_session: Session, minio
    ):
        """The hash a caller reads back is directly usable as the next If-Match."""
        share = make_folder_share(db_session, test_user, folder_items=[sync_item("loop.md", "v1")])
        raw_key = make_agent_key(db_session, share)

        got = client.get(
            f"{BASE}/{share.id}/download",
            params={"path": "loop.md"},
            headers=key_headers(raw_key),
        )
        etag = got.headers["etag"]

        resp = client.put(
            f"{BASE}/{share.id}/sync-write",
            params={"path": "loop.md"},
            content=b"v2",
            headers=key_headers(raw_key, **{"Content-Type": "text/markdown", "If-Match": etag}),
        )
        assert resp.status_code == 200, resp.text
        assert read_body(client, share.id, raw_key, "loop.md") == "v2"


# ── 3. the load-bearing negative: stale hash must not overwrite ──────────────


class TestStaleWriteIsRefusedAndChangesNothing:
    def test_mismatched_sha256_is_refused_and_body_unchanged(
        self, client: TestClient, test_user: models.User, db_session: Session, minio
    ):
        item = sync_item("notes/hello.md", "# Original")
        share = make_folder_share(db_session, test_user, folder_items=[item])
        raw_key = make_agent_key(db_session, share)
        stale = hashlib.sha256(b"# Some version this client last saw").hexdigest()

        resp = client.put(
            f"{BASE}/{share.id}/sync-write",
            params={"path": "notes/hello.md"},
            content=b"# Clobbered",
            headers=key_headers(
                raw_key, **{"Content-Type": "text/markdown", "If-Match": f'"{stale}"'}
            ),
        )

        assert resp.status_code == 412, resp.text
        # The verdict that matters: the document is byte-for-byte what it was.
        assert read_body(client, share.id, raw_key, "notes/hello.md") == "# Original"
        # And the index still advertises the ORIGINAL hash, so a well-behaved
        # client is not handed a version that never existed.
        index = client.get(f"{BASE}/{share.id}/files-index", headers=key_headers(raw_key)).json()
        assert index[0]["sha256"] == item["sha256"]
        assert index[0]["updated_at"] == item["modified_at"]
        # Nothing reached the object store either.
        minio.put_object.assert_not_called()

    def test_unconditional_write_is_refused_and_body_unchanged(
        self, client: TestClient, test_user: models.User, db_session: Session, minio
    ):
        share = make_folder_share(
            db_session, test_user, folder_items=[sync_item("notes/hello.md", "# Original")]
        )
        raw_key = make_agent_key(db_session, share)

        resp = client.put(
            f"{BASE}/{share.id}/sync-write",
            params={"path": "notes/hello.md"},
            content=b"# Clobbered",
            headers=key_headers(raw_key, **{"Content-Type": "text/markdown"}),
        )

        assert resp.status_code == 428, resp.text
        assert read_body(client, share.id, raw_key, "notes/hello.md") == "# Original"
        minio.put_object.assert_not_called()

    def test_if_match_wildcard_is_refused_and_body_unchanged(
        self, client: TestClient, test_user: models.User, db_session: Session, minio
    ):
        """`If-Match: *` only asserts existence — that is a blind overwrite."""
        share = make_folder_share(
            db_session, test_user, folder_items=[sync_item("notes/hello.md", "# Original")]
        )
        raw_key = make_agent_key(db_session, share)

        resp = client.put(
            f"{BASE}/{share.id}/sync-write",
            params={"path": "notes/hello.md"},
            content=b"# Clobbered",
            headers=key_headers(raw_key, **{"Content-Type": "text/markdown", "If-Match": "*"}),
        )

        assert resp.status_code == 428, resp.text
        assert read_body(client, share.id, raw_key, "notes/hello.md") == "# Original"
        minio.put_object.assert_not_called()

    def test_if_none_match_on_existing_file_is_refused_and_body_unchanged(
        self, client: TestClient, test_user: models.User, db_session: Session, minio
    ):
        share = make_folder_share(
            db_session, test_user, folder_items=[sync_item("notes/hello.md", "# Original")]
        )
        raw_key = make_agent_key(db_session, share)

        resp = client.put(
            f"{BASE}/{share.id}/sync-write",
            params={"path": "notes/hello.md"},
            content=b"# Clobbered",
            headers=key_headers(raw_key, **{"Content-Type": "text/markdown", "If-None-Match": "*"}),
        )

        assert resp.status_code == 412, resp.text
        assert read_body(client, share.id, raw_key, "notes/hello.md") == "# Original"
        minio.put_object.assert_not_called()

    def test_if_match_on_absent_file_is_refused_and_creates_nothing(
        self, client: TestClient, test_user: models.User, db_session: Session, minio
    ):
        share = make_folder_share(db_session, test_user)
        raw_key = make_agent_key(db_session, share)

        resp = client.put(
            f"{BASE}/{share.id}/sync-write",
            params={"path": "ghost.md"},
            content=b"content",
            headers=key_headers(
                raw_key,
                **{"Content-Type": "text/markdown", "If-Match": f'"{"0" * 64}"'},
            ),
        )

        assert resp.status_code == 412, resp.text
        index = client.get(f"{BASE}/{share.id}/files-index", headers=key_headers(raw_key)).json()
        assert index == []
        minio.put_object.assert_not_called()

    def test_both_preconditions_is_a_client_error_and_writes_nothing(
        self, client: TestClient, test_user: models.User, db_session: Session, minio
    ):
        item = sync_item("notes/hello.md", "# Original")
        share = make_folder_share(db_session, test_user, folder_items=[item])
        raw_key = make_agent_key(db_session, share)

        resp = client.put(
            f"{BASE}/{share.id}/sync-write",
            params={"path": "notes/hello.md"},
            content=b"# Clobbered",
            headers=key_headers(
                raw_key,
                **{
                    "Content-Type": "text/markdown",
                    "If-Match": f'"{item["sha256"]}"',
                    "If-None-Match": "*",
                },
            ),
        )

        assert resp.status_code == 400, resp.text
        assert read_body(client, share.id, raw_key, "notes/hello.md") == "# Original"
        minio.put_object.assert_not_called()

    def test_item_without_stored_sha256_can_never_be_matched(
        self, client: TestClient, test_user: models.User, db_session: Session, minio
    ):
        """A mesh-artifact entry has no sha256, so no If-Match can match it."""
        item = {
            "path": "agent/report.md",
            "name": "report.md",
            "type": "doc",
            "source": "mesh-artifact",
            "mime": "text/markdown",
            "size": 9,
            "modified_at": "2026-08-19T10:00:00+00:00",
            "storage_key": "web-assets/x/report.md",
            "content": "# Report",
        }
        share = make_folder_share(db_session, test_user, folder_items=[item])
        raw_key = make_agent_key(db_session, share)

        resp = client.put(
            f"{BASE}/{share.id}/sync-write",
            params={"path": "agent/report.md"},
            content=b"# Clobbered",
            headers=key_headers(
                raw_key,
                **{
                    "Content-Type": "text/markdown",
                    "If-Match": f'"{hashlib.sha256(b"# Report").hexdigest()}"',
                },
            ),
        )

        assert resp.status_code == 412, resp.text
        assert read_body(client, share.id, raw_key, "agent/report.md") == "# Report"
        minio.put_object.assert_not_called()


# ── 3b. ё and other non-ASCII letters are not path-invalid (finding 4) ───────


class TestUnicodePaths:
    """`_ALLOWED_FILE_PATH_RE` used to enumerate a Cyrillic alphabet range
    (А-Яа-я) that excludes ё/Ё (U+0451/U+0401 sort outside that block) — and
    would have excluded ANY other script's letters (ї, ә, é, ...) the same
    way. Fixed to a property-based check (\\w, Unicode-aware) instead of an
    alphabet enumeration."""

    def test_document_with_yo_can_be_created_and_read(
        self, client: TestClient, test_user: models.User, db_session: Session, minio
    ):
        share = make_folder_share(db_session, test_user)
        raw_key = make_agent_key(db_session, share)
        path = "Ёлка.md"

        create = client.put(
            f"{BASE}/{share.id}/sync-write",
            params={"path": path},
            content="про ёлку".encode("utf-8"),
            headers=key_headers(raw_key, **{"Content-Type": "text/markdown", "If-None-Match": "*"}),
        )
        assert create.status_code == 200, create.text
        assert read_body(client, share.id, raw_key, path) == "про ёлку"

        index = client.get(f"{BASE}/{share.id}/files-index", headers=key_headers(raw_key)).json()
        assert any(e["path"] == path for e in index)

    def test_document_with_yo_mid_word_can_be_created(
        self, client: TestClient, test_user: models.User, db_session: Session, minio
    ):
        share = make_folder_share(db_session, test_user)
        raw_key = make_agent_key(db_session, share)
        path = "Всё о релизе.md"

        create = client.put(
            f"{BASE}/{share.id}/sync-write",
            params={"path": path},
            content=b"release notes",
            headers=key_headers(raw_key, **{"Content-Type": "text/markdown", "If-None-Match": "*"}),
        )
        assert create.status_code == 200, create.text

    def test_diacritic_path_can_be_created(
        self, client: TestClient, test_user: models.User, db_session: Session, minio
    ):
        share = make_folder_share(db_session, test_user)
        raw_key = make_agent_key(db_session, share)
        path = "café.md"

        create = client.put(
            f"{BASE}/{share.id}/sync-write",
            params={"path": path},
            content=b"notes",
            headers=key_headers(raw_key, **{"Content-Type": "text/markdown", "If-None-Match": "*"}),
        )
        assert create.status_code == 200, create.text

    def test_traversal_still_rejected_alongside_widened_alphabet(
        self, client: TestClient, test_user: models.User, db_session: Session, minio
    ):
        """The alphabet widening must not loosen the separate `..`/depth/length
        checks in _validate_file_path — those run before the regex either way."""
        share = make_folder_share(db_session, test_user)
        raw_key = make_agent_key(db_session, share)

        resp = client.put(
            f"{BASE}/{share.id}/sync-write",
            params={"path": "../../etc/passwd"},
            content=b"pwned",
            headers=key_headers(raw_key, **{"Content-Type": "text/markdown", "If-None-Match": "*"}),
        )
        assert resp.status_code == 400
        minio.put_object.assert_not_called()


# ── 4. the scope boundary did not widen ──────────────────────────────────────


class TestScopeBoundary:
    def test_read_only_key_cannot_write(
        self, client: TestClient, test_user: models.User, db_session: Session, minio
    ):
        item = sync_item("notes/hello.md", "# Original")
        share = make_folder_share(db_session, test_user, folder_items=[item])
        raw_key = make_agent_key(db_session, share, scopes="read")

        resp = client.put(
            f"{BASE}/{share.id}/sync-write",
            params={"path": "notes/hello.md"},
            content=b"# Clobbered",
            headers=key_headers(
                raw_key,
                **{"Content-Type": "text/markdown", "If-Match": f'"{item["sha256"]}"'},
            ),
        )

        assert resp.status_code == 403, resp.text
        assert "write scope" in err_message(resp)
        assert read_body(client, share.id, raw_key, "notes/hello.md") == "# Original"
        minio.put_object.assert_not_called()

    def test_write_only_key_cannot_read(
        self, client: TestClient, test_user: models.User, db_session: Session
    ):
        """Literal scope policy (ADR-0001): write does not imply read, and the
        lenient-read grace deliberately does not reach the sync protocol."""
        share = make_folder_share(
            db_session, test_user, folder_items=[sync_item("secret.md", "vault content")]
        )
        raw_key = make_agent_key(db_session, share, scopes="write")

        index = client.get(f"{BASE}/{share.id}/files-index", headers=key_headers(raw_key))
        assert index.status_code == 403
        assert "read scope" in err_message(index)

        download = client.get(
            f"{BASE}/{share.id}/download",
            params={"path": "secret.md"},
            headers=key_headers(raw_key),
        )
        assert download.status_code == 403
        assert "vault content" not in download.text

    def test_key_cannot_reach_another_share(
        self, client: TestClient, test_user: models.User, db_session: Session, minio
    ):
        mine = make_folder_share(db_session, test_user)
        theirs = make_folder_share(
            db_session, test_user, folder_items=[sync_item("other.md", "not yours")]
        )
        raw_key = make_agent_key(db_session, mine)

        read = client.get(f"{BASE}/{theirs.id}/files-index", headers=key_headers(raw_key))
        assert read.status_code == 403
        assert "not valid for this share" in err_message(read)

        write = client.put(
            f"{BASE}/{theirs.id}/sync-write",
            params={"path": "other.md"},
            content=b"clobber",
            headers=key_headers(raw_key, **{"Content-Type": "text/markdown", "If-None-Match": "*"}),
        )
        assert write.status_code == 403
        minio.put_object.assert_not_called()

    def test_key_cannot_list_shares(
        self, client: TestClient, test_user: models.User, db_session: Session
    ):
        """Share management stays user-only — the key buys no account-wide reach."""
        share = make_folder_share(db_session, test_user)
        raw_key = make_agent_key(db_session, share)

        resp = client.get(BASE, headers=key_headers(raw_key))
        assert resp.status_code == 401

    def test_key_cannot_delete_the_share(
        self, client: TestClient, test_user: models.User, db_session: Session
    ):
        share = make_folder_share(db_session, test_user)
        raw_key = make_agent_key(db_session, share)

        resp = client.delete(f"{BASE}/{share.id}", headers=key_headers(raw_key))
        assert resp.status_code == 401
        assert db_session.get(models.Share, share.id) is not None

    def test_key_cannot_add_members(
        self, client: TestClient, test_user: models.User, db_session: Session
    ):
        share = make_folder_share(db_session, test_user)
        raw_key = make_agent_key(db_session, share)

        resp = client.post(
            f"{BASE}/{share.id}/members",
            json={"email": "intruder@example.com", "role": "editor"},
            headers=key_headers(raw_key),
        )
        assert resp.status_code == 401

    def test_write_only_key_cannot_mint_a_file_token(
        self, client: TestClient, test_user: models.User, db_session: Session
    ):
        """Minting only ever required read (mirrors the user-JWT floor at
        create_file_token) — write does not imply read (ADR-0001)."""
        share = make_folder_share(db_session, test_user)
        raw_key = make_agent_key(db_session, share, scopes="write")

        resp = client.post(
            f"{BASE}/{share.id}/file-token",
            json={
                "path": "a.png",
                "sha256": "0" * 64,
                "content_type": "image/png",
                "content_length": 10,
            },
            headers=key_headers(raw_key),
        )
        assert resp.status_code == 403
        assert "read scope" in err_message(resp)

    def test_write_rejects_non_folder_share(
        self, client: TestClient, test_user: models.User, db_session: Session, minio
    ):
        doc_share = models.Share(
            kind=models.ShareKind.DOC,
            path="Note.md",
            visibility=models.ShareVisibility.PRIVATE,
            owner_user_id=test_user.id,
        )
        db_session.add(doc_share)
        db_session.commit()
        db_session.refresh(doc_share)
        raw_key = make_agent_key(db_session, doc_share)

        resp = client.put(
            f"{BASE}/{doc_share.id}/sync-write",
            params={"path": "a.md"},
            content=b"x",
            headers=key_headers(raw_key, **{"Content-Type": "text/markdown", "If-None-Match": "*"}),
        )
        assert resp.status_code == 400
        minio.put_object.assert_not_called()

    def test_write_rejects_path_traversal(
        self, client: TestClient, test_user: models.User, db_session: Session, minio
    ):
        share = make_folder_share(db_session, test_user)
        raw_key = make_agent_key(db_session, share)

        resp = client.put(
            f"{BASE}/{share.id}/sync-write",
            params={"path": "../../etc/passwd"},
            content=b"x",
            headers=key_headers(raw_key, **{"Content-Type": "text/markdown", "If-None-Match": "*"}),
        )
        assert resp.status_code == 400
        minio.put_object.assert_not_called()

    def test_unknown_key_is_401(
        self, client: TestClient, test_user: models.User, db_session: Session
    ):
        share = make_folder_share(db_session, test_user)
        resp = client.get(
            f"{BASE}/{share.id}/files-index",
            headers=key_headers("tr_agent_" + uuid.uuid4().hex),
        )
        assert resp.status_code == 401


# ── 5. the user-JWT path is unchanged ────────────────────────────────────────


class TestUserJwtPathUnchanged:
    def _login(self, client: TestClient, email: str, password: str) -> str:
        resp = client.post("/auth/login", json={"email": email, "password": password})
        assert resp.status_code == 200, resp.text
        return resp.json()["access_token"]

    def test_owner_still_reads_with_bearer(
        self, client: TestClient, test_user: models.User, db_session: Session
    ):
        item = sync_item("notes/hello.md", "# Hello")
        share = make_folder_share(db_session, test_user, folder_items=[item])
        token = self._login(client, test_user.email, "test123456")

        resp = client.get(
            f"{BASE}/{share.id}/files-index", headers={"Authorization": f"Bearer {token}"}
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()[0]["sha256"] == item["sha256"]

    def test_owner_can_write_conditionally_with_bearer(
        self, client: TestClient, test_user: models.User, db_session: Session, minio
    ):
        item = sync_item("notes/hello.md", "# Hello")
        share = make_folder_share(db_session, test_user, folder_items=[item])
        token = self._login(client, test_user.email, "test123456")

        resp = client.put(
            f"{BASE}/{share.id}/sync-write",
            params={"path": "notes/hello.md"},
            content=b"# Edited by a human",
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "text/markdown",
                "If-Match": f'"{item["sha256"]}"',
            },
        )
        assert resp.status_code == 200, resp.text

    def test_non_member_bearer_is_still_refused(
        self, client: TestClient, test_user: models.User, db_session: Session
    ):
        from app.core import security as sec

        outsider = models.User(
            email="outsider@example.com",
            password_hash=sec.get_password_hash("outsider-password"),
            is_active=True,
        )
        db_session.add(outsider)
        db_session.commit()

        share = make_folder_share(
            db_session, test_user, folder_items=[sync_item("secret.md", "vault content")]
        )
        token = self._login(client, "outsider@example.com", "outsider-password")

        resp = client.get(
            f"{BASE}/{share.id}/files-index", headers={"Authorization": f"Bearer {token}"}
        )
        assert resp.status_code == 403


# ── 6. agent key reaches the attachment file-token chain (finding 3) ─────────


class TestAgentKeyFileToken:
    def test_read_scoped_key_can_mint(
        self, client: TestClient, test_user: models.User, db_session: Session
    ):
        share = make_folder_share(db_session, test_user)
        raw_key = make_agent_key(db_session, share, scopes="read")

        resp = client.post(
            f"{BASE}/{share.id}/file-token",
            json={
                "path": "attachments/photo.png",
                "sha256": "a" * 64,
                "content_type": "image/png",
                "content_length": 12345,
            },
            headers=key_headers(raw_key),
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["token"]

    def test_minted_token_reaches_a_presigned_download_url(
        self, client: TestClient, test_user: models.User, db_session: Session
    ):
        """End to end: an agent key with no models.User of its own can still
        walk file-token -> download-url, because the token's subject falls
        back to the share owner and the owner always clears ensure_read_access."""
        share = make_folder_share(db_session, test_user)
        raw_key = make_agent_key(db_session, share, scopes="read")

        mint_resp = client.post(
            f"{BASE}/{share.id}/file-token",
            json={
                "path": "attachments/photo.png",
                "sha256": "a" * 64,
                "content_type": "image/png",
                "content_length": 12345,
            },
            headers=key_headers(raw_key),
        )
        assert mint_resp.status_code == 200, mint_resp.text
        file_token = mint_resp.json()["token"]

        mock_client = MagicMock()
        mock_client.bucket_exists.return_value = True
        mock_client.stat_object.return_value = MagicMock()
        mock_client.presigned_get_object.return_value = "https://minio.test/presigned-get"
        with patch("app.api.routers.shares._get_minio_client", return_value=mock_client):
            dl_resp = client.get(
                f"{BASE}/{share.id}/files/attachments/photo.png/download-url",
                headers={"Authorization": f"Bearer {file_token}"},
            )
        assert dl_resp.status_code == 200, dl_resp.text
        assert dl_resp.json()["downloadUrl"] == "https://minio.test/presigned-get"

    def test_write_only_key_still_cannot_mint(
        self, client: TestClient, test_user: models.User, db_session: Session
    ):
        share = make_folder_share(db_session, test_user)
        raw_key = make_agent_key(db_session, share, scopes="write")

        resp = client.post(
            f"{BASE}/{share.id}/file-token",
            json={
                "path": "attachments/photo.png",
                "sha256": "a" * 64,
                "content_type": "image/png",
                "content_length": 12345,
            },
            headers=key_headers(raw_key),
        )
        assert resp.status_code == 403
        assert "read scope" in err_message(resp)

    def test_key_cannot_mint_for_another_share(
        self, client: TestClient, test_user: models.User, db_session: Session
    ):
        mine = make_folder_share(db_session, test_user)
        theirs = make_folder_share(db_session, test_user)
        raw_key = make_agent_key(db_session, mine, scopes="read")

        resp = client.post(
            f"{BASE}/{theirs.id}/file-token",
            json={
                "path": "attachments/photo.png",
                "sha256": "a" * 64,
                "content_type": "image/png",
                "content_length": 12345,
            },
            headers=key_headers(raw_key),
        )
        assert resp.status_code == 403
        assert "not valid for this share" in err_message(resp)

    def _mint(self, client: TestClient, share_id, raw_key: str) -> str:
        resp = client.post(
            f"{BASE}/{share_id}/file-token",
            json={
                "path": "attachments/photo.png",
                "sha256": "a" * 64,
                "content_type": "image/png",
                "content_length": 12345,
            },
            headers=key_headers(raw_key),
        )
        assert resp.status_code == 200, resp.text
        return resp.json()["token"]

    def test_read_scoped_key_token_cannot_reach_upload_url(
        self, client: TestClient, test_user: models.User, db_session: Session
    ):
        """The bug this pins (#d4c851af, found on review): create_file_token
        mints the token with the share OWNER as subject (an agent key has no
        models.User of its own), and get_file_upload_url's ensure_write_access
        check re-derives access from that subject — which is always the
        owner, so it always passes regardless of the minting key's own scope.
        A read-only key could mint a token and then use it to get a write
        presigned URL: read escalating to write. Must be 403, and MinIO must
        never be asked for a presigned URL."""
        share = make_folder_share(db_session, test_user)
        raw_key = make_agent_key(db_session, share, scopes="read")
        file_token = self._mint(client, share.id, raw_key)

        mock_client = MagicMock()
        with patch("app.api.routers.shares._get_minio_client", return_value=mock_client):
            resp = client.post(
                f"{BASE}/{share.id}/files/attachments/photo.png/upload-url",
                headers={"Authorization": f"Bearer {file_token}"},
            )
        assert resp.status_code == 403, resp.text
        assert "write scope" in err_message(resp)
        mock_client.presigned_put_object.assert_not_called()

    def test_read_write_scoped_key_token_reaches_upload_url(
        self, client: TestClient, test_user: models.User, db_session: Session
    ):
        """Positive control for the test above: a key that genuinely holds
        write scope must still work end to end — otherwise the 403 above
        would be meaningless (a broken chain 403s on everything)."""
        share = make_folder_share(db_session, test_user)
        raw_key = make_agent_key(db_session, share, scopes="read,write")
        file_token = self._mint(client, share.id, raw_key)

        mock_client = MagicMock()
        mock_client.bucket_exists.return_value = True
        mock_client.presigned_put_object.return_value = "https://minio.test/presigned-put"
        with patch("app.api.routers.shares._get_minio_client", return_value=mock_client):
            resp = client.post(
                f"{BASE}/{share.id}/files/attachments/photo.png/upload-url",
                headers={"Authorization": f"Bearer {file_token}"},
            )
        assert resp.status_code == 200, resp.text
        assert resp.json()["uploadUrl"] == "https://minio.test/presigned-put"
        mock_client.presigned_put_object.assert_called_once()
