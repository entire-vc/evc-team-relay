"""Regression: only the owner or a platform admin may DELETE a share.

`ensure_owner_or_admin` guards `DELETE /shares/{id}`. Until this module the
only DELETE call in the suite came from an admin, so a regression that let a
plain member (editor or viewer) delete the share would have gone unnoticed.
"""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import models

ADMIN_EMAIL = "bootstrap@example.com"
ADMIN_PASSWORD = "super-secret"


def auth_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def login(client: TestClient, email: str, password: str) -> str:
    response = client.post("/auth/login", json={"email": email, "password": password})
    assert response.status_code == 200, response.text
    return response.json()["access_token"]


def create_plain_user(client: TestClient, admin_token: str, email: str, password: str) -> str:
    response = client.post(
        "/admin/users",
        json={"email": email, "password": password, "is_admin": False, "is_active": True},
        headers=auth_headers(admin_token),
    )
    assert response.status_code == 201, response.text
    assert response.json()["is_admin"] is False
    return response.json()["id"]


def share_in_db(db_session: Session, share_id: str) -> bool:
    db_session.expire_all()
    stmt = select(models.Share.id).where(models.Share.id == uuid.UUID(share_id))
    return db_session.execute(stmt).scalar_one_or_none() is not None


@pytest.fixture
def owned_folder_share(client: TestClient) -> dict[str, str]:
    """A private folder share owned by a non-admin user."""
    admin_token = login(client, ADMIN_EMAIL, ADMIN_PASSWORD)
    create_plain_user(client, admin_token, "owner@example.com", "owner-pass")
    owner_token = login(client, "owner@example.com", "owner-pass")

    created = client.post(
        "/shares",
        json={"kind": "folder", "path": "Team/", "visibility": "private"},
        headers=auth_headers(owner_token),
    )
    assert created.status_code == 201, created.text
    return {
        "share_id": created.json()["id"],
        "admin_token": admin_token,
        "owner_token": owner_token,
    }


@pytest.mark.parametrize("role", ["editor", "viewer"])
def test_member_cannot_delete_share_owner_can(
    client: TestClient,
    db_session: Session,
    owned_folder_share: dict[str, str],
    role: str,
) -> None:
    share_id = owned_folder_share["share_id"]
    admin_token = owned_folder_share["admin_token"]
    owner_token = owned_folder_share["owner_token"]

    member_id = create_plain_user(client, admin_token, f"{role}@example.com", "member-pass")
    added = client.post(
        f"/shares/{share_id}/members",
        json={"user_id": member_id, "role": role},
        headers=auth_headers(owner_token),
    )
    assert added.status_code == 201, added.text
    member_token = login(client, f"{role}@example.com", "member-pass")

    # The member really has access: a 403 below is about the role, not a missing membership.
    member_read = client.get(f"/shares/{share_id}", headers=auth_headers(member_token))
    assert member_read.status_code == 200, member_read.text

    denied = client.delete(f"/shares/{share_id}", headers=auth_headers(member_token))
    assert denied.status_code == 403, denied.text

    still_there = client.get(f"/shares/{share_id}", headers=auth_headers(owner_token))
    assert still_there.status_code == 200, still_there.text
    assert share_in_db(db_session, share_id)

    # Positive control: the owner deletes the same share.
    deleted = client.delete(f"/shares/{share_id}", headers=auth_headers(owner_token))
    assert deleted.status_code == 204, deleted.text

    gone = client.get(f"/shares/{share_id}", headers=auth_headers(owner_token))
    assert gone.status_code == 404, gone.text
    assert not share_in_db(db_session, share_id)


def test_admin_who_is_not_owner_can_delete_share(
    client: TestClient,
    db_session: Session,
    owned_folder_share: dict[str, str],
) -> None:
    """Pins the current owner-or-admin contract for DELETE."""
    share_id = owned_folder_share["share_id"]
    admin_token = owned_folder_share["admin_token"]
    owner_token = owned_folder_share["owner_token"]

    me = client.get("/auth/me", headers=auth_headers(admin_token)).json()
    share = client.get(f"/shares/{share_id}", headers=auth_headers(owner_token)).json()
    assert share["owner_user_id"] != me["id"]

    deleted = client.delete(f"/shares/{share_id}", headers=auth_headers(admin_token))
    assert deleted.status_code == 204, deleted.text

    gone = client.get(f"/shares/{share_id}", headers=auth_headers(owner_token))
    assert gone.status_code == 404, gone.text
    assert not share_in_db(db_session, share_id)
