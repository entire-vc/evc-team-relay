"""Async httpx client for Billing Service API."""

from __future__ import annotations

import time
from collections.abc import Generator
from typing import Any

import httpx
import jwt
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import load_pem_private_key

from app.core.logging import get_logger

logger = get_logger(__name__)


class BillingServiceError(Exception):
    """Error from Billing Service API."""

    # Human-readable fallback messages for common HTTP status codes
    _STATUS_MESSAGES: dict[int, str] = {
        400: "Invalid request",
        401: "Billing authentication failed",
        403: "Billing access denied",
        404: "Billing resource not found",
        409: "Active subscription already exists",
        422: "Invalid billing data",
        429: "Too many billing requests, try again later",
    }

    def __init__(self, code: str, message: str, status: int, details: dict | None = None):
        self.code = code
        self.message = message
        self.status = status
        self.details = details or {}
        super().__init__(f"[{code}] {message}")


def _parse_billing_error(e: httpx.HTTPStatusError) -> BillingServiceError:
    """Parse httpx error into BillingServiceError with clean messages."""
    try:
        body = e.response.json() if e.response.content else {}
    except Exception:
        body = {}

    # Log raw response for debugging
    logger.debug("Billing API error response (status=%s): %s", e.response.status_code, body)

    # Support both {"error": {"code": ..., "message": ...}} and FastAPI {"detail": ...} formats
    err = body.get("error", {})
    code = err.get("code", "UNKNOWN")

    # Try multiple message sources: error.message, detail string, detail[0].msg
    message = err.get("message")
    if not message:
        detail = body.get("detail")
        if isinstance(detail, str):
            message = detail
        elif isinstance(detail, list) and detail:
            # FastAPI validation error format: [{"msg": "...", "loc": [...]}]
            message = detail[0].get("msg", "")

    message = message or BillingServiceError._STATUS_MESSAGES.get(
        e.response.status_code, f"Billing error ({e.response.status_code})"
    )
    return BillingServiceError(
        code=code,
        message=message,
        status=e.response.status_code,
        details=err.get("details"),
    )


SERVICE_JWT_AUDIENCE = "evc-billing"
# Billing rejects tokens with exp - iat > 120s; stay well under it.
SERVICE_JWT_LIFETIME_SECONDS = 60


def load_signing_key(pem: str) -> Ed25519PrivateKey:
    """Parse an Ed25519 private key from PEM (literal newlines or ``\\n`` escapes)."""
    key = load_pem_private_key(pem.replace("\\n", "\n").encode(), password=None)
    if not isinstance(key, Ed25519PrivateKey):
        raise ValueError("billing signing key must be an Ed25519 private key")
    return key


class ScopedJWTAuth(httpx.Auth):
    """Signs a fresh short-lived EdDSA JWT per request (iss=service_id, aud=evc-billing)."""

    def __init__(self, private_key: Ed25519PrivateKey, service_id: str, kid: str):
        self._key = private_key
        self._service_id = service_id
        self._kid = kid

    def make_token(self) -> str:
        now = int(time.time())
        return jwt.encode(
            {
                "iss": self._service_id,
                "aud": SERVICE_JWT_AUDIENCE,
                "iat": now,
                "exp": now + SERVICE_JWT_LIFETIME_SECONDS,
            },
            self._key,
            algorithm="EdDSA",
            headers={"kid": self._kid},
        )

    def auth_flow(self, request: httpx.Request) -> Generator[httpx.Request, httpx.Response, None]:
        request.headers["Authorization"] = f"Bearer {self.make_token()}"
        yield request


class BillingClient:
    """Async client for Billing Service API.

    Authenticates with a scoped EdDSA JWT when ``signing_key_pem`` + ``signing_key_id``
    are given, otherwise with the legacy shared ``service_token``.
    """

    def __init__(
        self,
        base_url: str,
        service_token: str = "",
        service_id: str = "relay",
        signing_key_pem: str = "",
        signing_key_id: str = "",
    ):
        self._service_id = service_id
        auth: httpx.Auth | None = None
        headers = {
            "X-Service-Id": service_id,
            "Content-Type": "application/json",
        }
        if signing_key_pem and signing_key_id:
            auth = ScopedJWTAuth(load_signing_key(signing_key_pem), service_id, signing_key_id)
        else:
            headers["Authorization"] = f"Bearer {service_token}"
        self._client = httpx.AsyncClient(
            base_url=base_url,
            auth=auth,
            headers={
                **headers,
            },
            timeout=httpx.Timeout(connect=5.0, read=30.0, write=10.0, pool=5.0),
            limits=httpx.Limits(max_keepalive_connections=0),
        )

    async def get_entitlements(self, user_id: str) -> dict[str, Any]:
        """Get user entitlements from Billing Service."""
        try:
            resp = await self._client.get(f"/entitlements/{user_id}/{self._service_id}")
            resp.raise_for_status()
            return resp.json()
        except httpx.HTTPStatusError as e:
            raise _parse_billing_error(e) from e

    async def get_products(self) -> dict[str, Any]:
        """Get available products/plans from Billing Service."""
        try:
            resp = await self._client.get(
                "/products",
                params={"service_id": self._service_id},
            )
            resp.raise_for_status()
            return resp.json()
        except httpx.HTTPStatusError as e:
            raise _parse_billing_error(e) from e

    async def create_subscription(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Create a subscription/checkout session for a user.

        Args:
            payload: Dict with service_id, product_id, price_id, return_url,
                    idempotency_key, metadata (optional)

        Returns:
            Dict with checkout_url and subscription_id
        """
        try:
            resp = await self._client.post("/subscriptions", json=payload)
            resp.raise_for_status()
            return resp.json()
        except httpx.HTTPStatusError as e:
            raise _parse_billing_error(e) from e

    async def change_plan(
        self,
        subscription_id: str,
        product_id: str,
        price_id: str | None = None,
    ) -> dict[str, Any]:
        """Change plan on an existing subscription.

        Args:
            subscription_id: Current subscription ID
            product_id: New product to switch to
            price_id: New price ID (optional, Billing Service picks default)

        Returns:
            Dict with updated subscription info
        """
        payload: dict[str, Any] = {
            "new_product_id": product_id,
            "new_price_id": price_id or "",
        }
        try:
            resp = await self._client.post(
                f"/subscriptions/{subscription_id}/change-plan",
                json=payload,
            )
            resp.raise_for_status()
            return resp.json()
        except httpx.HTTPStatusError as e:
            raise _parse_billing_error(e) from e

    async def cancel_subscription(self, subscription_id: str) -> dict[str, Any]:
        """Cancel active subscription.

        Args:
            subscription_id: Subscription ID to cancel

        Returns:
            Dict with status and message
        """
        try:
            resp = await self._client.post(f"/subscriptions/{subscription_id}/cancel")
            resp.raise_for_status()
            return resp.json()
        except httpx.HTTPStatusError as e:
            raise _parse_billing_error(e) from e

    async def create_portal_session(self, user_id: str, return_url: str) -> dict[str, Any]:
        """Create a Stripe Customer Portal session for a user.

        Args:
            user_id: Casdoor user ID
            return_url: URL to return to after portal session

        Returns:
            Dict with portal_url
        """
        try:
            resp = await self._client.post(
                "/portal-sessions",
                json={
                    "user_id": user_id,
                    "service_id": self._service_id,
                    "return_url": return_url,
                },
            )
            resp.raise_for_status()
            return resp.json()
        except httpx.HTTPStatusError as e:
            raise _parse_billing_error(e) from e

    async def close(self) -> None:
        """Close the underlying HTTP client."""
        await self._client.aclose()
