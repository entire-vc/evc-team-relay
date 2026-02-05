"""Webhook API endpoints."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, status
from slowapi import Limiter
from slowapi.util import get_remote_address
from sqlalchemy.orm import Session

from app.api import deps
from app.db import models
from app.db.models import WebhookDeliveryStatus
from app.db.session import get_db
from app.schemas import webhook as webhook_schema
from app.services import audit_service, webhook_service

router = APIRouter(prefix="/v1/webhooks", tags=["webhooks"])
admin_router = APIRouter(prefix="/v1/admin/webhooks", tags=["admin", "webhooks"])
limiter = Limiter(key_func=get_remote_address)


@router.get("/event-types", response_model=webhook_schema.EventTypesResponse)
def list_event_types():
    """List available webhook event types."""
    return webhook_schema.EventTypesResponse(
        event_types=sorted(webhook_service.VALID_EVENT_TYPES),
        admin_only_events=sorted(webhook_service.ADMIN_ONLY_EVENTS),
    )


@router.post(
    "",
    response_model=webhook_schema.WebhookWithSecret,
    status_code=status.HTTP_201_CREATED,
)
@limiter.limit("10/hour")
def create_webhook(
    request: Request,
    payload: webhook_schema.WebhookCreate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(deps.get_current_user),
):
    """Create a new webhook.

    Rate limited to 10 webhooks per hour per user.
    """
    webhook = webhook_service.create_webhook(
        db=db,
        user_id=current_user.id,
        name=payload.name,
        url=payload.url,
        events=payload.events,
        secret=payload.secret,
    )

    # Log audit
    audit_service.log_action(
        db=db,
        action=models.AuditAction.USER_UPDATED,  # We'll add WEBHOOK_CREATED later
        actor_user_id=current_user.id,
        details={
            "action": "webhook_created",
            "webhook_id": str(webhook.id),
            "webhook_name": webhook.name,
            "events": webhook.events,
        },
    )

    return webhook_schema.WebhookWithSecret(
        id=webhook.id,
        user_id=webhook.user_id,
        name=webhook.name,
        url=webhook.url,
        events=webhook.events,
        active=webhook.active,
        failure_count=webhook.failure_count,
        created_at=webhook.created_at,
        updated_at=webhook.updated_at,
        secret=webhook.secret,
    )


@router.get("", response_model=list[webhook_schema.WebhookRead])
def list_webhooks(
    db: Session = Depends(get_db),
    current_user: models.User = Depends(deps.get_current_user),
):
    """List user's webhooks."""
    webhooks = webhook_service.list_webhooks(db, user_id=current_user.id)
    return webhooks


@router.get("/{webhook_id}", response_model=webhook_schema.WebhookWithStats)
def get_webhook(
    webhook_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(deps.get_current_user),
):
    """Get webhook details with delivery statistics."""
    result = webhook_service.get_webhook_with_stats(db, webhook_id)

    if not result:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Webhook not found")

    webhook = result["webhook"]

    # Check ownership (unless admin)
    if webhook.user_id != current_user.id and not current_user.is_admin:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Webhook not found")

    return webhook_schema.WebhookWithStats(
        id=webhook.id,
        user_id=webhook.user_id,
        name=webhook.name,
        url=webhook.url,
        events=webhook.events,
        active=webhook.active,
        failure_count=webhook.failure_count,
        created_at=webhook.created_at,
        updated_at=webhook.updated_at,
        stats=webhook_schema.WebhookStats(**result["stats"]),
    )


@router.patch("/{webhook_id}", response_model=webhook_schema.WebhookRead)
def update_webhook(
    webhook_id: uuid.UUID,
    payload: webhook_schema.WebhookUpdate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(deps.get_current_user),
):
    """Update webhook configuration."""
    webhook = webhook_service.get_webhook(db, webhook_id)

    if not webhook:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Webhook not found")

    # Check ownership (unless admin)
    if webhook.user_id != current_user.id and not current_user.is_admin:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Webhook not found")

    updated = webhook_service.update_webhook(
        db=db,
        webhook_id=webhook_id,
        name=payload.name,
        url=payload.url,
        events=payload.events,
        active=payload.active,
    )

    return updated


@router.delete("/{webhook_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_webhook(
    webhook_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(deps.get_current_user),
) -> None:
    """Delete a webhook."""
    webhook = webhook_service.get_webhook(db, webhook_id)

    if not webhook:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Webhook not found")

    # Check ownership (unless admin)
    if webhook.user_id != current_user.id and not current_user.is_admin:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Webhook not found")

    webhook_service.delete_webhook(db, webhook_id)

    # Log audit
    audit_service.log_action(
        db=db,
        action=models.AuditAction.USER_UPDATED,
        actor_user_id=current_user.id,
        details={
            "action": "webhook_deleted",
            "webhook_id": str(webhook_id),
        },
    )


@router.post(
    "/{webhook_id}/rotate-secret", response_model=webhook_schema.WebhookSecretRotateResponse
)
def rotate_webhook_secret(
    webhook_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(deps.get_current_user),
):
    """Rotate webhook secret.

    Returns the new secret. This is the only time the secret is exposed.
    """
    webhook = webhook_service.get_webhook(db, webhook_id)

    if not webhook:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Webhook not found")

    # Check ownership (unless admin)
    if webhook.user_id != current_user.id and not current_user.is_admin:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Webhook not found")

    new_secret = webhook_service.rotate_webhook_secret(db, webhook_id)

    return webhook_schema.WebhookSecretRotateResponse(secret=new_secret)


@router.post("/{webhook_id}/test", response_model=webhook_schema.WebhookTestResponse)
async def test_webhook(
    webhook_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(deps.get_current_user),
):
    """Send a test event to verify webhook configuration."""
    webhook = webhook_service.get_webhook(db, webhook_id)

    if not webhook:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Webhook not found")

    # Check ownership (unless admin)
    if webhook.user_id != current_user.id and not current_user.is_admin:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Webhook not found")

    delivery = await webhook_service.send_test_event(db, webhook_id)

    return webhook_schema.WebhookTestResponse(
        delivery_id=delivery.id,
        status=delivery.status,
        response_status_code=delivery.response_status_code,
        response_body=delivery.response_body,
    )


@router.get("/{webhook_id}/deliveries", response_model=list[webhook_schema.WebhookDeliveryRead])
def list_webhook_deliveries(
    webhook_id: uuid.UUID,
    status_filter: WebhookDeliveryStatus | None = None,
    event_type: str | None = None,
    skip: int = 0,
    limit: int = 50,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(deps.get_current_user),
):
    """List deliveries for a specific webhook."""
    webhook = webhook_service.get_webhook(db, webhook_id)

    if not webhook:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Webhook not found")

    # Check ownership (unless admin)
    if webhook.user_id != current_user.id and not current_user.is_admin:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Webhook not found")

    deliveries = webhook_service.list_deliveries(
        db=db,
        webhook_id=webhook_id,
        status_filter=status_filter,
        event_type=event_type,
        skip=skip,
        limit=min(limit, 100),
    )

    return deliveries


# Admin endpoints


@admin_router.post(
    "",
    response_model=webhook_schema.WebhookWithSecret,
    status_code=status.HTTP_201_CREATED,
)
def create_admin_webhook(
    payload: webhook_schema.WebhookCreate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(deps.get_current_admin),
):
    """Create an admin/global webhook.

    Admin webhooks receive all events platform-wide and can subscribe to admin-only events.
    """
    webhook = webhook_service.create_webhook(
        db=db,
        user_id=None,  # Admin webhook
        name=payload.name,
        url=payload.url,
        events=payload.events,
        secret=payload.secret,
    )

    # Log audit
    audit_service.log_action(
        db=db,
        action=models.AuditAction.USER_UPDATED,
        actor_user_id=current_user.id,
        details={
            "action": "admin_webhook_created",
            "webhook_id": str(webhook.id),
            "webhook_name": webhook.name,
            "events": webhook.events,
        },
    )

    return webhook_schema.WebhookWithSecret(
        id=webhook.id,
        user_id=webhook.user_id,
        name=webhook.name,
        url=webhook.url,
        events=webhook.events,
        active=webhook.active,
        failure_count=webhook.failure_count,
        created_at=webhook.created_at,
        updated_at=webhook.updated_at,
        secret=webhook.secret,
    )


@admin_router.get("", response_model=list[webhook_schema.WebhookRead])
def list_all_webhooks(
    user_id: uuid.UUID | None = None,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(deps.get_current_admin),
):
    """List all webhooks (admin only).

    Optionally filter by user_id. Set user_id to None query param to see only admin webhooks.
    """
    webhooks = webhook_service.list_webhooks(db, user_id=user_id, include_admin=True)
    return webhooks


@admin_router.get("/deliveries", response_model=list[webhook_schema.WebhookDeliveryRead])
def list_all_deliveries(
    webhook_id: uuid.UUID | None = None,
    status_filter: WebhookDeliveryStatus | None = None,
    event_type: str | None = None,
    skip: int = 0,
    limit: int = 50,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(deps.get_current_admin),
):
    """List all webhook deliveries (admin only)."""
    deliveries = webhook_service.list_deliveries(
        db=db,
        webhook_id=webhook_id,
        status_filter=status_filter,
        event_type=event_type,
        skip=skip,
        limit=min(limit, 100),
    )

    return deliveries
