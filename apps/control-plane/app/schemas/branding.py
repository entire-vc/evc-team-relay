from __future__ import annotations

from pydantic import BaseModel, Field


class BrandingRead(BaseModel):
    """Instance branding settings."""

    name: str
    logo_url: str
    favicon_url: str
    custom_head_code: str = ""
    custom_body_code: str = ""


class BrandingUpdate(BaseModel):
    """Update instance branding settings."""

    name: str = Field(min_length=1, max_length=100)
    logo_url: str = Field(min_length=1, max_length=2048)
    favicon_url: str = Field(min_length=1, max_length=2048)
    custom_head_code: str = Field(default="", max_length=10000)
    custom_body_code: str = Field(default="", max_length=10000)
