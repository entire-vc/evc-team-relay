"""Webhook service for event notifications.

This module handles:
- Webhook CRUD operations
- Webhook delivery with HMAC-SHA256 signing
- Retry logic with exponential backoff
- URL validation
"""

from __future__ import annotations

import hashlib
import hmac
import ipaddress
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING
from urllib.parse import urlparse

import httpx
from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.logging import get_logger
from app.db.models import Webhook, WebhookDelivery, WebhookDeliveryStatus

if TYPE_CHECKING:
    pass

logger = get_logger(__name__)

# Event types that can be subscribed to
VALID_EVENT_TYPES = {
    # Share events
    "share.created",
    "share.updated",
    "share.deleted",
    # Member events
    "share.member.added",
    "share.member.updated",
    "share.member.removed",
    # Invite events
    "invite.created",
    "invite.redeemed",
    "invite.revoked",
    # Auth events
    "user.login",
    "user.logout",
    "user.password_reset",
    # Session events
    "session.created",
    "session.revoked",
    # OAuth events
    "oauth.login",
    "oauth.account.linked",
    # 2FA events
    "totp.enabled",
    "totp.disabled",
    # Admin events (admin webhooks only)
    "user.created",
    "user.updated",
    "user.deleted",
}

# Admin-only event types
ADMIN_ONLY_EVENTS = {
    "user.created",
    "user.updated",
    "user.deleted",
}

# Retry intervals in seconds (exponential backoff)
RETRY_INTERVALS = [60, 300, 900, 3600, 21600, 86400]  # 1m, 5m, 15m, 1h, 6h, 24h
MAX_RETRIES = len(RETRY_INTERVALS)

# Webhook delivery timeout
DELIVERY_TIMEOUT = 10.0

# Max consecutive failures before auto-disable
MAX_CONSECUTIVE_FAILURES = 10


def generate_webhook_secret() -> str:
    """Generate a cryptographically secure webhook secret.

    Returns:
        64-character hex string (32 bytes / 256 bits)
    """
    return secrets.token_hex(32)


def validate_webhook_url(url: str, allow_localhost: bool = False) -> tuple[bool, str | None]:
    """Validate webhook URL for security.

    Args:
        url: URL to validate
        allow_localhost: Whether to allow localhost/private IPs (for development)

    Returns:
        Tuple of (is_valid, error_message)
    """
    try:
        parsed = urlparse(url)

        # Must be HTTPS in production
        if not allow_localhost and parsed.scheme != "https":
            return False, "Webhook URL must use HTTPS"

        if parsed.scheme not in ("http", "https"):
            return False, "Invalid URL scheme"

        if not parsed.netloc:
            return False, "Invalid URL: missing host"

        # Extract host (without port)
        host = parsed.hostname
        if not host:
            return False, "Invalid URL: missing hostname"

        # Check for private/localhost IPs in production
        if not allow_localhost:
            try:
                ip = ipaddress.ip_address(host)
                if ip.is_private or ip.is_loopback or ip.is_reserved:
                    return False, "Webhook URL cannot point to private or localhost addresses"
            except ValueError:
                # Not an IP address, could be a hostname
                if host in ("localhost", "127.0.0.1", "::1"):
                    return False, "Webhook URL cannot point to localhost"
                # Check for common internal hostnames
                if host.endswith(".local") or host.endswith(".internal"):
                    return False, "Webhook URL cannot point to internal addresses"

        return True, None

    except Exception as e:
        return False, f"Invalid URL: {e}"


def validate_event_types(events: list[str], is_admin: bool = False) -> tuple[bool, str | None]:
    """Validate event types list.

    Args:
        events: List of event types to validate
        is_admin: Whether this is an admin webhook

    Returns:
        Tuple of (is_valid, error_message)
    """
    if not events:
        return False, "At least one event type is required"

    for event in events:
        if event not in VALID_EVENT_TYPES:
            return False, f"Invalid event type: {event}"

        if event in ADMIN_ONLY_EVENTS and not is_admin:
            return False, f"Event type '{event}' is only available for admin webhooks"

    return True, None


def create_webhook(
    db: Session,
    user_id: uuid.UUID | None,
    name: str,
    url: str,
    events: list[str],
    secret: str | None = None,
) -> Webhook:
    """Create a new webhook.

    Args:
        db: Database session
        user_id: User ID (None for admin/global webhooks)
        name: Webhook name
        url: Webhook URL
        events: List of event types to subscribe to
        secret: Optional secret (auto-generated if not provided)

    Returns:
        Created Webhook instance

    Raises:
        HTTPException: If validation fails
    """
    is_admin = user_id is None

    # Validate URL
    allow_localhost = getattr(get_settings(), "DEBUG", False)
    is_valid, error = validate_webhook_url(url, allow_localhost=allow_localhost)
    if not is_valid:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=error)

    # Validate events
    is_valid, error = validate_event_types(events, is_admin=is_admin)
    if not is_valid:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=error)

    # Generate secret if not provided
    if not secret:
        secret = generate_webhook_secret()

    webhook = Webhook(
        user_id=user_id,
        name=name,
        url=url,
        secret=secret,
        events=events,
        active=True,
        failure_count=0,
    )

    db.add(webhook)
    db.commit()
    db.refresh(webhook)

    logger.info(
        "Webhook created",
        extra={
            "webhook_id": str(webhook.id),
            "user_id": str(user_id) if user_id else "admin",
            "events": events,
        },
    )

    return webhook


def get_webhook(db: Session, webhook_id: uuid.UUID) -> Webhook | None:
    """Get webhook by ID.

    Args:
        db: Database session
        webhook_id: Webhook ID

    Returns:
        Webhook instance or None
    """
    return db.execute(select(Webhook).where(Webhook.id == webhook_id)).scalar_one_or_none()


def get_webhook_with_stats(db: Session, webhook_id: uuid.UUID) -> dict | None:
    """Get webhook with delivery statistics.

    Args:
        db: Database session
        webhook_id: Webhook ID

    Returns:
        Dict with webhook data and stats, or None
    """
    webhook = get_webhook(db, webhook_id)
    if not webhook:
        return None

    # Get delivery stats
    stats = db.execute(
        select(
            func.count(WebhookDelivery.id).label("total"),
            func.sum(
                func.cast(
                    WebhookDelivery.status == WebhookDeliveryStatus.SUCCESS,
                    db.bind.dialect.name == "postgresql" and "INTEGER" or None,
                )
            ).label("success"),
            func.sum(
                func.cast(
                    WebhookDelivery.status.in_(
                        [WebhookDeliveryStatus.FAILED, WebhookDeliveryStatus.MAX_RETRIES_EXCEEDED]
                    ),
                    db.bind.dialect.name == "postgresql" and "INTEGER" or None,
                )
            ).label("failed"),
            func.max(WebhookDelivery.updated_at).label("last_delivery_at"),
        ).where(WebhookDelivery.webhook_id == webhook_id)
    ).first()

    return {
        "webhook": webhook,
        "stats": {
            "total_deliveries": stats.total or 0 if stats else 0,
            "successful_deliveries": stats.success or 0 if stats else 0,
            "failed_deliveries": stats.failed or 0 if stats else 0,
            "last_delivery_at": stats.last_delivery_at if stats else None,
        },
    }


def list_webhooks(
    db: Session,
    user_id: uuid.UUID | None = None,
    include_admin: bool = False,
) -> list[Webhook]:
    """List webhooks.

    Args:
        db: Database session
        user_id: Filter by user ID (None for all)
        include_admin: Include admin webhooks (user_id is NULL)

    Returns:
        List of Webhook instances
    """
    stmt = select(Webhook)

    if user_id is not None:
        if include_admin:
            stmt = stmt.where((Webhook.user_id == user_id) | (Webhook.user_id.is_(None)))
        else:
            stmt = stmt.where(Webhook.user_id == user_id)
    elif not include_admin:
        stmt = stmt.where(Webhook.user_id.is_not(None))

    stmt = stmt.order_by(Webhook.created_at.desc())
    return list(db.execute(stmt).scalars().all())


def update_webhook(
    db: Session,
    webhook_id: uuid.UUID,
    name: str | None = None,
    url: str | None = None,
    events: list[str] | None = None,
    active: bool | None = None,
) -> Webhook:
    """Update webhook.

    Args:
        db: Database session
        webhook_id: Webhook ID
        name: New name (optional)
        url: New URL (optional)
        events: New events list (optional)
        active: New active status (optional)

    Returns:
        Updated Webhook instance

    Raises:
        HTTPException: If webhook not found or validation fails
    """
    webhook = get_webhook(db, webhook_id)
    if not webhook:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Webhook not found")

    is_admin = webhook.user_id is None

    if url is not None:
        allow_localhost = getattr(get_settings(), "DEBUG", False)
        is_valid, error = validate_webhook_url(url, allow_localhost=allow_localhost)
        if not is_valid:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=error)
        webhook.url = url

    if events is not None:
        is_valid, error = validate_event_types(events, is_admin=is_admin)
        if not is_valid:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=error)
        webhook.events = events

    if name is not None:
        webhook.name = name

    if active is not None:
        webhook.active = active
        # Reset failure count when re-enabling
        if active:
            webhook.failure_count = 0

    db.add(webhook)
    db.commit()
    db.refresh(webhook)

    logger.info("Webhook updated", extra={"webhook_id": str(webhook_id)})

    return webhook


def delete_webhook(db: Session, webhook_id: uuid.UUID) -> bool:
    """Delete webhook.

    Args:
        db: Database session
        webhook_id: Webhook ID

    Returns:
        True if deleted

    Raises:
        HTTPException: If webhook not found
    """
    webhook = get_webhook(db, webhook_id)
    if not webhook:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Webhook not found")

    db.delete(webhook)
    db.commit()

    logger.info("Webhook deleted", extra={"webhook_id": str(webhook_id)})

    return True


def rotate_webhook_secret(db: Session, webhook_id: uuid.UUID) -> str:
    """Rotate webhook secret.

    Args:
        db: Database session
        webhook_id: Webhook ID

    Returns:
        New secret

    Raises:
        HTTPException: If webhook not found
    """
    webhook = get_webhook(db, webhook_id)
    if not webhook:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Webhook not found")

    new_secret = generate_webhook_secret()
    webhook.secret = new_secret

    db.add(webhook)
    db.commit()

    logger.info("Webhook secret rotated", extra={"webhook_id": str(webhook_id)})

    return new_secret


def compute_signature(payload: bytes, secret: str) -> str:
    """Compute HMAC-SHA256 signature for webhook payload.

    Args:
        payload: Request body bytes
        secret: Webhook secret

    Returns:
        Signature string in format "sha256=<hex>"
    """
    mac = hmac.new(secret.encode(), payload, hashlib.sha256)
    return f"sha256={mac.hexdigest()}"


def find_matching_webhooks(
    db: Session,
    event_type: str,
    user_id: uuid.UUID | None = None,
) -> list[Webhook]:
    """Find webhooks that should receive an event.

    Args:
        db: Database session
        event_type: Event type
        user_id: User ID for user-scoped webhooks

    Returns:
        List of matching Webhook instances
    """
    import json

    from sqlalchemy import cast
    from sqlalchemy.dialects.postgresql import JSONB

    # Detect database dialect
    dialect_name = db.get_bind().dialect.name

    if dialect_name == "postgresql":
        # For PostgreSQL, use the @> operator for JSONB containment check
        # The events column stores a JSON array like ["event1", "event2"]
        # We check if it contains our event_type
        # Cast the value to JSONB for proper comparison
        stmt = select(Webhook).where(
            Webhook.active == True,  # noqa: E712
            Webhook.events.op("@>")(cast(json.dumps([event_type]), JSONB)),
        )

        # Include both user's webhooks and admin webhooks
        if user_id is not None:
            stmt = stmt.where((Webhook.user_id == user_id) | (Webhook.user_id.is_(None)))
        else:
            # Only admin webhooks
            stmt = stmt.where(Webhook.user_id.is_(None))

        return list(db.execute(stmt).scalars().all())
    else:
        # For SQLite and other databases, fetch all active webhooks and filter in Python
        stmt = select(Webhook).where(Webhook.active == True)  # noqa: E712

        # Include both user's webhooks and admin webhooks
        if user_id is not None:
            stmt = stmt.where((Webhook.user_id == user_id) | (Webhook.user_id.is_(None)))
        else:
            # Only admin webhooks
            stmt = stmt.where(Webhook.user_id.is_(None))

        all_webhooks = list(db.execute(stmt).scalars().all())

        # Filter in Python to find webhooks subscribed to this event
        return [wh for wh in all_webhooks if event_type in (wh.events or [])]


def queue_webhook_delivery(
    db: Session,
    webhook: Webhook,
    event_type: str,
    payload: dict,
    event_id: uuid.UUID | None = None,
) -> WebhookDelivery:
    """Queue a webhook delivery.

    Args:
        db: Database session
        webhook: Webhook to deliver to
        event_type: Event type
        payload: Event payload
        event_id: Unique event ID (auto-generated if not provided)

    Returns:
        Created WebhookDelivery instance
    """
    if event_id is None:
        event_id = uuid.uuid4()

    delivery = WebhookDelivery(
        webhook_id=webhook.id,
        event_id=event_id,
        event_type=event_type,
        payload=payload,
        status=WebhookDeliveryStatus.PENDING,
        attempt_count=0,
        next_retry_at=datetime.now(timezone.utc),  # Immediate delivery
    )

    db.add(delivery)
    db.commit()
    db.refresh(delivery)

    logger.info(
        "Webhook delivery queued",
        extra={
            "delivery_id": str(delivery.id),
            "webhook_id": str(webhook.id),
            "event_type": event_type,
            "event_id": str(event_id),
        },
    )

    return delivery


async def deliver_webhook(
    db: Session,
    delivery: WebhookDelivery,
) -> bool:
    """Attempt to deliver a webhook.

    Args:
        db: Database session
        delivery: WebhookDelivery instance

    Returns:
        True if successful, False otherwise
    """
    webhook = delivery.webhook
    if not webhook:
        logger.warning(
            "Webhook not found for delivery",
            extra={"delivery_id": str(delivery.id)},
        )
        delivery.status = WebhookDeliveryStatus.FAILED
        db.add(delivery)
        db.commit()
        return False

    # Prepare payload
    import json

    payload_bytes = json.dumps(delivery.payload).encode("utf-8")
    signature = compute_signature(payload_bytes, webhook.secret)

    headers = {
        "Content-Type": "application/json",
        "User-Agent": "RelayOnPrem-Webhooks/1.4.0",
        "X-Relay-Event": delivery.event_type,
        "X-Relay-Delivery": str(delivery.id),
        "X-Relay-Signature": signature,
    }

    delivery.attempt_count += 1

    try:
        async with httpx.AsyncClient(timeout=DELIVERY_TIMEOUT) as client:
            response = await client.post(webhook.url, content=payload_bytes, headers=headers)

        delivery.response_status_code = response.status_code
        # Truncate response body to 1KB
        delivery.response_body = response.text[:1024] if response.text else None

        if 200 <= response.status_code < 300:
            # Success
            delivery.status = WebhookDeliveryStatus.SUCCESS
            delivery.next_retry_at = None

            # Reset failure count on success
            webhook.failure_count = 0
            db.add(webhook)

            logger.info(
                "Webhook delivered successfully",
                extra={
                    "delivery_id": str(delivery.id),
                    "webhook_id": str(webhook.id),
                    "status_code": response.status_code,
                    "attempt": delivery.attempt_count,
                },
            )
            db.add(delivery)
            db.commit()
            return True

        elif response.status_code == 429 or response.status_code >= 500:
            # Temporary failure, schedule retry
            _schedule_retry(db, delivery, webhook)
            return False

        else:
            # Permanent failure (4xx except 429)
            delivery.status = WebhookDeliveryStatus.FAILED
            delivery.next_retry_at = None
            _increment_failure_count(db, webhook)

            logger.warning(
                "Webhook delivery failed permanently",
                extra={
                    "delivery_id": str(delivery.id),
                    "webhook_id": str(webhook.id),
                    "status_code": response.status_code,
                },
            )
            db.add(delivery)
            db.commit()
            return False

    except httpx.TimeoutException:
        delivery.response_body = "Request timed out"
        _schedule_retry(db, delivery, webhook)
        logger.warning(
            "Webhook delivery timed out",
            extra={
                "delivery_id": str(delivery.id),
                "webhook_id": str(webhook.id),
            },
        )
        return False

    except httpx.RequestError as e:
        delivery.response_body = str(e)[:1024]
        _schedule_retry(db, delivery, webhook)
        logger.warning(
            "Webhook delivery request error",
            extra={
                "delivery_id": str(delivery.id),
                "webhook_id": str(webhook.id),
                "error": str(e),
            },
        )
        return False


def _schedule_retry(db: Session, delivery: WebhookDelivery, webhook: Webhook) -> None:
    """Schedule a retry for failed delivery.

    Args:
        db: Database session
        delivery: WebhookDelivery instance
        webhook: Webhook instance
    """
    if delivery.attempt_count >= MAX_RETRIES:
        delivery.status = WebhookDeliveryStatus.MAX_RETRIES_EXCEEDED
        delivery.next_retry_at = None
        _increment_failure_count(db, webhook)

        logger.warning(
            "Webhook delivery max retries exceeded",
            extra={
                "delivery_id": str(delivery.id),
                "webhook_id": str(webhook.id),
                "attempts": delivery.attempt_count,
            },
        )
    else:
        retry_interval = RETRY_INTERVALS[delivery.attempt_count - 1]
        delivery.next_retry_at = datetime.now(timezone.utc) + timedelta(seconds=retry_interval)

        logger.info(
            "Webhook delivery scheduled for retry",
            extra={
                "delivery_id": str(delivery.id),
                "webhook_id": str(webhook.id),
                "attempt": delivery.attempt_count,
                "retry_in_seconds": retry_interval,
            },
        )

    db.add(delivery)
    db.commit()


def _increment_failure_count(db: Session, webhook: Webhook) -> None:
    """Increment webhook failure count and auto-disable if threshold exceeded.

    Args:
        db: Database session
        webhook: Webhook instance
    """
    webhook.failure_count += 1

    if webhook.failure_count >= MAX_CONSECUTIVE_FAILURES:
        webhook.active = False
        logger.warning(
            "Webhook auto-disabled due to consecutive failures",
            extra={
                "webhook_id": str(webhook.id),
                "failure_count": webhook.failure_count,
            },
        )

    db.add(webhook)
    db.commit()


def get_pending_deliveries(
    db: Session,
    limit: int = 100,
) -> list[WebhookDelivery]:
    """Get pending webhook deliveries ready for processing.

    Args:
        db: Database session
        limit: Maximum number of deliveries to return

    Returns:
        List of WebhookDelivery instances ready for delivery
    """
    now = datetime.now(timezone.utc)

    stmt = (
        select(WebhookDelivery)
        .where(
            WebhookDelivery.status == WebhookDeliveryStatus.PENDING,
            WebhookDelivery.next_retry_at <= now,
        )
        .order_by(WebhookDelivery.next_retry_at)
        .limit(limit)
    )

    return list(db.execute(stmt).scalars().all())


def list_deliveries(
    db: Session,
    webhook_id: uuid.UUID | None = None,
    status_filter: WebhookDeliveryStatus | None = None,
    event_type: str | None = None,
    skip: int = 0,
    limit: int = 50,
) -> list[WebhookDelivery]:
    """List webhook deliveries with optional filters.

    Args:
        db: Database session
        webhook_id: Filter by webhook ID
        status_filter: Filter by status
        event_type: Filter by event type
        skip: Number of records to skip
        limit: Maximum number of records to return

    Returns:
        List of WebhookDelivery instances
    """
    stmt = select(WebhookDelivery)

    if webhook_id:
        stmt = stmt.where(WebhookDelivery.webhook_id == webhook_id)
    if status_filter:
        stmt = stmt.where(WebhookDelivery.status == status_filter)
    if event_type:
        stmt = stmt.where(WebhookDelivery.event_type == event_type)

    stmt = stmt.order_by(WebhookDelivery.created_at.desc()).offset(skip).limit(limit)

    return list(db.execute(stmt).scalars().all())


async def send_test_event(db: Session, webhook_id: uuid.UUID) -> WebhookDelivery:
    """Send a test ping event to a webhook.

    Args:
        db: Database session
        webhook_id: Webhook ID

    Returns:
        WebhookDelivery instance with result

    Raises:
        HTTPException: If webhook not found
    """
    webhook = get_webhook(db, webhook_id)
    if not webhook:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Webhook not found")

    event_id = uuid.uuid4()
    payload = {
        "event_id": str(event_id),
        "event_type": "ping",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "data": {
            "message": "This is a test event from Relay OnPrem",
            "webhook_id": str(webhook_id),
            "webhook_name": webhook.name,
        },
    }

    delivery = queue_webhook_delivery(db, webhook, "ping", payload, event_id)

    # Attempt immediate delivery
    await deliver_webhook(db, delivery)

    # Refresh to get updated status
    db.refresh(delivery)

    return delivery
