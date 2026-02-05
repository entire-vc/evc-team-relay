"""OAuth/OIDC schemas."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class OAuthProviderInfo(BaseModel):
    """OAuth provider information for login UI."""

    name: str = Field(..., description="Provider name (e.g., 'casdoor', 'keycloak')")
    display_name: str = Field(..., description="Human-readable provider name")
    authorize_url: str = Field(..., description="Full URL to initiate OAuth flow")


class OAuthAuthorizeResponse(BaseModel):
    """Response with OAuth authorize URL for programmatic clients."""

    authorize_url: str = Field(..., description="Full URL to redirect user for OAuth")
    state: str = Field(..., description="State parameter for CSRF protection")


class OAuthCallbackResponse(BaseModel):
    """Response from OAuth callback with tokens and user info."""

    access_token: str = Field(..., description="JWT access token for API authentication")
    refresh_token: str = Field(..., description="Refresh token for token renewal")
    token_type: str = Field(default="bearer", description="Token type")
    expires_in: int = Field(..., description="Access token expiration in seconds")
    user_id: str = Field(..., description="User ID")
    user_email: str = Field(..., description="User email address")
    user_name: str | None = Field(None, description="User display name")


class OAuthStateData(BaseModel):
    """Data stored in OAuth state parameter."""

    code_verifier: str = Field(..., description="PKCE code verifier")
    redirect_uri: str = Field(..., description="OAuth redirect URI")
    created_at: datetime = Field(default_factory=datetime.utcnow, description="State creation time")
    return_url: str | None = Field(
        None, description="Optional URL to redirect to after OAuth callback"
    )


class OAuthUserInfo(BaseModel):
    """User information from OAuth provider."""

    sub: str = Field(..., description="Provider user ID (subject)")
    email: str = Field(..., description="User email address")
    name: str | None = Field(None, description="User display name")
    picture: str | None = Field(None, description="User profile picture URL")
    groups: list[str] = Field(default_factory=list, description="User groups from IAM")
