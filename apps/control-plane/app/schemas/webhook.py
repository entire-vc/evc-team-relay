"""Webhook API schemas."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.db.models import WebhookDeliveryStatus
from app.services.webhook_service import VALID_EVENT_TYPES


class WebhookCreate(BaseModel):
    """Schema for creating a webhook."""

    name: str = Field(..., min_length=1, max_length=100, description="Webhook name")
    url: str = Field(..., min_length=1, max_length=2048, description="Webhook URL (HTTPS required)")
    events: list[str] = Field(..., min_length=1, description="List of event types to subscribe to")
    secret: str | None = Field(None, description="Optional secret (auto-generated if not provided)")

    @field_validator("events")
    @classmethod
    def validate_events(cls, v: list[str]) -> list[str]:
        """Validate event types."""
        for event in v:
            if event not in VALID_EVENT_TYPES:
                raise ValueError(f"Invalid event type: {event}")
        return v


class WebhookUpdate(BaseModel):
    """Schema for updating a webhook."""

    name: str | None = Field(None, min_length=1, max_length=100, description="Webhook name")
    url: str | None = Field(None, min_length=1, max_length=2048, description="Webhook URL")
    events: list[str] | None = Field(None, min_length=1, description="Event types to subscribe to")
    active: bool | None = Field(None, description="Active status")

    @field_validator("events")
    @classmethod
    def validate_events(cls, v: list[str] | None) -> list[str] | None:
        """Validate event types."""
        if v is None:
            return v
        for event in v:
            if event not in VALID_EVENT_TYPES:
                raise ValueError(f"Invalid event type: {event}")
        return v


class WebhookRead(BaseModel):
    """Schema for reading a webhook (excludes secret)."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    user_id: uuid.UUID | None
    name: str
    url: str
    events: list[str]
    active: bool
    failure_count: int
    created_at: datetime
    updated_at: datetime


class WebhookWithSecret(WebhookRead):
    """Schema for webhook with secret (used after creation or rotation)."""

    secret: str


class WebhookStats(BaseModel):
    """Webhook delivery statistics."""

    total_deliveries: int
    successful_deliveries: int
    failed_deliveries: int
    last_delivery_at: datetime | None


class WebhookWithStats(BaseModel):
    """Webhook with delivery statistics."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    user_id: uuid.UUID | None
    name: str
    url: str
    events: list[str]
    active: bool
    failure_count: int
    created_at: datetime
    updated_at: datetime
    stats: WebhookStats


class WebhookDeliveryRead(BaseModel):
    """Schema for reading a webhook delivery."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    webhook_id: uuid.UUID
    event_id: uuid.UUID
    event_type: str
    payload: dict
    status: WebhookDeliveryStatus
    response_status_code: int | None
    response_body: str | None
    attempt_count: int
    next_retry_at: datetime | None
    created_at: datetime
    updated_at: datetime


class WebhookSecretRotateResponse(BaseModel):
    """Response after rotating webhook secret."""

    secret: str


class WebhookTestResponse(BaseModel):
    """Response after sending test event."""

    delivery_id: uuid.UUID
    status: WebhookDeliveryStatus
    response_status_code: int | None
    response_body: str | None


class EventTypesResponse(BaseModel):
    """List of available event types."""

    event_types: list[str]
    admin_only_events: list[str]
