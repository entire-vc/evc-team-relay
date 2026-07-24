"""Local billing stub for development when Billing Service is not available."""

from __future__ import annotations

from typing import Any

STUB_PLANS: dict[str, dict[str, Any]] = {
    "free": {
        "product_id": "prod_relay_free",
        "name": "Relay Free",
        "description": "For personal use",
        "price": None,
        "entitlements": {
            "max_shares": 3,
            "max_members_per_share": 3,
            "max_web_published": 1,
            "max_storage_bytes": 524288000,  # 500 MB
            "allowed_web_visibility": ["public"],
        },
    },
    "builder": {
        "product_id": "prod_relay_builder",
        "name": "Relay Builder",
        "description": "For creators and teams",
        "price": {"amount": 900, "currency": "USD", "period": "month"},
        "entitlements": {
            "max_shares": 10,
            "max_members_per_share": 10,
            "max_web_published": 5,
            "max_storage_bytes": 3221225472,  # 3 GB
            "allowed_web_visibility": ["public", "private"],
        },
    },
}


def _format_entitlement_value(value: Any) -> dict[str, Any]:
    """Format a raw entitlement value into the API response format.

    - list values → {"allowed": [...]}
    - numeric/None values → {"limit": N}
    """
    if isinstance(value, list):
        return {"allowed": value}
    return {"limit": value}


async def get_stub_entitlements(casdoor_id: str) -> dict[str, Any]:
    """Return Free plan entitlements for all users in stub mode."""
    free_plan = STUB_PLANS["free"]
    return {
        "plan": free_plan["name"],
        "subscription": None,
        "entitlements": {
            k: _format_entitlement_value(v) for k, v in free_plan["entitlements"].items()
        },
    }


async def get_stub_plans() -> list[dict[str, Any]]:
    """Return all available plans in stub mode."""
    return [
        {
            "product_id": plan["product_id"],
            "name": plan["name"],
            "description": plan["description"],
            "price": plan["price"],
            "entitlements": plan["entitlements"],
        }
        for plan in STUB_PLANS.values()
    ]


async def create_stub_checkout(casdoor_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Return stub checkout response (upgrade not available in stub mode)."""
    return {
        "checkout_url": "#stub-checkout-not-available",
        "subscription_id": None,
        "message": "Billing is in stub mode. Upgrade not available.",
    }


async def cancel_stub_subscription(casdoor_id: str) -> dict[str, Any]:
    """Return stub cancel response (cancellation not available in stub mode)."""
    return {
        "status": "not_available",
        "message": "Billing is in stub mode. Cancellation not available.",
    }


async def create_stub_portal_session(casdoor_id: str) -> dict[str, Any]:
    """Return stub portal session response."""
    return {
        "url": None,
        "message": "Billing is in stub mode. Portal not available.",
    }
