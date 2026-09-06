"""Tests for the billing integration (enterprise edition, TR·edition/billing #f75f04bb).

Real DB (SQLite in-memory via the shared `client`/`db_session` fixtures) and real
billing_service/billing_stub code paths throughout — per this repo's mocking
convention, the only TRUE external boundary is the Billing Service HTTP API
(app.clients.billing_client.BillingClient), which is mocked only in the
non-stub-mode tests that specifically exercise that boundary. Everything else
(DB, router, webhook signature/dedup, entitlements cache) runs for real.

shares.py limit/visibility enforcement (check_limit/check_visibility called from
create_share/update_share/add_member) is covered here via route-level requests,
not by calling the helpers directly — see TestShareLimitEnforcementViaRoute and
TestMemberLimitEnforcementViaRoute (#f08f0c25).
"""

from __future__ import annotations

import hashlib
import hmac
import os
import time
import uuid
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.services import billing_service, billing_stub, usage_service


def auth_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def login(client: TestClient, email: str, password: str) -> str:
    response = client.post("/auth/login", json={"email": email, "password": password})
    assert response.status_code == 200, response.text
    return response.json()["access_token"]


def register_and_login(client: TestClient, email: str, password: str = "test-pass-123") -> str:
    admin_token = login(client, "bootstrap@example.com", "super-secret")
    resp = client.post(
        "/admin/users",
        json={"email": email, "password": password, "is_admin": False, "is_active": True},
        headers=auth_headers(admin_token),
    )
    assert resp.status_code in (200, 201), resp.text
    return login(client, email, password)


@pytest.fixture(autouse=True)
def _billing_enabled_stub_mode():
    """Default test env for billing endpoints: enabled + stub mode (the safe default)."""
    os.environ["BILLING_ENABLED"] = "true"
    os.environ["BILLING_STUB_MODE"] = "true"
    get_settings.cache_clear()
    billing_service._entitlements_cache.clear()
    yield
    for key in ("BILLING_ENABLED", "BILLING_STUB_MODE", "BILLING_SERVICE_TOKEN"):
        os.environ.pop(key, None)
    get_settings.cache_clear()
    billing_service._entitlements_cache.clear()


# ---------------------------------------------------------------------------
# billing_stub — pure unit tests, no DB
# ---------------------------------------------------------------------------


class TestBillingStub:
    @pytest.mark.asyncio
    async def test_get_stub_entitlements_returns_free_plan(self):
        data = await billing_stub.get_stub_entitlements("any-casdoor-id")
        assert data["plan"] == "Relay Free"
        assert data["subscription"] is None
        assert data["entitlements"]["max_shares"] == {"limit": 3}
        assert data["entitlements"]["allowed_web_visibility"] == {"allowed": ["public"]}

    @pytest.mark.asyncio
    async def test_get_stub_plans_returns_both_plans(self):
        plans = await billing_stub.get_stub_plans()
        ids = {p["id"] for p in plans}
        assert ids == {"prod_relay_free", "prod_relay_builder"}

    @pytest.mark.asyncio
    async def test_get_stub_plans_shape_matches_real_products_contract(self):
        """Regression for #b4a7e703: the plugin's AvailablePlan type is
        written against the real Billing Service's GET /products shape
        (evc-billing services/billing/app/api/products.py), and has no
        way to detect it's actually talking to the stub. Every field the
        client reads must be present under the SAME name and shape here,
        or plan.id ends up undefined and every plan card matches
        currentPlanId (also undefined) at once.
        """
        plans = await billing_stub.get_stub_plans()
        assert len(plans) == 2
        for p in plans:
            assert isinstance(p["id"], str) and p["id"]
            assert p["service_id"] == "relay"
            assert isinstance(p["prices"], list) and len(p["prices"]) == 1
            price = p["prices"][0]
            assert isinstance(price["amount"], int)
            assert price["billing_period"] == "month"
            assert isinstance(p["entitlements"]["max_shares"], dict)
            assert "limit" in p["entitlements"]["max_shares"]
            assert isinstance(p["metadata"], dict)

        free = next(p for p in plans if p["id"] == "prod_relay_free")
        assert free["prices"][0]["amount"] == 0
        builder = next(p for p in plans if p["id"] == "prod_relay_builder")
        assert builder["prices"][0]["amount"] == 900

    @pytest.mark.asyncio
    async def test_create_stub_checkout_not_available(self):
        result = await billing_stub.create_stub_checkout(
            "cid", {"product_id": "prod_relay_builder"}
        )
        assert result["subscription_id"] is None
        assert "stub" in result["message"].lower()


# ---------------------------------------------------------------------------
# billing_service — limit/visibility checks, grace period (stub mode, real logic)
# ---------------------------------------------------------------------------


class TestLimitChecking:
    @pytest.mark.asyncio
    async def test_check_limit_passes_under_max(self):
        await billing_service.check_limit("cid-under", "max_shares", current_count=2)

    @pytest.mark.asyncio
    async def test_check_limit_raises_at_max(self):
        with pytest.raises(billing_service.LimitExceededError) as exc_info:
            await billing_service.check_limit("cid-at-max", "max_shares", current_count=3)
        assert exc_info.value.limit == "max_shares"
        assert exc_info.value.max_value == 3
        assert exc_info.value.plan == "Relay Free"

    @pytest.mark.asyncio
    async def test_check_limit_unlimited_key_never_raises(self):
        # Unknown entitlement key -> _get_limit returns None -> unlimited
        await billing_service.check_limit("cid-x", "nonexistent_key", current_count=999_999)


class TestVisibilityChecking:
    @pytest.mark.asyncio
    async def test_check_visibility_allows_public(self):
        await billing_service.check_visibility("cid", "public")

    @pytest.mark.asyncio
    async def test_check_visibility_rejects_private_on_free_plan(self):
        with pytest.raises(billing_service.VisibilityNotAllowedError) as exc_info:
            await billing_service.check_visibility("cid", "private")
        assert exc_info.value.visibility == "private"
        assert exc_info.value.allowed == ["public"]


class TestEntitlementsCache:
    @pytest.mark.asyncio
    async def test_cache_hit_returns_same_object_within_ttl(self):
        first = await billing_service.get_entitlements_cached("cid-cache")
        second = await billing_service.get_entitlements_cached("cid-cache")
        assert first is second  # served from cache, not recomputed

    @pytest.mark.asyncio
    async def test_invalidate_cache_forces_refetch(self):
        first = await billing_service.get_entitlements_cached("cid-inval")
        billing_service.invalidate_cache("cid-inval")
        second = await billing_service.get_entitlements_cached("cid-inval")
        assert first is not second


class TestGracePeriod:
    def test_no_subscription_not_in_grace(self):
        assert billing_service.is_in_grace_period(None) is False

    def test_active_subscription_not_in_grace(self):
        assert billing_service.is_in_grace_period({"status": "active"}) is False

    def test_cancelled_within_grace_window(self):
        import datetime

        now = datetime.datetime.now(datetime.timezone.utc)
        period_end = (now - datetime.timedelta(days=1)).isoformat()
        sub = {"status": "cancelled", "current_period_end": period_end}
        assert billing_service.is_in_grace_period(sub) is True

    def test_cancelled_past_grace_window(self):
        import datetime

        period_end = (
            datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=30)
        ).isoformat()
        sub = {"status": "cancelled", "current_period_end": period_end}
        assert billing_service.is_in_grace_period(sub) is False


# ---------------------------------------------------------------------------
# check_limit's own grace-period branch (#f08f0c25 review gap) — TestGracePeriod
# above only covers is_in_grace_period() in isolation; nothing exercised the
# fallback-to-Free branch check_limit() itself takes once grace has expired,
# or confirmed it does NOT downgrade while still inside the grace window.
# ---------------------------------------------------------------------------


class TestCheckLimitGracePeriodFallback:
    """The grace-expired -> Free-plan downgrade inside check_limit is itself
    gated by `if not settings.billing_stub_mode`. Every other test in this
    file (including the new shares.py route tests above) runs with
    BILLING_STUB_MODE=true (the autouse fixture's default), which means that
    guard is False and the downgrade branch NEVER executes there — a
    naive grace-period test written under the file's default env would
    silently pass without ever reaching the fallback it claims to test. Real
    subscriptions only ever come from the actual Billing Service, i.e.
    non-stub mode, so these tests flip BILLING_STUB_MODE off to actually
    exercise the branch (mirrors TestNonStubModeMocksExternalBillingClient's
    pattern below)."""

    @pytest.fixture(autouse=True)
    def _non_stub_for_grace_fallback(self):
        os.environ["BILLING_STUB_MODE"] = "false"
        get_settings.cache_clear()
        yield
        os.environ["BILLING_STUB_MODE"] = "true"
        get_settings.cache_clear()

    @pytest.mark.asyncio
    async def test_check_limit_falls_back_to_free_after_grace_expires(self):
        import datetime

        casdoor_id = "cid-grace-expired-checklimit"
        expired_end = (
            datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=30)
        ).isoformat()
        data = {
            "plan": "Relay Builder",
            "subscription": {"status": "cancelled", "current_period_end": expired_end},
            "entitlements": {
                k: billing_stub._format_entitlement_value(v)
                for k, v in billing_stub.STUB_PLANS["builder"]["entitlements"].items()
            },
        }
        billing_service._entitlements_cache[casdoor_id] = (data, time.monotonic())

        # Builder allows 10 shares; grace expired -> falls back to Free's 3.
        with pytest.raises(billing_service.LimitExceededError) as exc_info:
            await billing_service.check_limit(casdoor_id, "max_shares", current_count=3)
        assert exc_info.value.max_value == 3
        assert exc_info.value.plan == "Free (expired)"

    @pytest.mark.asyncio
    async def test_check_limit_does_not_fall_back_in_stub_mode(self):
        """Documents the guard directly: same cancelled+expired subscription
        as above, but with BILLING_STUB_MODE left at the file's normal
        default (true) -> the downgrade is skipped and Builder's higher
        limit is (perhaps surprisingly) still honored."""
        os.environ["BILLING_STUB_MODE"] = "true"
        get_settings.cache_clear()
        import datetime

        casdoor_id = "cid-grace-expired-stubmode"
        expired_end = (
            datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=30)
        ).isoformat()
        data = {
            "plan": "Relay Builder",
            "subscription": {"status": "cancelled", "current_period_end": expired_end},
            "entitlements": {
                k: billing_stub._format_entitlement_value(v)
                for k, v in billing_stub.STUB_PLANS["builder"]["entitlements"].items()
            },
        }
        billing_service._entitlements_cache[casdoor_id] = (data, time.monotonic())

        # In stub mode the fallback branch's `if not settings.billing_stub_mode`
        # guard is False, so entitlements are NOT swapped to Free -> Builder's
        # max_shares=10 still applies even though grace has expired.
        await billing_service.check_limit(casdoor_id, "max_shares", current_count=9)
        with pytest.raises(billing_service.LimitExceededError) as exc_info:
            await billing_service.check_limit(casdoor_id, "max_shares", current_count=10)
        assert exc_info.value.max_value == 10
        assert exc_info.value.plan == "Relay Builder"

    @pytest.mark.asyncio
    async def test_check_limit_keeps_paid_plan_within_grace_window(self):
        import datetime

        casdoor_id = "cid-grace-active-checklimit"
        recent_end = (
            datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=1)
        ).isoformat()
        data = {
            "plan": "Relay Builder",
            "subscription": {"status": "cancelled", "current_period_end": recent_end},
            "entitlements": {
                k: billing_stub._format_entitlement_value(v)
                for k, v in billing_stub.STUB_PLANS["builder"]["entitlements"].items()
            },
        }
        billing_service._entitlements_cache[casdoor_id] = (data, time.monotonic())

        # Still within the 7-day grace window -> stays on Builder's limit of 10,
        # not downgraded to Free's 3.
        await billing_service.check_limit(casdoor_id, "max_shares", current_count=9)
        with pytest.raises(billing_service.LimitExceededError) as exc_info:
            await billing_service.check_limit(casdoor_id, "max_shares", current_count=10)
        assert exc_info.value.max_value == 10
        assert exc_info.value.plan == "Relay Builder"


# ---------------------------------------------------------------------------
# usage_service — real DB counts
# ---------------------------------------------------------------------------


class TestUsageCounting:
    def test_count_user_shares(self, client: TestClient, db_session):
        from app.db import models

        token = register_and_login(client, "usage-shares@example.com")
        client.post("/shares", json={"kind": "doc", "path": "a.md"}, headers=auth_headers(token))
        client.post("/shares", json={"kind": "doc", "path": "b.md"}, headers=auth_headers(token))

        user = db_session.query(models.User).filter_by(email="usage-shares@example.com").one()
        assert usage_service.count_user_shares(db_session, user.id) == 2

    def test_count_web_published_excludes_unpublished(self, client: TestClient, db_session):
        from app.db import models

        token = register_and_login(client, "usage-web@example.com")
        client.post(
            "/shares", json={"kind": "doc", "path": "unpub.md"}, headers=auth_headers(token)
        )

        user = db_session.query(models.User).filter_by(email="usage-web@example.com").one()
        assert usage_service.count_web_published(db_session, user.id) == 0


# ---------------------------------------------------------------------------
# shares.py enforcement — via the route, not the helper (#f08f0c25 AC #2)
# ---------------------------------------------------------------------------


class TestShareLimitEnforcementViaRoute:
    def test_up_to_max_shares_succeeds(self, client: TestClient):
        token = register_and_login(client, "route-shares-ok@example.com")
        for i in range(3):
            resp = client.post(
                "/shares",
                json={"kind": "doc", "path": f"doc-{i}.md"},
                headers=auth_headers(token),
            )
            assert resp.status_code == 201, resp.text

    def test_share_beyond_max_shares_rejected(self, client: TestClient):
        token = register_and_login(client, "route-shares-over@example.com")
        for i in range(3):
            resp = client.post(
                "/shares",
                json={"kind": "doc", "path": f"doc-{i}.md"},
                headers=auth_headers(token),
            )
            assert resp.status_code == 201, resp.text

        resp = client.post(
            "/shares", json={"kind": "doc", "path": "doc-4th.md"}, headers=auth_headers(token)
        )
        assert resp.status_code == 403
        body = resp.json()
        assert body["error"] == "limit_exceeded"
        assert body["limit"] == "max_shares"

    def test_two_of_three_shares_both_pass(self, client: TestClient):
        token = register_and_login(client, "route-shares-two@example.com")
        resp1 = client.post(
            "/shares", json={"kind": "doc", "path": "a.md"}, headers=auth_headers(token)
        )
        resp2 = client.post(
            "/shares", json={"kind": "doc", "path": "b.md"}, headers=auth_headers(token)
        )
        assert resp1.status_code == 201
        assert resp2.status_code == 201

    def test_web_published_share_beyond_max_web_published_rejected(self, client: TestClient):
        # TR-39 forbids creating a share public+published in one POST (no content
        # yet at creation time) — create private, add content, then publish, same
        # as the real plugin flow. The second share's publish attempt never gets
        # that far: the quota check runs before TR-39's content check.
        token = register_and_login(client, "route-web-over@example.com")
        first = client.post(
            "/shares", json={"kind": "doc", "path": "pub1.md"}, headers=auth_headers(token)
        )
        assert first.status_code == 201, first.text
        publish_first = client.patch(
            f"/shares/{first.json()['id']}",
            json={"web_content": "hello", "visibility": "public", "web_published": True},
            headers=auth_headers(token),
        )
        assert publish_first.status_code == 200, publish_first.text

        second = client.post(
            "/shares", json={"kind": "doc", "path": "pub2.md"}, headers=auth_headers(token)
        )
        assert second.status_code == 201, second.text
        resp = client.patch(
            f"/shares/{second.json()['id']}",
            json={"web_content": "hello", "visibility": "public", "web_published": True},
            headers=auth_headers(token),
        )
        assert resp.status_code == 403
        assert resp.json()["limit"] == "max_web_published"

    def test_private_web_publish_rejected_on_free_plan(self, client: TestClient):
        token = register_and_login(client, "route-visibility-private@example.com")
        resp = client.post(
            "/shares",
            json={
                "kind": "doc",
                "path": "priv.md",
                "web_published": True,
                "visibility": "private",
            },
            headers=auth_headers(token),
        )
        assert resp.status_code == 403
        assert resp.json()["error"] == "visibility_not_allowed"

    def test_private_non_published_share_not_gated_by_visibility(self, client: TestClient):
        """visibility=private without web_published must NOT be blocked — the
        allowed_web_visibility tier only governs the published web page."""
        token = register_and_login(client, "route-visibility-nopub@example.com")
        resp = client.post(
            "/shares",
            json={"kind": "doc", "path": "priv2.md", "visibility": "private"},
            headers=auth_headers(token),
        )
        assert resp.status_code == 201

    def test_update_share_to_web_published_enforces_max_web_published(self, client: TestClient):
        token = register_and_login(client, "route-update-pub@example.com")
        first = client.post(
            "/shares", json={"kind": "doc", "path": "u1.md"}, headers=auth_headers(token)
        )
        assert first.status_code == 201, first.text
        publish_first = client.patch(
            f"/shares/{first.json()['id']}",
            json={"web_content": "hello", "visibility": "public", "web_published": True},
            headers=auth_headers(token),
        )
        assert publish_first.status_code == 200, publish_first.text

        second = client.post(
            "/shares", json={"kind": "doc", "path": "u2.md"}, headers=auth_headers(token)
        )
        share_id = second.json()["id"]

        resp = client.patch(
            f"/shares/{share_id}", json={"web_published": True}, headers=auth_headers(token)
        )
        assert resp.status_code == 403
        assert resp.json()["limit"] == "max_web_published"

    def test_update_share_visibility_to_private_while_published_rejected(self, client: TestClient):
        token = register_and_login(client, "route-update-visibility@example.com")
        created = client.post(
            "/shares", json={"kind": "doc", "path": "u3.md"}, headers=auth_headers(token)
        )
        assert created.status_code == 201, created.text
        share_id = created.json()["id"]
        publish = client.patch(
            f"/shares/{share_id}",
            json={"web_content": "hello", "visibility": "public", "web_published": True},
            headers=auth_headers(token),
        )
        assert publish.status_code == 200, publish.text

        resp = client.patch(
            f"/shares/{share_id}", json={"visibility": "private"}, headers=auth_headers(token)
        )
        assert resp.status_code == 403
        assert resp.json()["error"] == "visibility_not_allowed"

    def test_publish_without_content_still_blocked_by_tr39_when_billing_enabled(
        self, client: TestClient
    ):
        """TR-39's no-content guard lives in share_service.update_share, called
        AFTER the billing check block in the route. Every other TR-39 test in
        this file runs with billing disabled (test_public_content_guard.py /
        test_web_publish.py) — nothing confirmed billing enforcement doesn't
        short-circuit or shadow TR-39 once check_limit/check_visibility both
        pass. Here quota (1 allowed, 0 used) and visibility (public, allowed)
        both pass, so this must reach TR-39's check and get its 400 — not a
        403, and not a silent 200."""
        token = register_and_login(client, "route-tr39-billing@example.com")
        created = client.post(
            "/shares", json={"kind": "doc", "path": "empty.md"}, headers=auth_headers(token)
        )
        assert created.status_code == 201, created.text

        resp = client.patch(
            f"/shares/{created.json()['id']}",
            json={"visibility": "public", "web_published": True},
            headers=auth_headers(token),
        )
        assert resp.status_code == 400, resp.text


class TestBillingOwnerVsAdminCasdoorResolution:
    """update_share/add_member allow an admin to act on someone else's share
    (ensure_owner_or_admin) — quotas must be checked against the SHARE OWNER's
    plan, not the acting admin's (_billing_owner in shares.py). Nothing in the
    original test set exercised the admin-not-owner path at all; stub mode
    also gives every casdoor_id the same Free plan, so a same-plan test
    couldn't distinguish "checked owner's plan" from "checked admin's plan"
    even if it existed. Assert directly on which casdoor_id billing_service
    is called with.
    """

    def test_update_share_by_admin_checks_owner_casdoor_id_not_admins(
        self, client: TestClient, db_session
    ):
        from app.db import models

        owner_token = register_and_login(client, "owner-billing-update@example.com")
        admin_token = register_and_login(client, "admin-billing-update@example.com")

        owner = (
            db_session.query(models.User).filter_by(email="owner-billing-update@example.com").one()
        )
        admin = (
            db_session.query(models.User).filter_by(email="admin-billing-update@example.com").one()
        )
        owner.casdoor_id = "owner-cid-update"
        admin.casdoor_id = "admin-cid-update"
        admin.is_admin = True
        db_session.commit()

        created = client.post(
            "/shares",
            json={"kind": "doc", "path": "adm-update.md"},
            headers=auth_headers(owner_token),
        )
        assert created.status_code == 201, created.text
        share_id = created.json()["id"]

        with (
            patch(
                "app.api.routers.shares.billing_service.check_limit", new_callable=AsyncMock
            ) as mock_check_limit,
            patch(
                "app.api.routers.shares.billing_service.check_visibility", new_callable=AsyncMock
            ) as mock_check_visibility,
        ):
            resp = client.patch(
                f"/shares/{share_id}",
                json={"web_content": "hello", "visibility": "public", "web_published": True},
                headers=auth_headers(admin_token),
            )
        assert resp.status_code == 200, resp.text
        mock_check_limit.assert_awaited_once_with("owner-cid-update", "max_web_published", 0)
        mock_check_visibility.assert_awaited_once_with("owner-cid-update", "public")

    def test_add_member_by_admin_checks_owner_casdoor_id_not_admins(
        self, client: TestClient, db_session
    ):
        from app.db import models

        owner_token = register_and_login(client, "owner-billing-members@example.com")
        admin_token = register_and_login(client, "admin-billing-members@example.com")
        register_and_login(client, "new-billing-member@example.com")

        owner = (
            db_session.query(models.User).filter_by(email="owner-billing-members@example.com").one()
        )
        admin = (
            db_session.query(models.User).filter_by(email="admin-billing-members@example.com").one()
        )
        new_member = (
            db_session.query(models.User).filter_by(email="new-billing-member@example.com").one()
        )
        owner.casdoor_id = "owner-cid-members"
        admin.casdoor_id = "admin-cid-members"
        admin.is_admin = True
        db_session.commit()

        share_resp = client.post(
            "/shares",
            json={"kind": "doc", "path": "adm-share.md"},
            headers=auth_headers(owner_token),
        )
        assert share_resp.status_code == 201, share_resp.text
        share_id = share_resp.json()["id"]

        with patch(
            "app.api.routers.shares.billing_service.check_limit", new_callable=AsyncMock
        ) as mock_check_limit:
            resp = client.post(
                f"/shares/{share_id}/members",
                json={"user_id": str(new_member.id), "role": "viewer"},
                headers=auth_headers(admin_token),
            )
        assert resp.status_code == 201, resp.text
        mock_check_limit.assert_awaited_once_with("owner-cid-members", "max_members_per_share", 0)


class TestGracePeriodFallbackViaRoute:
    @pytest.fixture(autouse=True)
    def _non_stub_for_grace_fallback(self):
        # See TestCheckLimitGracePeriodFallback: the downgrade is a no-op in
        # stub mode (the file's default), so this route-level companion needs
        # it off too, or it would pass for the wrong reason (Builder's higher
        # limit, not Free's). Non-stub mode requires a service token at app
        # startup (M-16 validation in main.py) even though our casdoor_id's
        # entitlements come entirely from the pre-seeded cache and never hit
        # the real client.
        os.environ["BILLING_STUB_MODE"] = "false"
        os.environ["BILLING_SERVICE_TOKEN"] = "test-service-token"
        get_settings.cache_clear()
        yield
        os.environ["BILLING_STUB_MODE"] = "true"
        os.environ.pop("BILLING_SERVICE_TOKEN", None)
        get_settings.cache_clear()

    def test_create_share_grace_expired_falls_back_to_free_limit(
        self, client: TestClient, db_session
    ):
        """Companion to TestCheckLimitGracePeriodFallback (billing_service-level):
        confirms the grace-expired downgrade actually reaches a shares.py call
        site, not just the standalone function."""
        import datetime

        from app.db import models

        token = register_and_login(client, "grace-expired-route@example.com")
        user = (
            db_session.query(models.User).filter_by(email="grace-expired-route@example.com").one()
        )
        user.casdoor_id = "grace-expired-route-cid"
        db_session.commit()

        expired_end = (
            datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=30)
        ).isoformat()
        billing_service._entitlements_cache["grace-expired-route-cid"] = (
            {
                "plan": "Relay Builder",
                "subscription": {"status": "cancelled", "current_period_end": expired_end},
                "entitlements": {
                    k: billing_stub._format_entitlement_value(v)
                    for k, v in billing_stub.STUB_PLANS["builder"]["entitlements"].items()
                },
            },
            time.monotonic(),
        )

        # Builder would allow 10 shares; grace expired -> effectively Free's 3.
        for i in range(3):
            resp = client.post(
                "/shares",
                json={"kind": "doc", "path": f"grace-{i}.md"},
                headers=auth_headers(token),
            )
            assert resp.status_code == 201, resp.text

        resp = client.post(
            "/shares", json={"kind": "doc", "path": "grace-4th.md"}, headers=auth_headers(token)
        )
        assert resp.status_code == 403, resp.text
        body = resp.json()
        assert body["limit"] == "max_shares"
        assert body["max"] == 3
        assert body["plan"] == "Free (expired)"


class TestMemberLimitEnforcementViaRoute:
    def test_members_beyond_max_members_per_share_rejected(self, client: TestClient, db_session):
        from app.db import models

        owner_token = register_and_login(client, "route-members-owner@example.com")
        share_resp = client.post(
            "/shares", json={"kind": "doc", "path": "shared.md"}, headers=auth_headers(owner_token)
        )
        share_id = share_resp.json()["id"]

        for i in range(3):
            register_and_login(client, f"route-member-{i}@example.com")
            member = (
                db_session.query(models.User).filter_by(email=f"route-member-{i}@example.com").one()
            )
            resp = client.post(
                f"/shares/{share_id}/members",
                json={"user_id": str(member.id), "role": "viewer"},
                headers=auth_headers(owner_token),
            )
            assert resp.status_code == 201, resp.text

        register_and_login(client, "route-member-extra@example.com")
        extra = (
            db_session.query(models.User).filter_by(email="route-member-extra@example.com").one()
        )
        resp = client.post(
            f"/shares/{share_id}/members",
            json={"user_id": str(extra.id), "role": "viewer"},
            headers=auth_headers(owner_token),
        )
        assert resp.status_code == 403
        assert resp.json()["limit"] == "max_members_per_share"


# ---------------------------------------------------------------------------
# Billing disabled -> every /billing/* endpoint 404s (feature-flagged off)
# ---------------------------------------------------------------------------


class TestBillingDisabled:
    def test_billing_endpoints_404_when_disabled(self, client: TestClient):
        os.environ["BILLING_ENABLED"] = "false"
        get_settings.cache_clear()
        try:
            token = register_and_login(client, "disabled-billing@example.com")
            assert client.get("/v1/billing/plan", headers=auth_headers(token)).status_code == 404
            assert client.get("/v1/billing/plans").status_code == 404
            checkout_resp = client.post(
                "/v1/billing/checkout", json={}, headers=auth_headers(token)
            )
            assert checkout_resp.status_code == 404
        finally:
            os.environ["BILLING_ENABLED"] = "true"
            get_settings.cache_clear()

    def test_share_and_member_quotas_not_enforced_when_billing_disabled(
        self, client: TestClient, db_session
    ):
        """The only billing_enabled=False coverage before this test was the
        /v1/billing/* 404 check above — nothing confirmed the three new
        check_limit/check_visibility call sites wired into shares.py
        (create_share/update_share/add_member) actually no-op when the flag
        is off, as opposed to still gating (settings read at call time, not
        cached at import time — worth confirming directly)."""
        from app.db import models

        os.environ["BILLING_ENABLED"] = "false"
        get_settings.cache_clear()
        try:
            token = register_and_login(client, "disabled-shares@example.com")

            # Free plan caps max_shares at 3 -- billing off must allow more.
            share_ids = []
            for i in range(5):
                resp = client.post(
                    "/shares",
                    json={"kind": "doc", "path": f"noquota-{i}.md"},
                    headers=auth_headers(token),
                )
                assert resp.status_code == 201, resp.text
                share_ids.append(resp.json()["id"])

            # Free plan disallows 'private' web-published visibility -- billing
            # off must allow it (would be 403 visibility_not_allowed if enabled,
            # per test_private_web_publish_rejected_on_free_plan above).
            publish_resp = client.patch(
                f"/shares/{share_ids[0]}",
                json={"web_content": "hi", "visibility": "private", "web_published": True},
                headers=auth_headers(token),
            )
            assert publish_resp.status_code == 200, publish_resp.text

            # Free plan caps max_members_per_share at 3 -- billing off must
            # allow a 4th.
            for i in range(4):
                register_and_login(client, f"disabled-member-{i}@example.com")
                member = (
                    db_session.query(models.User)
                    .filter_by(email=f"disabled-member-{i}@example.com")
                    .one()
                )
                member_resp = client.post(
                    f"/shares/{share_ids[1]}/members",
                    json={"user_id": str(member.id), "role": "viewer"},
                    headers=auth_headers(token),
                )
                assert member_resp.status_code == 201, member_resp.text
        finally:
            os.environ["BILLING_ENABLED"] = "true"
            get_settings.cache_clear()


# ---------------------------------------------------------------------------
# /v1/billing/plan, /plans, /checkout, /cancel, /portal (stub mode)
# ---------------------------------------------------------------------------


class TestBillingPlanEndpoint:
    def test_get_billing_plan_returns_usage_and_entitlements(self, client: TestClient):
        token = register_and_login(client, "plan-endpoint@example.com")
        client.post("/shares", json={"kind": "doc", "path": "p.md"}, headers=auth_headers(token))

        resp = client.get("/v1/billing/plan", headers=auth_headers(token))
        assert resp.status_code == 200
        data = resp.json()
        assert data["plan"] == "Relay Free"
        assert data["usage"]["shares"]["current"] == 1
        assert data["usage"]["shares"]["max"] == 3

    def test_get_billing_plan_requires_auth(self, client: TestClient):
        assert client.get("/v1/billing/plan").status_code == 401

    def test_get_billing_plan_storage_usage_uses_current_max_not_bytes_suffix(
        self, client: TestClient
    ):
        """Regression for #f696490d: storage's usage entry was the only one
        of the three (shares/web_published/storage) keyed current_bytes/
        max_bytes instead of current/max. The plugin reads usage.current/
        usage.max uniformly for every row, so storage specifically always
        saw undefined and rendered "-- / Unlimited" regardless of the real
        numbers (#1b3f600c only fixed the undefined -> "Unlimited" label,
        not this root cause).

        current_bytes/max_bytes stay in the response as deprecated aliases,
        mirroring current/max exactly -- installed plugin builds <=1.1.43
        read usage.current_bytes directly with no current/max fallback, so
        dropping the old names outright would blank the Storage row for
        every already-installed user (Daedalus review on !252).
        """
        token = register_and_login(client, "storage-usage@example.com")
        resp = client.get("/v1/billing/plan", headers=auth_headers(token))
        assert resp.status_code == 200
        storage = resp.json()["usage"]["storage"]
        assert "current" in storage
        assert "max" in storage
        assert storage["max"] == 524288000  # Free plan's max_storage_bytes entitlement
        assert storage["current_bytes"] == storage["current"]
        assert storage["max_bytes"] == storage["max"]


class TestBillingPlansEndpoint:
    def test_get_billing_plans_is_public(self, client: TestClient):
        resp = client.get("/v1/billing/plans")
        assert resp.status_code == 200
        assert len(resp.json()["plans"]) == 2


class TestCheckoutEndpoint:
    def test_checkout_stub_mode_returns_not_available(self, client: TestClient):
        token = register_and_login(client, "checkout@example.com")
        resp = client.post(
            "/v1/billing/checkout",
            json={"product_id": "prod_relay_builder"},
            headers=auth_headers(token),
        )
        assert resp.status_code == 200
        assert resp.json()["subscription_id"] is None


class TestCancelEndpoint:
    def test_cancel_stub_mode_returns_not_available(self, client: TestClient):
        token = register_and_login(client, "cancel@example.com")
        resp = client.post("/v1/billing/cancel", headers=auth_headers(token))
        assert resp.status_code == 200
        assert resp.json()["status"] == "not_available"


class TestPortalEndpoint:
    def test_portal_stub_mode(self, client: TestClient):
        token = register_and_login(client, "portal@example.com")
        resp = client.post("/v1/billing/portal", json={}, headers=auth_headers(token))
        assert resp.status_code == 200
        assert resp.json()["url"] is None


# ---------------------------------------------------------------------------
# GET /billing/callback (public SSR page)
# ---------------------------------------------------------------------------


class TestBillingCallback:
    def test_callback_success_status(self, client: TestClient):
        resp = client.get("/billing/callback?payment_status=success&subscription_id=sub_123")
        assert resp.status_code == 200
        assert "sub_123" in resp.text

    def test_callback_no_params_defaults_unknown(self, client: TestClient):
        resp = client.get("/billing/callback")
        assert resp.status_code == 200

    def test_callback_legacy_cancelled_maps_to_canceled(self, client: TestClient):
        resp = client.get("/billing/callback?status=cancelled")
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# POST /v1/billing/webhooks
# ---------------------------------------------------------------------------


def _sign(body: bytes, secret: str, timestamp: str) -> str:
    message = f"{timestamp}.{body.decode('utf-8')}"
    return hmac.new(secret.encode(), message.encode(), hashlib.sha256).hexdigest()


class TestBillingWebhook:
    SECRET = "test-webhook-secret"

    @pytest.fixture(autouse=True)
    def _webhook_secret(self):
        os.environ["BILLING_WEBHOOK_SECRET"] = self.SECRET
        get_settings.cache_clear()
        yield
        os.environ.pop("BILLING_WEBHOOK_SECRET", None)
        get_settings.cache_clear()

    def _post_webhook(self, client: TestClient, payload: dict, event_id: str | None = None):
        import json

        body = json.dumps(payload).encode()
        ts = str(int(time.time()))
        event_id = event_id or str(uuid.uuid4())
        headers = {
            "X-Webhook-Signature": _sign(body, self.SECRET, ts),
            "X-Webhook-Timestamp": ts,
            "X-Webhook-Id": event_id,
            "Content-Type": "application/json",
        }
        return client.post("/v1/billing/webhooks", content=body, headers=headers), event_id

    def test_missing_event_id_rejected(self, client: TestClient):
        resp = client.post(
            "/v1/billing/webhooks",
            content=b"{}",
            headers={"X-Webhook-Signature": "x", "X-Webhook-Timestamp": str(int(time.time()))},
        )
        assert resp.status_code == 400

    def test_bad_signature_rejected(self, client: TestClient):
        resp = client.post(
            "/v1/billing/webhooks",
            content=b"{}",
            headers={
                "X-Webhook-Signature": "bad-signature",
                "X-Webhook-Timestamp": str(int(time.time())),
                "X-Webhook-Id": str(uuid.uuid4()),
            },
        )
        assert resp.status_code == 401

    def test_stale_timestamp_rejected(self, client: TestClient):
        body = b"{}"
        old_ts = str(int(time.time()) - 600)  # 10 minutes old, > 5-minute window
        resp = client.post(
            "/v1/billing/webhooks",
            content=body,
            headers={
                "X-Webhook-Signature": _sign(body, self.SECRET, old_ts),
                "X-Webhook-Timestamp": old_ts,
                "X-Webhook-Id": str(uuid.uuid4()),
            },
        )
        assert resp.status_code == 401

    def test_valid_webhook_processed_and_deduped(self, client: TestClient, db_session):
        from app.db import models

        resp1, event_id = self._post_webhook(
            client, {"event": "subscription.created", "data": {"user_id": "casdoor-abc"}}
        )
        assert resp1.status_code == 200
        assert resp1.json()["duplicate"] is False if "duplicate" in resp1.json() else True

        # Replay same event_id -> dedup, no second audit log
        resp2, _ = self._post_webhook(
            client,
            {"event": "subscription.created", "data": {"user_id": "casdoor-abc"}},
            event_id=event_id,
        )
        assert resp2.status_code == 200
        assert resp2.json()["duplicate"] is True

        count = db_session.query(models.BillingWebhookEvent).filter_by(event_id=event_id).count()
        assert count == 1

    def test_webhook_creates_audit_log(self, client: TestClient, db_session):
        from app.db import models

        _, event_id = self._post_webhook(
            client, {"event": "subscription.activated", "data": {"user_id": "casdoor-audit"}}
        )
        audit = (
            db_session.query(models.AuditLog)
            .filter_by(action=models.AuditAction.BILLING_SUBSCRIPTION_ACTIVATED)
            .first()
        )
        assert audit is not None

    def test_webhook_stores_subscription_id_on_user(self, client: TestClient, db_session):
        from app.db import models

        token = register_and_login(client, "webhook-user@example.com")
        user = db_session.query(models.User).filter_by(email="webhook-user@example.com").one()
        user.casdoor_id = "casdoor-webhook-user"
        db_session.commit()

        self._post_webhook(
            client,
            {
                "event": "subscription.created",
                "data": {"user_id": "casdoor-webhook-user", "subscription_id": "sub_xyz"},
            },
        )

        db_session.refresh(user)
        assert user.billing_subscription_id == "sub_xyz"

    def test_webhook_finds_user_via_oauth_provider_user_id_when_casdoor_id_is_null(
        self, client: TestClient, db_session
    ):
        """The bug (#c44c6e87): no code path ever writes users.casdoor_id (87%
        NULL on prod) — billing_service.get_billing_identity() actually sends
        the OAuth provider_user_id. Before the fix, the webhook looked up
        strictly by users.casdoor_id and silently dropped this case."""
        from app.db import models

        token = register_and_login(client, "webhook-oauth-user@example.com")
        user = db_session.query(models.User).filter_by(email="webhook-oauth-user@example.com").one()
        assert user.casdoor_id is None

        provider = models.OAuthProvider(
            id=uuid.uuid4(),
            name="casdoor",
            provider_type=models.OAuthProviderType.OIDC,
            issuer_url="https://casdoor.example.com",
            client_id="test_client",
            client_secret_encrypted="secret",
            enabled=True,
            auto_register=True,
        )
        db_session.add(provider)
        db_session.flush()
        db_session.add(
            models.UserOAuthAccount(
                id=uuid.uuid4(),
                user_id=user.id,
                provider_id=provider.id,
                provider_user_id="oauth-subject-not-casdoor-id",
                email=user.email,
            )
        )
        db_session.commit()

        self._post_webhook(
            client,
            {
                "event": "subscription.created",
                "data": {
                    "user_id": "oauth-subject-not-casdoor-id",
                    "subscription_id": "sub_via_oauth",
                },
            },
        )

        db_session.refresh(user)
        assert user.billing_subscription_id == "sub_via_oauth"

    def test_webhook_finds_user_via_internal_id_fallback(self, client: TestClient, db_session):
        """get_billing_identity()'s third fallback (no casdoor_id, no OAuth
        account) sends str(user.id) — the reverse lookup must resolve that
        too, not just the two OAuth-shaped forms."""
        from app.db import models

        token = register_and_login(client, "webhook-internal-id-user@example.com")
        user = (
            db_session.query(models.User)
            .filter_by(email="webhook-internal-id-user@example.com")
            .one()
        )
        assert user.casdoor_id is None

        self._post_webhook(
            client,
            {
                "event": "subscription.created",
                "data": {"user_id": str(user.id), "subscription_id": "sub_via_internal_id"},
            },
        )

        db_session.refresh(user)
        assert user.billing_subscription_id == "sub_via_internal_id"

    def test_webhook_unresolvable_user_id_logs_warning_instead_of_silent_drop(
        self, client: TestClient, caplog
    ):
        """A miss used to be `if user: ...` with no else — the
        subscription_id vanished with zero error signal. It must now log
        with the user_id so the miss is at least visible."""
        import logging

        with caplog.at_level(logging.WARNING, logger="app.api.routers.billing_webhooks"):
            resp, _ = self._post_webhook(
                client,
                {
                    "event": "subscription.created",
                    "data": {"user_id": "no-such-user-anywhere", "subscription_id": "sub_lost"},
                },
            )
        assert resp.status_code == 200
        assert any(
            "no-such-user-anywhere" in str(record.__dict__.get("user_id", ""))
            or "no-such-user-anywhere" in record.getMessage()
            for record in caplog.records
        )

    def test_no_secret_configured_rejects_all(self, client: TestClient):
        os.environ.pop("BILLING_WEBHOOK_SECRET", None)
        get_settings.cache_clear()
        try:
            body = b"{}"
            ts = str(int(time.time()))
            resp = client.post(
                "/v1/billing/webhooks",
                content=body,
                headers={
                    "X-Webhook-Signature": "irrelevant",
                    "X-Webhook-Timestamp": ts,
                    "X-Webhook-Id": str(uuid.uuid4()),
                },
            )
            assert resp.status_code == 401
        finally:
            os.environ["BILLING_WEBHOOK_SECRET"] = self.SECRET
            get_settings.cache_clear()


# ---------------------------------------------------------------------------
# #1ccd7956 — provider_user_id is only unique paired with provider_id
# (uq_provider_user is composite); a second OAuth provider can legitimately
# produce a duplicate provider_user_id string, which used to make
# find_user_by_billing_identity() raise MultipleResultsFound (webhook 500)
# instead of resolving deterministically.
# ---------------------------------------------------------------------------


class TestFindUserByBillingIdentityAmbiguousProviderUserId:
    def _make_provider(self, db_session, name: str):
        from app.db import models

        provider = models.OAuthProvider(
            id=uuid.uuid4(),
            name=name,
            provider_type=models.OAuthProviderType.OIDC,
            issuer_url=f"https://{name}.example.com",
            client_id="test_client",
            client_secret_encrypted="secret",
            enabled=True,
            auto_register=True,
        )
        db_session.add(provider)
        db_session.commit()
        return provider

    def test_REGRESSION_same_provider_user_id_across_two_providers_resolves_deterministically(
        self, db_session
    ):
        """MUST fail on the pre-fix code: a plain scalar_one_or_none() over
        both rows raises MultipleResultsFound instead of picking one."""
        from app.db import models

        configured_provider = self._make_provider(db_session, "casdoor")  # settings default
        other_provider = self._make_provider(db_session, "google")

        user_a = models.User(
            id=uuid.uuid4(), email="a@example.com", password_hash="", is_active=True
        )
        user_b = models.User(
            id=uuid.uuid4(), email="b@example.com", password_hash="", is_active=True
        )
        db_session.add_all([user_a, user_b])
        db_session.flush()

        shared_subject = "shared-subject-123"
        db_session.add(
            models.UserOAuthAccount(
                id=uuid.uuid4(),
                user_id=user_a.id,
                provider_id=configured_provider.id,
                provider_user_id=shared_subject,
                email="a@example.com",
            )
        )
        db_session.add(
            models.UserOAuthAccount(
                id=uuid.uuid4(),
                user_id=user_b.id,
                provider_id=other_provider.id,
                provider_user_id=shared_subject,
                email="b@example.com",
            )
        )
        db_session.commit()

        resolved = billing_service.find_user_by_billing_identity(db_session, shared_subject)
        assert resolved is not None
        # Deterministic: the row on the configured OAuth provider wins.
        assert resolved.id == user_a.id

    def test_ambiguous_match_is_logged(self, db_session, caplog):
        import logging

        from app.db import models

        configured_provider = self._make_provider(db_session, "casdoor")
        other_provider = self._make_provider(db_session, "google")
        user_a = models.User(
            id=uuid.uuid4(), email="loga@example.com", password_hash="", is_active=True
        )
        user_b = models.User(
            id=uuid.uuid4(), email="logb@example.com", password_hash="", is_active=True
        )
        db_session.add_all([user_a, user_b])
        db_session.flush()
        shared_subject = "shared-subject-for-log-test"
        db_session.add_all(
            [
                models.UserOAuthAccount(
                    id=uuid.uuid4(),
                    user_id=user_a.id,
                    provider_id=configured_provider.id,
                    provider_user_id=shared_subject,
                    email="loga@example.com",
                ),
                models.UserOAuthAccount(
                    id=uuid.uuid4(),
                    user_id=user_b.id,
                    provider_id=other_provider.id,
                    provider_user_id=shared_subject,
                    email="logb@example.com",
                ),
            ]
        )
        db_session.commit()

        with caplog.at_level(logging.WARNING, logger="app.services.billing_service"):
            billing_service.find_user_by_billing_identity(db_session, shared_subject)

        assert any(
            shared_subject in record.getMessage()
            or shared_subject in str(record.__dict__.get("provider_user_id", ""))
            for record in caplog.records
        )

    def test_unambiguous_single_match_still_works(self, db_session):
        """Positive control: the common case (one provider, one match) is
        untouched by the new resolution logic."""
        from app.db import models

        provider = self._make_provider(db_session, "casdoor")
        user = models.User(
            id=uuid.uuid4(), email="solo@example.com", password_hash="", is_active=True
        )
        db_session.add(user)
        db_session.flush()
        db_session.add(
            models.UserOAuthAccount(
                id=uuid.uuid4(),
                user_id=user.id,
                provider_id=provider.id,
                provider_user_id="solo-subject",
                email="solo@example.com",
            )
        )
        db_session.commit()

        resolved = billing_service.find_user_by_billing_identity(db_session, "solo-subject")
        assert resolved is not None
        assert resolved.id == user.id

    def test_users_casdoor_id_is_globally_unique_at_the_db_level(self, db_session):
        """The task's symmetric concern ('users.casdoor_id тоже не уникален')
        does not hold: ix_users_casdoor_id is a real unique index (see
        migration 202607250001), so two users can never share a
        casdoor_id — this documents that find_user_by_billing_identity()'s
        casdoor_id branch needs no equivalent fix, with a live DB-level
        proof rather than just reading the model definition."""
        from sqlalchemy.exc import IntegrityError

        from app.db import models

        user_a = models.User(
            id=uuid.uuid4(),
            email="dup-a@example.com",
            password_hash="",
            is_active=True,
            casdoor_id="duplicate-casdoor-id",
        )
        db_session.add(user_a)
        db_session.commit()

        user_b = models.User(
            id=uuid.uuid4(),
            email="dup-b@example.com",
            password_hash="",
            is_active=True,
            casdoor_id="duplicate-casdoor-id",
        )
        db_session.add(user_b)
        with pytest.raises(IntegrityError):
            db_session.commit()
        db_session.rollback()


# ---------------------------------------------------------------------------
# server/info — billing_enabled feature flag + enterprise edition
# ---------------------------------------------------------------------------


class TestServerInfoBilling:
    def test_billing_enabled_true_when_configured(self, client: TestClient):
        resp = client.get("/server/info")
        assert resp.status_code == 200
        assert resp.json()["edition"] == "enterprise"
        assert resp.json()["features"]["billing_enabled"] is True

    def test_billing_enabled_false_by_default(self, client: TestClient):
        os.environ["BILLING_ENABLED"] = "false"
        get_settings.cache_clear()
        try:
            resp = client.get("/server/info")
            assert resp.json()["features"]["billing_enabled"] is False
        finally:
            os.environ["BILLING_ENABLED"] = "true"
            get_settings.cache_clear()


# ---------------------------------------------------------------------------
# casdoor_id resolution fallback (_get_casdoor_id)
# ---------------------------------------------------------------------------


class TestCasdoorId:
    def test_uses_user_casdoor_id_when_set(self, client: TestClient, db_session):
        from app.db import models

        token = register_and_login(client, "casdoor-direct@example.com")
        user = db_session.query(models.User).filter_by(email="casdoor-direct@example.com").one()
        user.casdoor_id = "direct-casdoor-id"
        db_session.commit()

        resp = client.get("/v1/billing/plan", headers=auth_headers(token))
        assert resp.status_code == 200

    def test_falls_back_to_user_id_when_no_oauth(self, client: TestClient):
        # No casdoor_id, no oauth account -> falls back to str(user.id), must not crash.
        token = register_and_login(client, "casdoor-fallback@example.com")
        resp = client.get("/v1/billing/plan", headers=auth_headers(token))
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Non-stub mode — BillingClient is the true external boundary, mocked here.
# ---------------------------------------------------------------------------


class TestNonStubModeMocksExternalBillingClient:
    @pytest.fixture(autouse=True)
    def _non_stub(self):
        os.environ["BILLING_STUB_MODE"] = "false"
        os.environ["BILLING_SERVICE_TOKEN"] = "test-service-token"
        get_settings.cache_clear()
        billing_service._billing_client = None
        yield
        os.environ["BILLING_STUB_MODE"] = "true"
        os.environ.pop("BILLING_SERVICE_TOKEN", None)
        get_settings.cache_clear()
        billing_service._billing_client = None

    @pytest.mark.asyncio
    async def test_get_entitlements_falls_back_to_stub_on_billing_service_error(self):
        from app.clients.billing_client import BillingServiceError

        with patch.object(
            billing_service,
            "_get_billing_client",
        ) as mock_get_client:
            mock_client = AsyncMock()
            mock_client.get_entitlements.side_effect = BillingServiceError(
                code="UPSTREAM_DOWN", message="unavailable", status=502
            )
            mock_get_client.return_value = mock_client

            data = await billing_service.get_entitlements_cached("cid-nonstub-fallback")
            # Billing Service failed -> falls back to stub entitlements, does not raise.
            assert data["plan"] in ("Relay Free", "Unknown")

    @pytest.mark.asyncio
    async def test_create_checkout_session_calls_billing_client(self):
        with patch.object(billing_service, "_get_billing_client") as mock_get_client:
            mock_client = AsyncMock()
            mock_client.create_subscription.return_value = {
                "checkout_url": "https://billing.entire.vc/checkout/abc",
                "id": "sub_new",
            }
            mock_get_client.return_value = mock_client

            class _FakeUser:
                billing_subscription_id = None

            class _FakeDb:
                def commit(self):
                    pass

            result = await billing_service.create_checkout_session(
                "cid-checkout", {"product_id": "prod_relay_builder"}, _FakeDb(), _FakeUser()
            )
            assert result["checkout_url"] == "https://billing.entire.vc/checkout/abc"
            mock_client.create_subscription.assert_awaited_once()
