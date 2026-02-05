from __future__ import annotations

import uuid
from datetime import datetime
from enum import Enum

from pydantic import BaseModel


class TokenMode(str, Enum):
    READ = "read"
    WRITE = "write"


class RelayTokenRequest(BaseModel):
    share_id: uuid.UUID
    doc_id: str
    mode: TokenMode = TokenMode.READ
    password: str | None = None
    file_path: str | None = None  # For folder shares: path of file within folder


class RelayTokenResponse(BaseModel):
    relay_url: str
    token: str
    expires_at: datetime
