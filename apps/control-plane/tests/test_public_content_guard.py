"""Tests for the public-content guard (TR-39).

A share that is web_published + visibility=public with no real content
renders a live public page (doc) or a folder tree of "Content not
available" placeholders (folder) — confirmed live in prod for the shares
`repo`, `goooooool`, `testforberd`. share_service.create_share/update_share
must reject any change that would leave a share in that state.
"""

from __future__ import annotations

import uuid

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.db import models


def auth_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def login(client: TestClient, email: str, password: str) -> str:
    response = client.post("/auth/login", json={"email": email, "password": password})
    assert response.status_code == 200, response.text
    return response.json()["access_token"]


class TestCreateSharePublicContentGuard:
    def test_create_doc_share_public_and_published_rejected(self, client: TestClient) -> None:
        token = login(client, "bootstrap@example.com", "super-secret")
        response = client.post(
            "/shares",
            json={
                "kind": "doc",
                "path": "vault/empty-public.md",
                "visibility": "public",
                "web_published": True,
            },
            headers=auth_headers(token),
        )
        assert response.status_code == 400, response.text
        assert "content" in response.json()["error"]["message"].lower()

    def test_create_folder_share_public_and_published_rejected(self, client: TestClient) -> None:
        token = login(client, "bootstrap@example.com", "super-secret")
        response = client.post(
            "/shares",
            json={
                "kind": "folder",
                "path": "vault/empty-public-folder",
                "visibility": "public",
                "web_published": True,
            },
            headers=auth_headers(token),
        )
        assert response.status_code == 400, response.text

    def test_create_share_public_but_not_published_is_allowed(self, client: TestClient) -> None:
        """visibility=public alone isn't live on the public site — only
        web_published makes it reachable (see web.py's web_published==True
        filter on every public route) — so this combination is harmless."""
        token = login(client, "bootstrap@example.com", "super-secret")
        response = client.post(
            "/shares",
            json={
                "kind": "doc",
                "path": "vault/public-not-yet-published.md",
                "visibility": "public",
                "web_published": False,
            },
            headers=auth_headers(token),
        )
        assert response.status_code == 201, response.text

    def test_create_share_published_but_private_is_allowed(self, client: TestClient) -> None:
        """No regression: private+published (the normal draft-publish flow)
        still works even with no content yet."""
        token = login(client, "bootstrap@example.com", "super-secret")
        response = client.post(
            "/shares",
            json={
                "kind": "doc",
                "path": "vault/private-published.md",
                "visibility": "private",
                "web_published": True,
            },
            headers=auth_headers(token),
        )
        assert response.status_code == 201, response.text


class TestUpdateSharePublicContentGuard:
    def _create_private_published_doc_share(self, client: TestClient, token: str, path: str) -> str:
        response = client.post(
            "/shares",
            json={"kind": "doc", "path": path, "visibility": "private", "web_published": True},
            headers=auth_headers(token),
        )
        assert response.status_code == 201, response.text
        return response.json()["id"]

    def test_flip_to_public_with_no_content_rejected(self, client: TestClient) -> None:
        token = login(client, "bootstrap@example.com", "super-secret")
        share_id = self._create_private_published_doc_share(client, token, "vault/flip-empty.md")

        response = client.patch(
            f"/shares/{share_id}",
            json={"visibility": "public"},
            headers=auth_headers(token),
        )
        assert response.status_code == 400, response.text
        assert "content" in response.json()["error"]["message"].lower()

        # Rejected update must not have partially applied — visibility stays private.
        get_response = client.get(f"/shares/{share_id}", headers=auth_headers(token))
        assert get_response.json()["visibility"] == "private"

    def test_flip_to_public_with_content_in_same_call_allowed(self, client: TestClient) -> None:
        """The guard checks the RESULTING state of the whole update, so
        setting content and visibility=public in the same PATCH is fine."""
        token = login(client, "bootstrap@example.com", "super-secret")
        share_id = self._create_private_published_doc_share(
            client, token, "vault/flip-with-content.md"
        )

        response = client.patch(
            f"/shares/{share_id}",
            json={"visibility": "public", "web_content": "# Hello\n\nReal content."},
            headers=auth_headers(token),
        )
        assert response.status_code == 200, response.text
        assert response.json()["visibility"] == "public"

    def test_flip_to_public_after_content_already_set_allowed(self, client: TestClient) -> None:
        token = login(client, "bootstrap@example.com", "super-secret")
        share_id = self._create_private_published_doc_share(
            client, token, "vault/content-then-flip.md"
        )

        content_response = client.patch(
            f"/shares/{share_id}",
            json={"web_content": "# Hello\n\nReal content."},
            headers=auth_headers(token),
        )
        assert content_response.status_code == 200, content_response.text

        response = client.patch(
            f"/shares/{share_id}",
            json={"visibility": "public"},
            headers=auth_headers(token),
        )
        assert response.status_code == 200, response.text

    def test_clearing_content_on_already_public_share_rejected(self, client: TestClient) -> None:
        """Guard applies symmetrically: an already-public+published share
        can't have its content wiped out either — same bug, different order
        of operations (this is exactly how `repo`/`goooooool`/`testforberd`
        could recur even after a one-time cleanup)."""
        token = login(client, "bootstrap@example.com", "super-secret")
        share_id = self._create_private_published_doc_share(client, token, "vault/clear-content.md")

        setup = client.patch(
            f"/shares/{share_id}",
            json={"visibility": "public", "web_content": "# Hello\n\nReal content."},
            headers=auth_headers(token),
        )
        assert setup.status_code == 200, setup.text

        response = client.patch(
            f"/shares/{share_id}",
            json={"web_content": ""},
            headers=auth_headers(token),
        )
        assert response.status_code == 400, response.text

    def test_unrelated_update_on_pre_existing_empty_public_share_also_rejected(
        self, client: TestClient, db_session: Session
    ) -> None:
        """The guard checks the RESULT of every update against the invariant
        (public+published ⇒ has content), not just transitions caused by this
        call — so it also catches an unrelated field edit (e.g. toggling
        noindex) on a share that reached the broken state before this guard
        existed (like `repo`/`goooooool`/`testforberd` pre-cleanup). This is
        intentional: it surfaces the invariant violation on the FIRST touch
        of any such row, rather than only at the moment something makes it
        worse. The actual fix for these rows is to unpublish/delete them
        (see the next test) or give them real content — not edit around them."""
        token = login(client, "bootstrap@example.com", "super-secret")

        # Simulate a pre-existing empty public share (bypassing the new create
        # guard, same as the prod rows this finding is about) directly via the DB.
        owner = db_session.query(models.User).filter_by(email="bootstrap@example.com").one()
        share = models.Share(
            kind=models.ShareKind.DOC,
            path="vault/pre-existing-empty-public.md",
            visibility=models.ShareVisibility.PUBLIC,
            owner_user_id=owner.id,
            web_published=True,
            web_slug="pre-existing-empty-public",
        )
        db_session.add(share)
        db_session.commit()
        db_session.refresh(share)

        response = client.patch(
            f"/shares/{share.id}",
            json={"web_noindex": False},
            headers=auth_headers(token),
        )
        assert response.status_code == 400, response.text

    def test_unpublishing_a_pre_existing_empty_public_share_is_the_escape_hatch(
        self, client: TestClient, db_session: Session
    ) -> None:
        """The remediation path for an already-broken row (unpublish, or drop
        to private/protected) must still work through the same guard —
        confirms the guard doesn't trap a broken share in a state nothing
        can fix via the API."""
        token = login(client, "bootstrap@example.com", "super-secret")

        owner = db_session.query(models.User).filter_by(email="bootstrap@example.com").one()
        share = models.Share(
            kind=models.ShareKind.DOC,
            path="vault/pre-existing-empty-public-2.md",
            visibility=models.ShareVisibility.PUBLIC,
            owner_user_id=owner.id,
            web_published=True,
            web_slug="pre-existing-empty-public-2",
        )
        db_session.add(share)
        db_session.commit()
        db_session.refresh(share)

        response = client.patch(
            f"/shares/{share.id}",
            json={"web_published": False},
            headers=auth_headers(token),
        )
        assert response.status_code == 200, response.text
        assert response.json()["web_published"] is False

    def test_folder_share_flip_to_public_without_synced_content_rejected(
        self, client: TestClient
    ) -> None:
        token = login(client, "bootstrap@example.com", "super-secret")
        create_response = client.post(
            "/shares",
            json={
                "kind": "folder",
                "path": "vault/empty-folder",
                "visibility": "private",
                "web_published": True,
            },
            headers=auth_headers(token),
        )
        assert create_response.status_code == 201, create_response.text
        share_id = create_response.json()["id"]

        # Folder navigation entries with no content/storage_key — matches
        # `repo`'s 89-doc tree where every page is a placeholder.
        response = client.patch(
            f"/shares/{share_id}",
            json={
                "visibility": "public",
                "web_folder_items": [{"path": "a.md", "name": "a.md", "type": "doc"}],
            },
            headers=auth_headers(token),
        )
        assert response.status_code == 400, response.text

    def test_folder_share_flip_to_public_with_synced_content_allowed(
        self, client: TestClient, db_session: Session
    ) -> None:
        """A folder item's `content`/`storage_key` is populated by the sync
        pipeline (plugin/agent upload), never by this generic ShareUpdate
        payload — seed it directly on the row to simulate a folder that's
        actually been synced, then confirm the guard lets it publish."""
        token = login(client, "bootstrap@example.com", "super-secret")
        create_response = client.post(
            "/shares",
            json={
                "kind": "folder",
                "path": "vault/synced-folder",
                "visibility": "private",
                "web_published": True,
            },
            headers=auth_headers(token),
        )
        assert create_response.status_code == 201, create_response.text
        share_id = create_response.json()["id"]

        share = db_session.get(models.Share, uuid.UUID(share_id))
        share.web_folder_items = [
            {"path": "a.md", "name": "a.md", "type": "doc", "content": "# Hello"}
        ]
        db_session.add(share)
        db_session.commit()

        response = client.patch(
            f"/shares/{share_id}",
            json={"visibility": "public"},
            headers=auth_headers(token),
        )
        assert response.status_code == 200, response.text
