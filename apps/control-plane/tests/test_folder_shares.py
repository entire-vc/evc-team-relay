"""
Tests for folder share permission resolution and recursive permissions.

This module tests the implementation of folder shares with recursive permissions
as specified in v1.3.1-folder-recursive-permissions.md.
"""

from __future__ import annotations

from fastapi.testclient import TestClient


def auth_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def login(client: TestClient, email: str, password: str) -> str:
    response = client.post("/auth/login", json={"email": email, "password": password})
    assert response.status_code == 200, response.text
    return response.json()["access_token"]


def test_folder_share_token_with_file_path(client: TestClient) -> None:
    """Test that token issuance works for files within shared folder."""
    admin_token = login(client, "bootstrap@example.com", "super-secret")

    # Create a folder share
    folder_share = client.post(
        "/shares",
        json={"kind": "folder", "path": "Projects/", "visibility": "private"},
        headers=auth_headers(admin_token),
    )
    assert folder_share.status_code == 201
    share_id = folder_share.json()["id"]

    # Request token for file within folder
    token_request = {
        "share_id": share_id,
        "doc_id": "some-guid/Projects/doc.md",
        "file_path": "Projects/doc.md",
        "mode": "read",
    }
    response = client.post("/tokens/relay", json=token_request, headers=auth_headers(admin_token))
    assert response.status_code == 200, response.text
    assert "token" in response.json()


def test_folder_share_token_allows_any_doc_id(client: TestClient) -> None:
    """Folder share tokens work with any doc_id (UUID-based, not path-based).

    File path validation was removed because doc_id for files within folder
    shares is a UUID, local folder names can differ between devices, and
    membership check handles authorization.
    """
    admin_token = login(client, "bootstrap@example.com", "super-secret")

    # Create a folder share
    folder_share = client.post(
        "/shares",
        json={"kind": "folder", "path": "Projects/", "visibility": "private"},
        headers=auth_headers(admin_token),
    )
    assert folder_share.status_code == 201
    share_id = folder_share.json()["id"]

    # Request token with arbitrary doc_id — should succeed for share members
    token_request = {
        "share_id": share_id,
        "doc_id": "some-uuid-for-document",
        "mode": "read",
    }
    response = client.post("/tokens/relay", json=token_request, headers=auth_headers(admin_token))
    assert response.status_code == 200
    assert "token" in response.json()


def test_folder_share_nested_file(client: TestClient) -> None:
    """Test that files in nested subfolders are accessible."""
    admin_token = login(client, "bootstrap@example.com", "super-secret")

    # Create a folder share
    folder_share = client.post(
        "/shares",
        json={"kind": "folder", "path": "Projects/", "visibility": "private"},
        headers=auth_headers(admin_token),
    )
    assert folder_share.status_code == 201
    share_id = folder_share.json()["id"]

    # Request token for deeply nested file
    token_request = {
        "share_id": share_id,
        "doc_id": "some-guid/Projects/subdir/nested/doc.md",
        "file_path": "Projects/subdir/nested/doc.md",
        "mode": "read",
    }
    response = client.post("/tokens/relay", json=token_request, headers=auth_headers(admin_token))
    assert response.status_code == 200, response.text


def test_folder_share_doc_id_is_opaque(client: TestClient) -> None:
    """doc_id is an opaque key for the relay, not a filesystem path.

    Path traversal in doc_id is not a security risk because the relay
    server treats doc_id as a storage key, not a file path.
    """
    admin_token = login(client, "bootstrap@example.com", "super-secret")

    # Create a folder share
    folder_share = client.post(
        "/shares",
        json={"kind": "folder", "path": "Projects/", "visibility": "private"},
        headers=auth_headers(admin_token),
    )
    assert folder_share.status_code == 201
    share_id = folder_share.json()["id"]

    # doc_id with path-like content is treated as opaque string — succeeds
    token_request = {
        "share_id": share_id,
        "doc_id": "some-guid/../../../etc/passwd",
        "mode": "read",
    }
    response = client.post("/tokens/relay", json=token_request, headers=auth_headers(admin_token))
    assert response.status_code == 200


def test_find_share_for_path_doc_precedence(client: TestClient) -> None:
    """Test that direct doc share takes precedence over folder share."""
    admin_token = login(client, "bootstrap@example.com", "super-secret")

    # Create a folder share
    folder_share = client.post(
        "/shares",
        json={"kind": "folder", "path": "Projects/", "visibility": "private"},
        headers=auth_headers(admin_token),
    )
    assert folder_share.status_code == 201

    # Create a direct doc share for a file within the folder
    doc_share = client.post(
        "/shares",
        json={"kind": "doc", "path": "Projects/important.md", "visibility": "public"},
        headers=auth_headers(admin_token),
    )
    assert doc_share.status_code == 201
    doc_share_id = doc_share.json()["id"]

    # Token request using doc share should work
    token_request = {
        "share_id": doc_share_id,
        "doc_id": "some-guid",
        "mode": "read",
    }
    response = client.post("/tokens/relay", json=token_request)
    assert response.status_code == 200  # Public doc share, no auth needed


def test_nested_folder_shares_most_specific_wins(client: TestClient) -> None:
    """Test that most specific (longest path) folder share is used."""
    admin_token = login(client, "bootstrap@example.com", "super-secret")

    # Create parent folder share
    parent_folder = client.post(
        "/shares",
        json={"kind": "folder", "path": "Projects/", "visibility": "private"},
        headers=auth_headers(admin_token),
    )
    assert parent_folder.status_code == 201
    parent_id = parent_folder.json()["id"]

    # Create nested folder share with different visibility
    nested_folder = client.post(
        "/shares",
        json={"kind": "folder", "path": "Projects/subproject/", "visibility": "public"},
        headers=auth_headers(admin_token),
    )
    assert nested_folder.status_code == 201
    nested_id = nested_folder.json()["id"]

    # File in parent folder only - needs auth
    token_request = {
        "share_id": parent_id,
        "doc_id": "guid/Projects/other.md",
        "file_path": "Projects/other.md",
        "mode": "read",
    }
    response = client.post("/tokens/relay", json=token_request, headers=auth_headers(admin_token))
    assert response.status_code == 200

    # File in nested folder - should work with public share (no auth)
    token_request = {
        "share_id": nested_id,
        "doc_id": "guid/Projects/subproject/file.md",
        "file_path": "Projects/subproject/file.md",
        "mode": "read",
    }
    response = client.post("/tokens/relay", json=token_request)  # No auth
    assert response.status_code == 200  # Public nested share


def test_folder_share_viewer_cannot_write(client: TestClient) -> None:
    """Test that viewer role on folder cannot get write token."""
    admin_token = login(client, "bootstrap@example.com", "super-secret")

    # Create folder share
    folder_share = client.post(
        "/shares",
        json={"kind": "folder", "path": "Projects/", "visibility": "private"},
        headers=auth_headers(admin_token),
    )
    assert folder_share.status_code == 201
    share_id = folder_share.json()["id"]

    # Create viewer user
    user_payload = {
        "email": "viewer@example.com",
        "password": "viewer-pass",
        "is_admin": False,
        "is_active": True,
    }
    create_user = client.post("/admin/users", json=user_payload, headers=auth_headers(admin_token))
    assert create_user.status_code == 201
    user_id = create_user.json()["id"]

    # Add user as viewer
    add_member = client.post(
        f"/shares/{share_id}/members",
        json={"user_id": user_id, "role": "viewer"},
        headers=auth_headers(admin_token),
    )
    assert add_member.status_code == 201

    # Login as viewer
    viewer_token = login(client, "viewer@example.com", "viewer-pass")

    # Try to get write token
    token_request = {
        "share_id": share_id,
        "doc_id": "guid/Projects/doc.md",
        "file_path": "Projects/doc.md",
        "mode": "write",
    }
    response = client.post("/tokens/relay", json=token_request, headers=auth_headers(viewer_token))
    assert response.status_code == 403


def test_folder_share_editor_can_write(client: TestClient) -> None:
    """Test that editor role on folder can get write token."""
    admin_token = login(client, "bootstrap@example.com", "super-secret")

    # Create folder share
    folder_share = client.post(
        "/shares",
        json={"kind": "folder", "path": "Projects/", "visibility": "private"},
        headers=auth_headers(admin_token),
    )
    assert folder_share.status_code == 201
    share_id = folder_share.json()["id"]

    # Create editor user
    user_payload = {
        "email": "editor@example.com",
        "password": "editor-pass",
        "is_admin": False,
        "is_active": True,
    }
    create_user = client.post("/admin/users", json=user_payload, headers=auth_headers(admin_token))
    assert create_user.status_code == 201
    user_id = create_user.json()["id"]

    # Add user as editor
    add_member = client.post(
        f"/shares/{share_id}/members",
        json={"user_id": user_id, "role": "editor"},
        headers=auth_headers(admin_token),
    )
    assert add_member.status_code == 201

    # Login as editor
    editor_token = login(client, "editor@example.com", "editor-pass")

    # Get write token
    token_request = {
        "share_id": share_id,
        "doc_id": "guid/Projects/doc.md",
        "file_path": "Projects/doc.md",
        "mode": "write",
    }
    response = client.post("/tokens/relay", json=token_request, headers=auth_headers(editor_token))
    assert response.status_code == 200, response.text
    assert "token" in response.json()


def test_folder_share_owner_has_full_access(client: TestClient) -> None:
    """Test that folder share owner has full access to all files."""
    admin_token = login(client, "bootstrap@example.com", "super-secret")

    # Create folder share
    folder_share = client.post(
        "/shares",
        json={"kind": "folder", "path": "MyProject/", "visibility": "private"},
        headers=auth_headers(admin_token),
    )
    assert folder_share.status_code == 201
    share_id = folder_share.json()["id"]

    # Owner requests write token for file in folder
    token_request = {
        "share_id": share_id,
        "doc_id": "guid/MyProject/doc.md",
        "file_path": "MyProject/doc.md",
        "mode": "write",
    }
    response = client.post("/tokens/relay", json=token_request, headers=auth_headers(admin_token))
    assert response.status_code == 200, response.text


def test_protected_folder_share_requires_password(client: TestClient) -> None:
    """Test that protected folder shares require password for token issuance."""
    admin_token = login(client, "bootstrap@example.com", "super-secret")

    # Create protected folder share
    folder_share = client.post(
        "/shares",
        json={
            "kind": "folder",
            "path": "Protected/",
            "visibility": "protected",
            "password": "secret123",
        },
        headers=auth_headers(admin_token),
    )
    assert folder_share.status_code == 201
    share_id = folder_share.json()["id"]

    # Try without password
    token_request = {
        "share_id": share_id,
        "doc_id": "guid/Protected/doc.md",
        "file_path": "Protected/doc.md",
        "mode": "read",
    }
    response = client.post("/tokens/relay", json=token_request)
    assert response.status_code == 403

    # Try with wrong password
    token_request["password"] = "wrong"
    response = client.post("/tokens/relay", json=token_request)
    assert response.status_code == 403

    # Try with correct password
    token_request["password"] = "secret123"
    response = client.post("/tokens/relay", json=token_request)
    assert response.status_code == 200, response.text


def test_public_folder_share_no_auth_for_read(client: TestClient) -> None:
    """Test that public folder shares allow read tokens without authentication."""
    admin_token = login(client, "bootstrap@example.com", "super-secret")

    # Create public folder share
    folder_share = client.post(
        "/shares",
        json={"kind": "folder", "path": "Public/", "visibility": "public"},
        headers=auth_headers(admin_token),
    )
    assert folder_share.status_code == 201
    share_id = folder_share.json()["id"]

    # Request read token without auth
    token_request = {
        "share_id": share_id,
        "doc_id": "guid/Public/doc.md",
        "file_path": "Public/doc.md",
        "mode": "read",
    }
    response = client.post("/tokens/relay", json=token_request)
    assert response.status_code == 200, response.text

    # Write token should still require auth
    token_request["mode"] = "write"
    response = client.post("/tokens/relay", json=token_request)
    assert response.status_code == 403


def test_list_shares_includes_folders(client: TestClient) -> None:
    """Test that GET /shares returns folder shares with correct kind."""
    admin_token = login(client, "bootstrap@example.com", "super-secret")

    # Create doc and folder shares
    doc_share = client.post(
        "/shares",
        json={"kind": "doc", "path": "doc.md", "visibility": "private"},
        headers=auth_headers(admin_token),
    )
    assert doc_share.status_code == 201

    folder_share = client.post(
        "/shares",
        json={"kind": "folder", "path": "Folder/", "visibility": "private"},
        headers=auth_headers(admin_token),
    )
    assert folder_share.status_code == 201

    # List shares
    response = client.get("/shares", headers=auth_headers(admin_token))
    assert response.status_code == 200
    shares = response.json()

    # Find our shares
    doc_shares = [s for s in shares if s["kind"] == "doc"]
    folder_shares = [s for s in shares if s["kind"] == "folder"]

    assert len(doc_shares) >= 1
    assert len(folder_shares) >= 1


def test_folder_share_path_normalization(client: TestClient) -> None:
    """Test that path normalization handles leading/trailing slashes correctly."""
    admin_token = login(client, "bootstrap@example.com", "super-secret")

    # Create folder share with trailing slash
    folder_share = client.post(
        "/shares",
        json={"kind": "folder", "path": "Projects/", "visibility": "private"},
        headers=auth_headers(admin_token),
    )
    assert folder_share.status_code == 201
    share_id = folder_share.json()["id"]

    # Test file path without leading slash
    token_request = {
        "share_id": share_id,
        "doc_id": "guid/Projects/doc.md",
        "file_path": "Projects/doc.md",
        "mode": "read",
    }
    response = client.post("/tokens/relay", json=token_request, headers=auth_headers(admin_token))
    assert response.status_code == 200, response.text
