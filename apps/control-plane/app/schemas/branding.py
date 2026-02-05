from __future__ import annotations

from pydantic import BaseModel, Field


class BrandingRead(BaseModel):
    """Instance branding settings."""

    name: str
    logo_url: str
    favicon_url: str


class BrandingUpdate(BaseModel):
    """Update instance branding settings."""

    name: str = Field(min_length=1, max_length=100)
    logo_url: str = Field(min_length=1, max_length=2048)
    favicon_url: str = Field(min_length=1, max_length=2048)
