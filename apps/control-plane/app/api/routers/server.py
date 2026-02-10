from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.session import get_db
from app.services import instance_settings_service

router = APIRouter(tags=["meta"])


class ServerFeatures(BaseModel):
    """Supported server features."""

    multi_user: bool = True
    share_members: bool = True
    audit_logging: bool = True
    admin_ui: bool = True
    oauth_enabled: bool = False
    oauth_provider: str | None = None
    web_publish_enabled: bool = False
    web_publish_domain: str | None = None


class ServerBranding(BaseModel):
    """Server branding settings."""

    name: str
    logo_url: str
    favicon_url: str
    custom_head_code: str = ""
    custom_body_code: str = ""


class ServerInfo(BaseModel):
    """Server metadata for plugin configuration."""

    id: str
    name: str
    version: str
    edition: str  # "community" or "enterprise"
    relay_url: str
    features: ServerFeatures
    branding: ServerBranding


@router.get("/server/info", response_model=ServerInfo)
def get_server_info(db: Session = Depends(get_db)) -> ServerInfo:
    """
    Get server metadata.

    This endpoint provides information about the control plane server,
    including its identity, version, and supported features.
    Used by plugins for multi-server configuration.
    """
    settings = get_settings()

    # Use explicit server_id or derive from relay_key_id
    server_id = settings.server_id or settings.relay_key_id or "default"

    # Get branding settings
    branding_data = instance_settings_service.get_branding(db)

    return ServerInfo(
        id=server_id,
        name=settings.server_name,
        version=settings.api_version,
        edition="community",  # OSS edition; Enterprise will override this
        relay_url=str(settings.relay_public_url).rstrip("/"),
        features=ServerFeatures(
            oauth_enabled=settings.oauth_enabled,
            oauth_provider=settings.oauth_provider_name if settings.oauth_enabled else None,
            web_publish_enabled=settings.web_publish_enabled,
            web_publish_domain=settings.web_publish_domain,
        ),
        branding=ServerBranding(**branding_data),
    )
