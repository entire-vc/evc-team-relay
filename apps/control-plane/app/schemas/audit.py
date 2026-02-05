from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel

from app.db.models import AuditAction


class AuditLogRead(BaseModel):
    id: uuid.UUID
    timestamp: datetime
    action: AuditAction
    actor_user_id: uuid.UUID | None
    target_user_id: uuid.UUID | None
    target_share_id: uuid.UUID | None
    details: dict | None
    ip_address: str | None
    user_agent: str | None

    model_config = {"from_attributes": True}
