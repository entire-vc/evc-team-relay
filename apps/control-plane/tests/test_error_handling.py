"""Tests for enhanced error handling and path validation."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db import models
from app.services import share_service


def auth_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def login(client: TestClient, email: str, password: str) -> str:
    response = client.post("/auth/login", json={"email": email, "password": password})
    assert response.status_code == 200, response.text
    return response.json()["access_token"]


def test_duplicate_share_path_returns_409(client: TestClient) -> None:
    """Test that creating a share with duplicate path returns 409 Conflict."""
    admin_token = login(client, "bootstrap@example.com", "super-secret")

    # Create first share
    response1 = client.post(
        "/shares",
        json={
            "kind": "doc",
            "path": "vault/unique-test.md",
            "visibility": "private",
        },
        headers=auth_headers(admin_token),
    )
    assert response1.status_code == 201, response1.text

    # Try to create duplicate share with same path
    response2 = client.post(
        "/shares",
        json={
            "kind": "doc",
            "path": "vault/unique-test.md",
            "visibility": "public",
        },
        headers=auth_headers(admin_token),
    )

    # Should return 409 Conflict with clear message
    assert (
        response2.status_code == 409
    ), f"Expected 409, got {response2.status_code}: {response2.text}"
    error_data = response2.json()
    assert "error" in error_data
    assert error_data["error"]["code"] == 409
    # Should mention "already exists" or similar
    assert (
        "already exists" in error_data["error"]["message"].lower()
        or "duplicate" in error_data["error"]["message"].lower()
    )


def test_duplicate_folder_share_path_returns_409(client: TestClient) -> None:
    """Exact-duplicate rejection is not doc-only: it applies to folder shares too."""
    admin_token = login(client, "bootstrap@example.com", "super-secret")

    response1 = client.post(
        "/shares",
        json={
            "kind": "folder",
            "path": "Projects/Alpha",
            "visibility": "private",
        },
        headers=auth_headers(admin_token),
    )
    assert response1.status_code == 201, response1.text

    # Same owner, same kind, byte-identical path — only visibility differs.
    response2 = client.post(
        "/shares",
        json={
            "kind": "folder",
            "path": "Projects/Alpha",
            "visibility": "public",
        },
        headers=auth_headers(admin_token),
    )

    assert (
        response2.status_code == 409
    ), f"Expected 409, got {response2.status_code}: {response2.text}"
    error_data = response2.json()
    assert "error" in error_data
    assert error_data["error"]["code"] == 409
    assert (
        "already exists" in error_data["error"]["message"].lower()
        or "duplicate" in error_data["error"]["message"].lower()
    )


@pytest.mark.parametrize(
    "root_first",
    [True, False],
    ids=["root-then-nested", "nested-then-root"],
)
def test_overlapping_folder_shares_still_allowed(
    client: TestClient, db_session, root_first: bool
) -> None:
    """Overlapping-but-different folder shares must KEEP working (regression guard).

    The exact-duplicate 409 must not spill over onto the legitimate pattern of
    a broad share plus a narrower one carved out of it — a root share
    (path="") overlaps every other folder share by construction. Both
    creation orders are exercised because the check queries pre-existing rows,
    so an over-broad predicate would only fail in one direction.

    Also pins that "most specific wins" still resolves to the NESTED share
    regardless of which one was created first.
    """
    admin_token = login(client, "bootstrap@example.com", "super-secret")

    root_payload = {"kind": "folder", "path": "", "visibility": "private"}
    nested_payload = {"kind": "folder", "path": "Projects", "visibility": "private"}
    order = [root_payload, nested_payload] if root_first else [nested_payload, root_payload]

    created_ids: dict[str, str] = {}
    for payload in order:
        response = client.post("/shares", json=payload, headers=auth_headers(admin_token))
        assert (
            response.status_code == 201
        ), f"overlapping folder share {payload['path']!r} was rejected: {response.text}"
        created_ids[payload["path"]] = response.json()["id"]

    admin = db_session.execute(
        select(models.User).where(models.User.email == "bootstrap@example.com")
    ).scalar_one()

    found = share_service.find_share_for_path(db_session, admin, "Projects/x.md")
    assert found is not None, "no share resolved for a path covered by both shares"
    assert (
        str(found.id) == created_ids["Projects"]
    ), "most-specific-wins broken: expected the nested 'Projects' share, got the root share"


def test_path_traversal_rejected(client: TestClient) -> None:
    """Test that path traversal attempts are rejected."""
    admin_token = login(client, "bootstrap@example.com", "super-secret")

    # Try various path traversal patterns
    traversal_paths = [
        "../etc/passwd",
        "vault/../../etc/passwd",
        "vault/../../../secret.md",
        "..\\windows\\system32\\config",
    ]

    for bad_path in traversal_paths:
        response = client.post(
            "/shares",
            json={
                "kind": "doc",
                "path": bad_path,
                "visibility": "private",
            },
            headers=auth_headers(admin_token),
        )

        # Should be rejected with 400 Bad Request
        assert response.status_code == 400, f"Path traversal not blocked for: {bad_path}"
        error_data = response.json()
        assert (
            "traversal" in error_data["error"]["message"].lower()
        ), f"Error message should mention traversal: {error_data}"


def test_absolute_path_rejected(client: TestClient) -> None:
    """Test that absolute paths are rejected."""
    admin_token = login(client, "bootstrap@example.com", "super-secret")

    absolute_paths = [
        "/absolute/path.md",
        "\\absolute\\path.md",
        "C:\\Users\\test.md",
        "D:/documents/file.md",
    ]

    for bad_path in absolute_paths:
        response = client.post(
            "/shares",
            json={
                "kind": "doc",
                "path": bad_path,
                "visibility": "private",
            },
            headers=auth_headers(admin_token),
        )

        assert response.status_code == 400, f"Absolute path not blocked: {bad_path}"
        error_data = response.json()
        # Should mention absolute path or drive letter
        assert (
            "absolute" in error_data["error"]["message"].lower()
            or "drive" in error_data["error"]["message"].lower()
        )


def test_doc_share_extension_validation(client: TestClient) -> None:
    """Test that doc shares require .md or .canvas extension."""
    admin_token = login(client, "bootstrap@example.com", "super-secret")

    invalid_extensions = [
        "vault/document.txt",
        "vault/document.pdf",
        "vault/document",  # No extension
        "vault/document.docx",
    ]

    for invalid_path in invalid_extensions:
        response = client.post(
            "/shares",
            json={
                "kind": "doc",
                "path": invalid_path,
                "visibility": "private",
            },
            headers=auth_headers(admin_token),
        )

        assert response.status_code == 400, f"Invalid extension not blocked: {invalid_path}"
        error_data = response.json()
        assert "extension" in error_data["error"]["message"].lower()


def test_doc_share_valid_extensions_accepted(client: TestClient) -> None:
    """Test that .md and .canvas extensions are accepted for doc shares."""
    admin_token = login(client, "bootstrap@example.com", "super-secret")

    valid_paths = [
        "vault/note.md",
        "vault/diagram.canvas",
        "vault/subfolder/document.MD",  # Case insensitive
        "vault/subfolder/board.CANVAS",
    ]

    for valid_path in valid_paths:
        response = client.post(
            "/shares",
            json={
                "kind": "doc",
                "path": valid_path,
                "visibility": "private",
            },
            headers=auth_headers(admin_token),
        )

        assert (
            response.status_code == 201
        ), f"Valid extension rejected: {valid_path}. Response: {response.text}"


def test_folder_share_no_extension_required(client: TestClient) -> None:
    """Test that folder shares don't require file extensions."""
    admin_token = login(client, "bootstrap@example.com", "super-secret")

    response = client.post(
        "/shares",
        json={
            "kind": "folder",
            "path": "vault/my-folder",
            "visibility": "private",
        },
        headers=auth_headers(admin_token),
    )

    assert response.status_code == 201, f"Folder share without extension rejected: {response.text}"


def test_empty_path_rejected(client: TestClient) -> None:
    """Test that empty/whitespace-only paths are rejected for DOC shares.

    #1f27561a: the schema no longer blanket-rejects "" via Pydantic
    min_length=1 — "" is the canonical "whole vault" path for FOLDER shares
    (see test_root_folder_share.py). For DOC shares, which always need a real
    file, every "empty" spelling is still rejected — now uniformly by
    share_service.validate_share_path_safety, so all of them are 400 rather
    than "" being a special 422 case.
    """
    admin_token = login(client, "bootstrap@example.com", "super-secret")

    for empty_path in ["", "   ", "\t"]:
        response = client.post(
            "/shares",
            json={"kind": "doc", "path": empty_path, "visibility": "private"},
            headers=auth_headers(admin_token),
        )
        assert response.status_code == 400, f"Empty/whitespace path not rejected: '{empty_path}'"


def test_null_byte_rejected(client: TestClient) -> None:
    """Test that paths with null bytes are rejected."""
    admin_token = login(client, "bootstrap@example.com", "super-secret")

    response = client.post(
        "/shares",
        json={
            "kind": "doc",
            "path": "vault/file\x00.md",
            "visibility": "private",
        },
        headers=auth_headers(admin_token),
    )

    assert response.status_code == 400, "Null byte not rejected"
    error_data = response.json()
    assert "null byte" in error_data["error"]["message"].lower()


def test_path_length_limit(client: TestClient) -> None:
    """Test that excessively long paths are rejected."""
    admin_token = login(client, "bootstrap@example.com", "super-secret")

    # Create path longer than 1000 chars
    long_path = "vault/" + "a" * 1100 + ".md"

    response = client.post(
        "/shares",
        json={
            "kind": "doc",
            "path": long_path,
            "visibility": "private",
        },
        headers=auth_headers(admin_token),
    )

    # Pydantic validates max_length=512, returns 422
    assert response.status_code == 422, "Excessively long path not rejected"


def test_protected_share_without_password_rejected(client: TestClient) -> None:
    """Test that protected shares require a password."""
    admin_token = login(client, "bootstrap@example.com", "super-secret")

    response = client.post(
        "/shares",
        json={
            "kind": "doc",
            "path": "vault/protected.md",
            "visibility": "protected",
            # Missing password field
        },
        headers=auth_headers(admin_token),
    )

    assert response.status_code == 400, "Protected share without password not rejected"
    error_data = response.json()
    assert "password" in error_data["error"]["message"].lower()


def test_protected_share_with_password_accepted(client: TestClient) -> None:
    """Test that protected shares with password are accepted."""
    admin_token = login(client, "bootstrap@example.com", "super-secret")

    response = client.post(
        "/shares",
        json={
            "kind": "doc",
            "path": "vault/protected-valid.md",
            "visibility": "protected",
            "password": "SecurePassword123!",
        },
        headers=auth_headers(admin_token),
    )

    assert response.status_code == 201, f"Protected share with password rejected: {response.text}"
    share_data = response.json()
    assert share_data["visibility"] == "protected"
    # Password hash should be stored but not returned
    assert "password" not in share_data
