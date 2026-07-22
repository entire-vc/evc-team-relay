"""Email queue admin API schemas (TR-35)."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.db.models import EmailStatus


class EmailQueueRead(BaseModel):
    """Schema for reading a queued email (admin visibility)."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    to_email: str
    subject: str
    email_type: str
    status: EmailStatus
    attempt_count: int
    error_message: str | None
    next_retry_at: datetime | None
    created_at: datetime
    sent_at: datetime | None
