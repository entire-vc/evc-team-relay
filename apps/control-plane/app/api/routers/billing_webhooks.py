"""Billing Service webhook receiver."""

from __future__ import annotations

import hashlib
import hmac
import time

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.logging import get_logger
from app.db import models
from app.db.session import get_db
from app.services import audit_service, billing_service

logger = get_logger(__name__)

router = APIRouter(prefix="/v1/billing", tags=["billing"])


def _verify_webhook_signature(body: bytes, signature: str, timestamp: str) -> bool:
    """Verify HMAC-SHA256 webhook signature."""
    settings = get_settings()
    secret = settings.billing_webhook_secret
    if not secret:
        logger.warning("Billing webhook secret not configured, rejecting webhook")
        return False

    # Check timestamp freshness (reject if older than 5 minutes)
    try:
        ts = int(timestamp)
        if abs(time.time() - ts) > 300:
            return False
    except (ValueError, TypeError):
        return False

    # Compute expected signature
    message = f"{timestamp}.{body.decode('utf-8')}"
    expected = hmac.new(
        secret.encode("utf-8"),
        message.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()

    return hmac.compare_digest(expected, signature)


_EVENT_TO_AUDIT: dict[str, models.AuditAction | None] = {
    "subscription.created": models.AuditAction.BILLING_SUBSCRIPTION_CREATED,
    "subscription.updated": models.AuditAction.BILLING_SUBSCRIPTION_UPDATED,
    "subscription.renewed": models.AuditAction.BILLING_SUBSCRIPTION_UPDATED,
    "subscription.cancelled": models.AuditAction.BILLING_SUBSCRIPTION_CANCELLED,
    "subscription.expired": models.AuditAction.BILLING_SUBSCRIPTION_CANCELLED,
    "subscription.activated": models.AuditAction.BILLING_SUBSCRIPTION_ACTIVATED,
    "subscription.payment_failed": models.AuditAction.BILLING_PAYMENT_FAILED,
    "payment.succeeded": None,  # Acknowledge without audit log
}


@router.post("/webhooks", status_code=status.HTTP_200_OK)
async def billing_webhook(
    request: Request,
    db: Session = Depends(get_db),
):
    """Receive billing webhooks from Billing Service.

    Verifies HMAC signature, deduplicates events, invalidates entitlements cache,
    and creates audit log entries.
    """
    settings = get_settings()
    if not settings.billing_enabled:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Billing is not enabled",
        )

    # Get headers
    signature = request.headers.get("X-Webhook-Signature", "")
    timestamp = request.headers.get("X-Webhook-Timestamp", "")
    event_id = request.headers.get("X-Webhook-Id", "")

    if not event_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Missing X-Webhook-Id header",
        )

    # Read body
    body = await request.body()

    # Verify signature
    if not _verify_webhook_signature(body, signature, timestamp):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid webhook signature",
        )

    # Dedup check
    existing = db.execute(
        select(models.BillingWebhookEvent).where(models.BillingWebhookEvent.event_id == event_id)
    ).scalar_one_or_none()

    if existing:
        logger.info("Duplicate webhook event, skipping", extra={"event_id": event_id})
        return {"status": "ok", "duplicate": True}

    # Parse payload
    import json

    payload = json.loads(body)
    event_type = payload.get("event", "")
    data = payload.get("data", {})
    user_id = data.get("user_id", "")
    subscription_id = data.get("subscription_id", "")

    # C-08: Use canonical event_id from header only (body ID ignored for dedup)
    # If body has a different ID, log warning but use header ID consistently

    # Invalidate entitlements cache
    if user_id:
        billing_service.invalidate_cache(user_id)

    # Store subscription_id on user if provided. user_id here is whatever
    # billing_service.get_billing_identity() sent when the subscription was
    # created — casdoor_id, an OAuth provider_user_id, or (rarely) the
    # internal user.id — so the lookup must try all three forms, the same
    # way find_user_by_billing_identity() does, not just casdoor_id (which
    # no code path ever writes — see get_billing_identity()'s docstring).
    if user_id and subscription_id:
        user = billing_service.find_user_by_billing_identity(db, user_id)
        if user:
            user.billing_subscription_id = subscription_id
            db.commit()
        else:
            logger.warning(
                "Billing webhook: no user found for billing identity, subscription_id not stored",
                extra={
                    "event_id": event_id,
                    "user_id": user_id,
                    "subscription_id": subscription_id,
                },
            )

    # Create audit log entry
    audit_action = _EVENT_TO_AUDIT.get(event_type)
    if audit_action:
        audit_service.log_action(
            db=db,
            action=audit_action,
            details={
                "event_id": event_id,
                "event_type": event_type,
                "user_id": user_id,
                "subscription_id": subscription_id,
            },
        )

    # Record processed event (dedup) — always use canonical header event_id
    webhook_event = models.BillingWebhookEvent(
        event_id=event_id,
        event_type=event_type,
    )
    db.add(webhook_event)
    db.commit()

    logger.info(
        "Processed billing webhook",
        extra={"event_id": event_id, "event_type": event_type, "user_id": user_id},
    )

    return {"status": "ok"}
