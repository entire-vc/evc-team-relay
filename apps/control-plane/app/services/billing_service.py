"""Billing service — entitlement cache, limit enforcement, plan info."""

from __future__ import annotations

import time
import uuid
from typing import Any

from sqlalchemy.orm import Session

from app.clients.billing_client import BillingClient, BillingServiceError
from app.core.config import get_settings
from app.core.logging import get_logger
from app.services.billing_stub import (
    _format_entitlement_value,
    cancel_stub_subscription,
    create_stub_checkout,
    create_stub_portal_session,
    get_stub_entitlements,
    get_stub_plans,
)

logger = get_logger(__name__)

# In-memory entitlements cache: {casdoor_id: (data, timestamp)}
_entitlements_cache: dict[str, tuple[dict[str, Any], float]] = {}
_CACHE_TTL_SECONDS = 300  # 5 minutes

# Singleton billing client (lazy init)
_billing_client: BillingClient | None = None


def _get_limit(entitlements: dict[str, Any], key: str) -> int | None:
    """Extract limit value from entitlements.

    Supports both structured format {"limit": N} and plain int (backwards compat).

    Args:
        entitlements: Entitlements dict
        key: Entitlement key (e.g. 'max_shares')

    Returns:
        Limit value (int) or None (unlimited)
    """
    value = entitlements.get(key)
    if value is None:
        return None
    if isinstance(value, dict) and "limit" in value:
        return value["limit"]
    if isinstance(value, int):
        return value
    return None


def _get_allowed_list(entitlements: dict[str, Any], key: str) -> list[str]:
    """Extract allowed-values list from entitlements.

    Supports:
        - {"allowed": ["public", "protected"]}  (Billing Service format)
        - ["public", "protected"]                (plain list, stub/backwards compat)

    Args:
        entitlements: Entitlements dict
        key: Entitlement key (e.g. 'allowed_web_visibility')

    Returns:
        List of allowed values, defaults to ["public"] if missing/invalid.
    """
    value = entitlements.get(key)
    if value is None:
        return ["public"]
    if isinstance(value, dict) and "allowed" in value:
        return value["allowed"]
    if isinstance(value, list):
        return value
    return ["public"]


class LimitExceededError(Exception):
    """Raised when a user exceeds their plan limit."""

    def __init__(self, limit: str, current: int, max_value: int, plan: str):
        self.limit = limit
        self.current = current
        self.max_value = max_value
        self.plan = plan
        super().__init__(f"Limit exceeded: {limit} ({current}/{max_value}) on plan {plan}")


class VisibilityNotAllowedError(Exception):
    """Raised when a user tries to use a visibility tier not in their plan."""

    def __init__(self, visibility: str, allowed: list[str], plan: str):
        self.visibility = visibility
        self.allowed = allowed
        self.plan = plan
        super().__init__(
            f"Visibility '{visibility}' not allowed on plan {plan}. "
            f"Allowed: {', '.join(allowed)}"
        )


def _get_billing_client() -> BillingClient:
    """Get or create singleton billing client."""
    global _billing_client
    if _billing_client is None:
        settings = get_settings()
        _billing_client = BillingClient(
            base_url=settings.billing_base_url,
            service_token=settings.billing_service_token,
        )
    return _billing_client


async def get_entitlements_cached(casdoor_id: str) -> dict[str, Any]:
    """Get user entitlements with 5-minute TTL cache.

    Returns:
        Dict with 'plan' and 'entitlements' keys.
    """
    now = time.monotonic()

    # Check cache
    cached = _entitlements_cache.get(casdoor_id)
    if cached:
        data, ts = cached
        if now - ts < _CACHE_TTL_SECONDS:
            return data

    # Fetch fresh
    settings = get_settings()
    if settings.billing_stub_mode:
        data = await get_stub_entitlements(casdoor_id)
    else:
        try:
            client = _get_billing_client()
            data = await client.get_entitlements(casdoor_id)
        except BillingServiceError:
            logger.exception("Failed to fetch entitlements from Billing Service")
            # Fallback to cache if available, even if expired
            if cached:
                return cached[0]
            # Last resort: return stub entitlements
            data = await get_stub_entitlements(casdoor_id)

    # Free tier fallback: billing returns empty entitlements for users without subscription.
    # Apply local free-tier defaults so limits are enforced.
    if not data.get("entitlements") and data.get("plan") in ("free", "Free", None):
        from app.services.billing_stub import STUB_PLANS

        free_ent = STUB_PLANS["free"]["entitlements"]
        data["entitlements"] = {k: _format_entitlement_value(v) for k, v in free_ent.items()}

    # Update cache
    _entitlements_cache[casdoor_id] = (data, now)
    return data


def invalidate_cache(casdoor_id: str) -> None:
    """Invalidate cached entitlements for a user (called by webhook handler)."""
    _entitlements_cache.pop(casdoor_id, None)


def is_in_grace_period(subscription: dict[str, Any] | None) -> bool:
    """Check if user is in grace period after subscription expiry.

    Grace period applies when:
    - Subscription status is "cancelled"
    - current_period_end is set
    - Current time < current_period_end + grace_period_days

    Args:
        subscription: Subscription object with status and current_period_end

    Returns:
        True if in grace period, False otherwise
    """
    import datetime

    if not subscription:
        return False

    status = subscription.get("status")
    period_end_str = subscription.get("current_period_end")

    if status != "cancelled" or not period_end_str:
        return False

    settings = get_settings()
    try:
        period_end = datetime.datetime.fromisoformat(period_end_str.replace("Z", "+00:00"))
        grace_end = period_end + datetime.timedelta(days=settings.billing_grace_period_days)
        return datetime.datetime.now(datetime.timezone.utc) < grace_end
    except (ValueError, AttributeError):
        return False


async def check_limit(casdoor_id: str, entitlement_key: str, current_count: int) -> None:
    """Check if current usage is within plan limits.

    Accounts for grace period: if subscription is cancelled but within grace period,
    continue using the cancelled plan's entitlements. After grace period expires,
    fall back to Free plan limits.

    Args:
        casdoor_id: User's Casdoor UUID
        entitlement_key: Entitlement key (e.g. 'max_shares')
        current_count: Current usage count

    Raises:
        LimitExceededError: If the user has reached or exceeded the limit
    """
    data = await get_entitlements_cached(casdoor_id)
    plan_name = data.get("plan", "Unknown")
    subscription = data.get("subscription")
    entitlements = data.get("entitlements", {})

    # Check if in grace period
    if (
        subscription
        and subscription.get("status") in ("cancelled", "expired")
        and not is_in_grace_period(subscription)
    ):
        # Grace period expired, use Free plan limits
        settings = get_settings()
        if not settings.billing_stub_mode:
            from app.services.billing_stub import STUB_PLANS

            free_entitlements = STUB_PLANS["free"]["entitlements"]
            entitlements = free_entitlements
            plan_name = "Free (expired)"

    max_value = _get_limit(entitlements, entitlement_key)

    # None means unlimited
    if max_value is None:
        return

    if current_count >= max_value:
        raise LimitExceededError(
            limit=entitlement_key,
            current=current_count,
            max_value=max_value,
            plan=plan_name,
        )


async def check_visibility(casdoor_id: str, visibility: str) -> None:
    """Check if a visibility tier is allowed by the user's plan.

    Accounts for grace period: after grace period expires, falls back to Free plan
    which only allows 'public'.

    Args:
        casdoor_id: User's Casdoor UUID
        visibility: Target visibility value (e.g. 'public', 'protected', 'private')

    Raises:
        VisibilityNotAllowedError: If the visibility is not in the user's allowed list
    """
    data = await get_entitlements_cached(casdoor_id)
    plan_name = data.get("plan", "Unknown")
    subscription = data.get("subscription")
    entitlements = data.get("entitlements", {})

    # Check if grace period expired → fallback to Free plan
    if (
        subscription
        and subscription.get("status") in ("cancelled", "expired")
        and not is_in_grace_period(subscription)
    ):
        settings = get_settings()
        if not settings.billing_stub_mode:
            from app.services.billing_stub import STUB_PLANS

            entitlements = STUB_PLANS["free"]["entitlements"]
            plan_name = "Free (expired)"

    allowed = _get_allowed_list(entitlements, "allowed_web_visibility")

    if visibility not in allowed:
        raise VisibilityNotAllowedError(
            visibility=visibility,
            allowed=allowed,
            plan=plan_name,
        )


async def get_billing_plan(
    casdoor_id: str,
    db: Session,
    user_id: Any,
) -> dict[str, Any]:
    """Get full billing plan info with usage data for /v1/billing/plan endpoint.

    Args:
        casdoor_id: User's Casdoor UUID
        db: Database session
        user_id: Internal user UUID

    Returns:
        Dict with plan, entitlements, and usage.
    """
    from app.services import usage_service

    data = await get_entitlements_cached(casdoor_id)
    entitlements = data.get("entitlements", {})

    # Get current usage
    shares_count = usage_service.count_user_shares(db, user_id)
    web_published_count = usage_service.count_web_published(db, user_id)
    storage_bytes = await usage_service.get_user_storage_bytes(db, user_id)

    max_shares = _get_limit(entitlements, "max_shares")
    max_web = _get_limit(entitlements, "max_web_published")
    max_storage = _get_limit(entitlements, "max_storage_bytes")
    allowed_visibility = _get_allowed_list(entitlements, "allowed_web_visibility")

    return {
        "plan": data.get("plan", "Unknown"),
        "subscription": data.get("subscription"),
        "entitlements": entitlements,
        "allowed_web_visibility": allowed_visibility,
        "usage": {
            "shares": {
                "current": shares_count,
                "max": max_shares,
                "percentage": (round(shares_count / max_shares * 100) if max_shares else None),
            },
            "web_published": {
                "current": web_published_count,
                "max": max_web,
                "percentage": (round(web_published_count / max_web * 100) if max_web else None),
            },
            "storage": {
                "current_bytes": storage_bytes,
                "max_bytes": max_storage,
                "percentage": (
                    round(storage_bytes / max_storage * 100)
                    if storage_bytes is not None and max_storage
                    else None
                ),
            },
        },
    }


async def get_available_plans() -> list[dict[str, Any]]:
    """Get available plans for upgrade UI."""
    settings = get_settings()
    if settings.billing_stub_mode:
        return await get_stub_plans()

    try:
        client = _get_billing_client()
        result = await client.get_products()
        return result.get("products", [])
    except BillingServiceError:
        logger.exception("Failed to fetch plans from Billing Service")
        return await get_stub_plans()


async def create_checkout_session(
    casdoor_id: str,
    payload: dict[str, Any],
    db: Any,
    user: Any,
) -> dict[str, Any]:
    """Create a checkout session for plan upgrade.

    Args:
        casdoor_id: User's Casdoor UUID
        payload: Dict with product_id and optional price_id
        db: Database session
        user: User model instance

    Returns:
        Dict with checkout_url and subscription_id
    """
    settings = get_settings()
    if settings.billing_stub_mode:
        return await create_stub_checkout(casdoor_id, payload)

    # Prepare payload with required fields
    full_payload = {
        "service_id": "relay",
        "user_id": casdoor_id,
        "product_id": payload.get("product_id"),
        "price_id": payload.get("price_id"),
        "return_url": settings.billing_return_url or payload.get("return_url", ""),
        "idempotency_key": payload.get("idempotency_key") or str(uuid.uuid4()),
        "metadata": payload.get("metadata", {}),
    }

    try:
        client = _get_billing_client()
        result = await client.create_subscription(full_payload)

        # Store subscription_id on user
        if result.get("id"):
            user.billing_subscription_id = result["id"]
            db.commit()

        return result
    except BillingServiceError:
        logger.exception("Failed to create checkout session")
        raise


async def cancel_user_subscription(casdoor_id: str, subscription_id: str | None) -> dict[str, Any]:
    """Cancel user's active subscription.

    Args:
        casdoor_id: User's Casdoor UUID
        subscription_id: Subscription ID to cancel

    Returns:
        Dict with status and message
    """
    settings = get_settings()
    if settings.billing_stub_mode:
        return await cancel_stub_subscription(casdoor_id)

    # Prefer subscription_id from Billing Service (authoritative)
    try:
        ent_data = await get_entitlements_cached(casdoor_id)
        billing_sub = ent_data.get("subscription") or {}
        fresh_sub_id = billing_sub.get("id") or ent_data.get("subscription_id")
        if fresh_sub_id:
            subscription_id = fresh_sub_id
    except Exception:
        logger.debug("Could not fetch entitlements for subscription_id lookup")

    if not subscription_id:
        raise BillingServiceError(
            code="NO_SUBSCRIPTION",
            message="No active subscription found",
            status=400,
        )

    try:
        client = _get_billing_client()
        result = await client.cancel_subscription(subscription_id)
        # Invalidate cache so next entitlement check fetches fresh data
        invalidate_cache(casdoor_id)
        return result
    except BillingServiceError:
        logger.exception("Failed to cancel subscription")
        raise


async def change_subscription_plan(
    casdoor_id: str,
    subscription_id: str | None,
    product_id: str,
    price_id: str | None = None,
) -> dict[str, Any]:
    """Change plan on an existing subscription.

    Args:
        casdoor_id: User's Casdoor UUID
        subscription_id: Current subscription ID
        product_id: New product to switch to
        price_id: New price ID (optional)

    Returns:
        Dict with updated subscription info from Billing Service
    """
    settings = get_settings()
    if settings.billing_stub_mode:
        return {
            "status": "changed",
            "message": "Plan changed (stub mode)",
            "product_id": product_id,
        }

    # Prefer subscription_id from Billing Service entitlements (authoritative source)
    # over the potentially stale value stored in our DB.
    try:
        ent_data = await get_entitlements_cached(casdoor_id)
        billing_sub = ent_data.get("subscription") or {}
        fresh_sub_id = billing_sub.get("id") or ent_data.get("subscription_id")
        if fresh_sub_id:
            subscription_id = fresh_sub_id
    except Exception:
        logger.debug("Could not fetch entitlements for subscription_id lookup")

    if not subscription_id:
        raise BillingServiceError(
            code="NO_SUBSCRIPTION",
            message="No active subscription to change",
            status=400,
        )

    try:
        client = _get_billing_client()
        result = await client.change_plan(subscription_id, product_id, price_id)
        invalidate_cache(casdoor_id)
        return result
    except BillingServiceError:
        logger.exception("Failed to change subscription plan")
        raise


async def get_usage_warnings(casdoor_id: str, db: Any, user_id: Any) -> list[str]:
    """Get usage warnings for entitlements at >= 80% of limit.

    Args:
        casdoor_id: User's Casdoor UUID
        db: Database session
        user_id: Internal user UUID

    Returns:
        List of warning strings like ['shares:approaching_limit', ...]
    """
    from app.services import usage_service

    warnings = []
    data = await get_entitlements_cached(casdoor_id)
    entitlements = data.get("entitlements", {})

    # Check shares
    max_shares = _get_limit(entitlements, "max_shares")
    if max_shares:
        shares_count = usage_service.count_user_shares(db, user_id)
        if shares_count >= max_shares * 0.8:
            warnings.append("shares:approaching_limit")

    # Check web published
    max_web = _get_limit(entitlements, "max_web_published")
    if max_web:
        web_count = usage_service.count_web_published(db, user_id)
        if web_count >= max_web * 0.8:
            warnings.append("web_published:approaching_limit")

    # Check storage
    max_storage = _get_limit(entitlements, "max_storage_bytes")
    if max_storage:
        storage_bytes = await usage_service.get_user_storage_bytes(db, user_id)
        if storage_bytes is not None and storage_bytes >= max_storage * 0.8:
            warnings.append("storage:approaching_limit")

    # Note: members check would need share_id context, skip for now

    return warnings


async def create_portal_session(casdoor_id: str, return_url: str) -> dict[str, Any]:
    """Create a Stripe Customer Portal session for subscription management.

    Args:
        casdoor_id: User's Casdoor UUID
        return_url: URL to redirect to after portal session

    Returns:
        Dict with portal_url
    """
    settings = get_settings()
    if settings.billing_stub_mode:
        return await create_stub_portal_session(casdoor_id)

    try:
        client = _get_billing_client()
        result = await client.create_portal_session(casdoor_id, return_url)
        return result
    except BillingServiceError:
        logger.exception("Failed to create portal session")
        raise
