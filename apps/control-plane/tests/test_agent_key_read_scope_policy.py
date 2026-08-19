"""Unified read-scope policy for agent keys (#b69d73fb, ADR-0001).

The defect: ``_require_private_web_auth`` answered "write implies read" while
``_auth_share_read_access`` demanded the literal ``read`` scope, so one key on
one share could read a document through ``GET /v1/web/shares/{slug}/files`` and
be refused the same bytes through ``GET /v1/web/shares/{id}/download``.

Every test here drives a ROUTE, never a helper directly. A helper-level test
could not have caught this bug: both helpers were individually self-consistent
and correct against their own intent — the defect only existed in the gap
*between* them. So the central assertion is a property across the whole set of
read routes ("they all agree"), evaluated end-to-end through the app.
"""

from __future__ import annotations

import hashlib
import logging
import secrets
import uuid
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core import security
from app.db import models

SECRET_MARKER = "TOP-SECRET-VAULT-BODY"


# --------------------------------------------------------------------------
# fixtures / helpers
# --------------------------------------------------------------------------


def make_user(db: Session, email: str, is_admin: bool = False) -> models.User:
    user = models.User(
        email=email,
        password_hash=security.get_password_hash("test123456"),
        is_admin=is_admin,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def make_folder_share(
    db: Session, owner: models.User, slug: str, published: bool = True
) -> models.Share:
    share = models.Share(
        kind=models.ShareKind.FOLDER,
        path=f"{slug}/",
        visibility=models.ShareVisibility.PRIVATE,
        owner_user_id=owner.id,
        web_published=published,
        web_slug=slug if published else None,
        web_folder_items=[
            {
                "path": "secret.md",
                "name": "secret",
                "type": "doc",
                "content": f"# {SECRET_MARKER}",
            }
        ],
    )
    db.add(share)
    db.commit()
    db.refresh(share)
    return share


def make_agent_key(
    db: Session, share: models.Share, created_by: uuid.UUID, scopes: str
) -> tuple[str, models.ShareAgentKey]:
    raw = "tr_agent_" + secrets.token_hex(24)
    ak = models.ShareAgentKey(
        share_id=share.id,
        key_hash=hashlib.sha256(raw.encode()).hexdigest(),
        label=f"scope-policy-{scopes}",
        scopes=scopes,
        created_by=created_by,
    )
    db.add(ak)
    db.commit()
    db.refresh(ak)
    return raw, ak


def minio_empty():
    """An asset store containing nothing, so /assets can answer 404 rather than
    exploding — the auth decision has already been made by that point."""
    from minio.error import S3Error

    m = MagicMock()
    m.bucket_exists.return_value = True
    m.get_object.side_effect = S3Error("NoSuchKey", "missing", "res", "req", "host", None)
    return patch("app.api.routers.web._get_minio_client", return_value=m)


@pytest.fixture
def web_enabled(monkeypatch):
    monkeypatch.setenv("WEB_PUBLISH_DOMAIN", "docs.test.com")
    from app.core.config import get_settings

    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def grace_off(monkeypatch, web_enabled):
    """Phase 2: the literal policy enforced on every read route."""
    monkeypatch.setenv("AGENT_KEY_LENIENT_READ_GRACE", "false")
    from app.core.config import get_settings

    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def read_routes(share: models.Share) -> dict[str, str]:
    """Every route that serves share CONTENT to an agent key.

    Keyed by name so a failure says which route disagreed. `/assets` is included
    because it is gated by the same helper even though its body is a binary.
    """
    sid = str(share.id)
    slug = share.web_slug
    return {
        "metadata": f"/v1/web/shares/{slug}",
        "files": f"/v1/web/shares/{slug}/files?path=secret.md",
        "assets": f"/v1/web/shares/{slug}/assets?path=image.png",
        "files-index": f"/v1/web/shares/{sid}/files-index",
        "download": f"/v1/web/shares/{sid}/download?path=secret.md",
    }


def call(client: TestClient, url: str, raw_key: str):
    with minio_empty():
        return client.get(url, headers={"X-Agent-Key": raw_key})


def denies_read(resp) -> bool:
    """Did this route refuse on scope grounds?

    401/403 both count as refusal. 404 does NOT: on /assets it means "authorised,
    object absent", which is a successful read decision.
    """
    return resp.status_code in (401, 403)


# --------------------------------------------------------------------------
# the property that was broken: all read routes must agree
# --------------------------------------------------------------------------


class TestAllReadRoutesAgree:
    @pytest.mark.parametrize("scopes", ["write", "read", "read,write"])
    def test_every_read_route_gives_the_same_verdict(self, db_session, client, grace_off, scopes):
        """The regression test for #b69d73fb, stated as a property.

        Under the unified policy a key either may read this share's content or
        may not, and every content route must say so identically. Before the
        fix this failed for scopes="write": metadata/files/assets served the
        document while files-index/download answered 403.
        """
        owner = make_user(db_session, f"o-{uuid.uuid4().hex[:8]}@x.com")
        share = make_folder_share(db_session, owner, f"s{uuid.uuid4().hex[:8]}")
        raw, _ = make_agent_key(db_session, share, owner.id, scopes)

        verdicts = {
            name: denies_read(call(client, url, raw)) for name, url in read_routes(share).items()
        }
        assert len(set(verdicts.values())) == 1, (
            f"read routes disagree for scopes={scopes!r}: "
            f"{ {k: ('DENY' if v else 'ALLOW') for k, v in verdicts.items()} }"
        )

    def test_write_only_key_is_denied_on_every_read_route(self, db_session, client, grace_off):
        """Direction of the unified policy: literal, so write does not imply read."""
        owner = make_user(db_session, f"o-{uuid.uuid4().hex[:8]}@x.com")
        share = make_folder_share(db_session, owner, f"s{uuid.uuid4().hex[:8]}")
        raw, _ = make_agent_key(db_session, share, owner.id, "write")

        for name, url in read_routes(share).items():
            resp = call(client, url, raw)
            assert denies_read(resp), f"{name} let a write-only key read: {resp.status_code}"

    def test_read_key_is_allowed_on_every_read_route_and_content_arrives(
        self, db_session, client, grace_off
    ):
        """Negative control for the test above.

        Without this, "everything is denied" would pass the previous test for a
        trivially broken reason — e.g. if auth were rejecting every key.
        """
        owner = make_user(db_session, f"o-{uuid.uuid4().hex[:8]}@x.com")
        share = make_folder_share(db_session, owner, f"s{uuid.uuid4().hex[:8]}")
        raw, _ = make_agent_key(db_session, share, owner.id, "read")

        for name, url in read_routes(share).items():
            resp = call(client, url, raw)
            assert not denies_read(resp), f"{name} denied a read key: {resp.status_code}"

        # And the bytes really are served, not just a 200 shell.
        body = call(client, read_routes(share)["download"], raw).text
        assert SECRET_MARKER in body


# --------------------------------------------------------------------------
# grace window (phase 1): behaviour preserved, population measured
# --------------------------------------------------------------------------


class TestLenientReadGrace:
    def test_grace_preserves_the_old_lenient_behaviour(self, db_session, client, web_enabled):
        """With grace on (the default), a write-only key still reads the lenient
        routes exactly as it did before this change. That is the whole point:
        phase 1 must not change a single verdict."""
        owner = make_user(db_session, f"o-{uuid.uuid4().hex[:8]}@x.com")
        share = make_folder_share(db_session, owner, f"s{uuid.uuid4().hex[:8]}")
        raw, _ = make_agent_key(db_session, share, owner.id, "write")

        routes = read_routes(share)
        assert not denies_read(call(client, routes["metadata"], raw))
        assert not denies_read(call(client, routes["files"], raw))

    def test_grace_does_NOT_widen_the_strict_routes(self, db_session, client, web_enabled):
        """Grace exists to hold the lenient routes still, never to loosen the
        strict ones.

        This is the guard on the dangerous direction: /download and /files-index
        are UUID-addressable and so reach never-published shares, where a
        write-only key can read nothing today. If grace leaked into them, five
        external customers' write-only keys would gain read access to unpublished
        private vault content.
        """
        owner = make_user(db_session, f"o-{uuid.uuid4().hex[:8]}@x.com")
        share = make_folder_share(db_session, owner, f"s{uuid.uuid4().hex[:8]}")
        raw, _ = make_agent_key(db_session, share, owner.id, "write")

        routes = read_routes(share)
        assert denies_read(call(client, routes["download"], raw))
        assert denies_read(call(client, routes["files-index"], raw))

    def test_grace_use_is_logged_with_the_key_and_route(
        self, db_session, client, web_enabled, caplog
    ):
        """The migration signal. Without it, "which keys still need read?" is
        unanswerable — which is precisely how this defect stayed invisible."""
        owner = make_user(db_session, f"o-{uuid.uuid4().hex[:8]}@x.com")
        share = make_folder_share(db_session, owner, f"s{uuid.uuid4().hex[:8]}")
        raw, ak = make_agent_key(db_session, share, owner.id, "write")

        with caplog.at_level(logging.WARNING, logger="app.core.agent_key_scopes"):
            call(client, read_routes(share)["files"], raw)

        records = [
            r for r in caplog.records if getattr(r, "event", "") == "agent_key_read_scope_grace"
        ]
        assert records, "grace was used but nothing was logged"
        assert records[0].agent_key_id == str(ak.id)
        assert records[0].route.endswith("/files")

    def test_no_grace_log_for_a_key_that_genuinely_has_read(
        self, db_session, client, web_enabled, caplog
    ):
        """Anti-goal: the signal must name only keys that actually need migrating.
        A warning on every read would be noise and would hide the real ones."""
        owner = make_user(db_session, f"o-{uuid.uuid4().hex[:8]}@x.com")
        share = make_folder_share(db_session, owner, f"s{uuid.uuid4().hex[:8]}")
        raw, _ = make_agent_key(db_session, share, owner.id, "read,write")

        with caplog.at_level(logging.WARNING, logger="app.core.agent_key_scopes"):
            call(client, read_routes(share)["files"], raw)

        assert not [
            r for r in caplog.records if getattr(r, "event", "") == "agent_key_read_scope_grace"
        ]


# --------------------------------------------------------------------------
# unpublished shares — the case that chose the direction
# --------------------------------------------------------------------------


class TestUnpublishedShareIsUnreachableByTheLenientRoutes:
    def test_lenient_routes_cannot_reach_an_unpublished_share_at_all(
        self, db_session, client, web_enabled
    ):
        """Recorded because it is the evidence behind ADR-0001's direction.

        The lenient routes resolve a share by (web_slug, web_published==True), so
        on an unpublished share they 404 by construction regardless of scope.
        Only the strict routes reach it — which is why unifying on the LENIENT
        policy would have granted new read access here, and unifying on the
        literal one grants none.
        """
        owner = make_user(db_session, f"o-{uuid.uuid4().hex[:8]}@x.com")
        share = make_folder_share(db_session, owner, "unpub", published=False)
        raw, _ = make_agent_key(db_session, share, owner.id, "read")

        for url in (
            "/v1/web/shares/unpub",
            "/v1/web/shares/unpub/files?path=secret.md",
        ):
            assert call(client, url, raw).status_code == 404

        # ...while the strict route serves it to a read-scoped key.
        strict = f"/v1/web/shares/{share.id}/download?path=secret.md"
        assert SECRET_MARKER in call(client, strict, raw).text


# --------------------------------------------------------------------------
# usage recording — the blind spot that made the population unmeasurable
# --------------------------------------------------------------------------


class TestLastUsedAtCoversLenientRoutes:
    @pytest.mark.parametrize("route_name", ["metadata", "files", "download", "files-index"])
    def test_every_authenticated_read_route_records_usage(
        self, db_session, client, web_enabled, route_name
    ):
        """Before this change only /download and /files-index stamped
        last_used_at, so a key reading through /files looked unused forever —
        and "is anyone reading with a write-only key?" could not be answered
        from the data at all."""
        owner = make_user(db_session, f"o-{uuid.uuid4().hex[:8]}@x.com")
        share = make_folder_share(db_session, owner, f"s{uuid.uuid4().hex[:8]}")
        raw, ak = make_agent_key(db_session, share, owner.id, "read,write")
        assert ak.last_used_at is None

        call(client, read_routes(share)[route_name], raw)

        db_session.refresh(ak)
        assert ak.last_used_at is not None, f"{route_name} did not record key usage"


# --------------------------------------------------------------------------
# migration tooling: PATCH scopes
# --------------------------------------------------------------------------


def login(client: TestClient, email: str) -> str:
    resp = client.post("/auth/login", json={"email": email, "password": "test123456"})
    assert resp.status_code == 200, resp.text
    return resp.json()["access_token"]


class TestPatchAgentKeyScopes:
    def test_owner_can_grant_read_without_rotating_the_secret(self, db_session, client, grace_off):
        """The migration path in one call: the write-only key that could not read
        can read afterwards, and the integration holding the raw secret never
        has to be reconfigured."""
        owner = make_user(db_session, f"o-{uuid.uuid4().hex[:8]}@x.com")
        share = make_folder_share(db_session, owner, f"s{uuid.uuid4().hex[:8]}")
        raw, ak = make_agent_key(db_session, share, owner.id, "write")
        download = read_routes(share)["download"]

        assert denies_read(call(client, download, raw))

        token = login(client, owner.email)
        resp = client.patch(
            f"/v1/web/shares/{share.id}/agent-keys/{ak.id}",
            json={"scopes": ["read", "write"]},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200, resp.text
        assert sorted(resp.json()["scopes"]) == ["read", "write"]

        # Same raw key, now admitted, and the content really arrives.
        assert SECRET_MARKER in call(client, download, raw).text

    def test_non_owner_cannot_widen_a_key(self, db_session, client, web_enabled):
        """Scopes are the authorization surface — widening them needs the same
        standing as minting a key."""
        owner = make_user(db_session, f"o-{uuid.uuid4().hex[:8]}@x.com")
        outsider = make_user(db_session, f"x-{uuid.uuid4().hex[:8]}@x.com")
        share = make_folder_share(db_session, owner, f"s{uuid.uuid4().hex[:8]}")
        _, ak = make_agent_key(db_session, share, owner.id, "write")

        token = login(client, outsider.email)
        resp = client.patch(
            f"/v1/web/shares/{share.id}/agent-keys/{ak.id}",
            json={"scopes": ["read", "write"]},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 403

    def test_revoked_key_cannot_be_edited_back_into_service(self, db_session, client, web_enabled):
        """Granting scopes must not be a way to quietly undo a revocation."""
        owner = make_user(db_session, f"o-{uuid.uuid4().hex[:8]}@x.com")
        share = make_folder_share(db_session, owner, f"s{uuid.uuid4().hex[:8]}")
        _, ak = make_agent_key(db_session, share, owner.id, "write")
        ak.revoked_at = security.utcnow()
        db_session.commit()

        token = login(client, owner.email)
        resp = client.patch(
            f"/v1/web/shares/{share.id}/agent-keys/{ak.id}",
            json={"scopes": ["read", "write"]},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 409

    def test_invalid_scope_is_rejected(self, db_session, client, web_enabled):
        owner = make_user(db_session, f"o-{uuid.uuid4().hex[:8]}@x.com")
        share = make_folder_share(db_session, owner, f"s{uuid.uuid4().hex[:8]}")
        _, ak = make_agent_key(db_session, share, owner.id, "write")

        token = login(client, owner.email)
        resp = client.patch(
            f"/v1/web/shares/{share.id}/agent-keys/{ak.id}",
            json={"scopes": ["admin"]},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 422


class TestCreateDefaultScopes:
    def test_key_created_without_scopes_can_read(self, db_session, client, grace_off):
        """The plugin — the only key-creation UI — sends no `scopes` field, so
        the server default is what real users get. Under the old write-only
        default their brand-new key was refused by the very endpoint the MCP
        server reads with. Asserted through a route, not by inspecting the
        default: what matters is that the key works."""
        owner = make_user(db_session, f"o-{uuid.uuid4().hex[:8]}@x.com")
        share = make_folder_share(db_session, owner, f"s{uuid.uuid4().hex[:8]}")

        token = login(client, owner.email)
        created = client.post(
            f"/v1/web/shares/{share.id}/agent-keys",
            json={"label": "plugin-style-no-scopes"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert created.status_code == 201, created.text
        raw = created.json()["key"]

        assert SECRET_MARKER in call(client, read_routes(share)["download"], raw).text

    def test_explicit_write_only_still_produces_a_drop_box(self, db_session, client, grace_off):
        """The restriction remains available — it just has to be asked for."""
        owner = make_user(db_session, f"o-{uuid.uuid4().hex[:8]}@x.com")
        share = make_folder_share(db_session, owner, f"s{uuid.uuid4().hex[:8]}")

        token = login(client, owner.email)
        created = client.post(
            f"/v1/web/shares/{share.id}/agent-keys",
            json={"label": "drop-box", "scopes": ["write"]},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert created.status_code == 201, created.text
        raw = created.json()["key"]

        for name, url in read_routes(share).items():
            assert denies_read(call(client, url, raw)), f"{name} leaked to a drop-box key"
