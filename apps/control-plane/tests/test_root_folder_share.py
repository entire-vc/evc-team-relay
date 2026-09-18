"""Tests for empty path = "whole vault" on FOLDER shares (#1f27561a).

Obsidian's vault-root `TFolder.path` is literally the string "/", and that
spelling is (correctly) rejected as an absolute path. Before this fix there
was no path value a client could send to mean "share everything": "" was
rejected as empty, "/" was rejected as absolute. These tests pin the fix at
three levels: the pure path-safety validator, the pure folder-containment
check, and the actual HTTP create-share endpoint.
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.db import models
from app.services import share_service

# --- validate_share_path_safety ---------------------------------------------


def test_empty_path_accepted_for_folder() -> None:
    share_service.validate_share_path_safety("", models.ShareKind.FOLDER)


def test_empty_path_still_rejected_for_doc() -> None:
    with pytest.raises(HTTPException) as exc_info:
        share_service.validate_share_path_safety("", models.ShareKind.DOC)
    assert exc_info.value.status_code == 400


def test_whitespace_only_path_still_rejected_for_folder() -> None:
    """Only the literal "" is the canonical root — "   " is not a valid spelling."""
    with pytest.raises(HTTPException) as exc_info:
        share_service.validate_share_path_safety("   ", models.ShareKind.FOLDER)
    assert exc_info.value.status_code == 400


@pytest.mark.parametrize("absolute_path", ["/", "/Projects", "\\Projects"])
def test_absolute_path_still_rejected_for_folder(absolute_path: str) -> None:
    """The fix narrows the empty-path rejection — it must not touch this guard."""
    with pytest.raises(HTTPException) as exc_info:
        share_service.validate_share_path_safety(absolute_path, models.ShareKind.FOLDER)
    assert exc_info.value.status_code == 400


def test_normal_folder_path_still_accepted() -> None:
    share_service.validate_share_path_safety("Projects/", models.ShareKind.FOLDER)


def test_normal_doc_path_still_accepted() -> None:
    share_service.validate_share_path_safety("Projects/doc.md", models.ShareKind.DOC)


def test_doc_without_valid_extension_still_rejected() -> None:
    with pytest.raises(HTTPException) as exc_info:
        share_service.validate_share_path_safety("Projects/doc.txt", models.ShareKind.DOC)
    assert exc_info.value.status_code == 400


# --- validate_path_within_folder ---------------------------------------------


@pytest.mark.parametrize(
    "file_path",
    ["top-level.md", "Projects/doc.md", "Projects/sub/nested/doc.md"],
)
def test_root_folder_contains_top_level_and_nested_files(file_path: str) -> None:
    assert share_service.validate_path_within_folder("", file_path) is True


def test_root_folder_does_not_contain_empty_file_path() -> None:
    assert share_service.validate_path_within_folder("", "") is False


def test_non_root_folder_behaviour_unchanged() -> None:
    assert share_service.validate_path_within_folder("Projects/", "Projects/doc.md") is True
    assert share_service.validate_path_within_folder("Projects/", "Other/doc.md") is False


# --- find_share_for_path (root vs. a more specific nested folder share) -----


@pytest.fixture
def owner(db_session):
    from app.core import security

    user = models.User(
        email="root-share-owner@example.com",
        password_hash=security.get_password_hash("test123456"),
        is_admin=False,
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    return user


def _make_folder_share(db_session, owner: models.User, path: str) -> models.Share:
    share = models.Share(
        kind=models.ShareKind.FOLDER,
        path=path,
        visibility=models.ShareVisibility.PRIVATE,
        owner_user_id=owner.id,
    )
    db_session.add(share)
    db_session.commit()
    db_session.refresh(share)
    return share


def test_find_share_for_path_root_share_covers_top_level_file(db_session, owner) -> None:
    root_share = _make_folder_share(db_session, owner, "")
    found = share_service.find_share_for_path(db_session, owner, "top-level.md")
    assert found is not None
    assert found.id == root_share.id


def test_find_share_for_path_nested_share_wins_over_root(db_session, owner) -> None:
    """A root share and a more specific folder share coexist; the more
    specific (longest path) one is returned for files it covers — matching
    the existing precedent for two ordinary nested folder shares
    (test_nested_folder_shares_most_specific_wins).
    """
    root_share = _make_folder_share(db_session, owner, "")
    nested_share = _make_folder_share(db_session, owner, "Projects/")

    found_nested = share_service.find_share_for_path(db_session, owner, "Projects/doc.md")
    assert found_nested is not None
    assert found_nested.id == nested_share.id

    found_root = share_service.find_share_for_path(db_session, owner, "Other/doc.md")
    assert found_root is not None
    assert found_root.id == root_share.id


# --- HTTP-level: POST /shares -------------------------------------------------


def _login(client: TestClient, email: str, password: str) -> str:
    response = client.post("/auth/login", json={"email": email, "password": password})
    assert response.status_code == 200, response.text
    return response.json()["access_token"]


def test_post_shares_empty_path_folder_succeeds(client: TestClient) -> None:
    token = _login(client, "bootstrap@example.com", "super-secret")
    response = client.post(
        "/shares",
        json={"path": "", "kind": "folder", "visibility": "private"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 201, response.text
    assert response.json()["path"] == ""
    assert response.json()["kind"] == "folder"


def test_post_shares_empty_path_doc_still_rejected(client: TestClient) -> None:
    token = _login(client, "bootstrap@example.com", "super-secret")
    response = client.post(
        "/shares",
        json={"path": "", "kind": "doc", "visibility": "private"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 400, response.text
