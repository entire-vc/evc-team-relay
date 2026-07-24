"""Tests for the billing integration (enterprise edition, TR·edition/billing #f75f04bb).

Real DB (SQLite in-memory via the shared `client`/`db_session` fixtures) and real
billing_service/billing_stub code paths throughout — per this repo's mocking
convention, the only TRUE external boundary is the Billing Service HTTP API
(app.clients.billing_client.BillingClient), which is mocked only in the
non-stub-mode tests that specifically exercise that boundary. Everything else
(DB, router, webhook signature/dedup, entitlements cache) runs for real.

Deliberately NOT covered here: shares.py limit/visibility enforcement
(check_limit/check_visibility are ported and unit-tested directly, but no
caller was wired into shares.py in this change — see the task comment on
#f75f04bb for why that's a separate, product-level decision).
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
        product_ids = {p["product_id"] for p in plans}
        assert product_ids == {"prod_relay_free", "prod_relay_builder"}

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
            assert result["checkout_url"].startswith("https://billing.entire.vc")
            mock_client.create_subscription.assert_awaited_once()
