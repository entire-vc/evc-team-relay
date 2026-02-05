from __future__ import annotations

from pydantic import BaseModel


class PublicKeyResponse(BaseModel):
    """Response containing public key for relay JWT verification."""

    key_id: str
    public_key: str
    algorithm: str

    model_config = {"from_attributes": True}
