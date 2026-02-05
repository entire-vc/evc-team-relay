"""OAuth/OIDC authentication service."""

from __future__ import annotations

import base64
import hashlib
import secrets
import uuid
from typing import Any

from authlib.integrations.httpx_client import AsyncOAuth2Client
from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db import models
from app.schemas import oauth as oauth_schema


def generate_code_verifier() -> str:
    """Generate PKCE code verifier (43-128 characters, URL-safe)."""
    return base64.urlsafe_b64encode(secrets.token_bytes(32)).decode("utf-8").rstrip("=")


def generate_code_challenge(code_verifier: str) -> str:
    """Generate PKCE code challenge from verifier."""
    digest = hashlib.sha256(code_verifier.encode("utf-8")).digest()
    return base64.urlsafe_b64encode(digest).decode("utf-8").rstrip("=")


def encode_state(state_data: oauth_schema.OAuthStateData) -> str:
    """Encode state data as base64 JSON (not encrypted, just encoded)."""
    json_str = state_data.model_dump_json()
    return base64.urlsafe_b64encode(json_str.encode("utf-8")).decode("utf-8")


def decode_state(state: str) -> oauth_schema.OAuthStateData:
    """Decode state data from base64 JSON."""
    try:
        json_str = base64.urlsafe_b64decode(state.encode("utf-8")).decode("utf-8")
        return oauth_schema.OAuthStateData.model_validate_json(json_str)
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid state parameter: {e}",
        )


def get_oauth_providers(db: Session) -> list[models.OAuthProvider]:
    """Get all enabled OAuth providers from database.

    If OAuth is configured via environment variables and provider doesn't exist,
    it will be created in the database.
    """
    settings = get_settings()

    # If OAuth is enabled via env vars, ensure provider exists in DB
    if settings.oauth_enabled and settings.oauth_issuer_url and settings.oauth_client_id:
        # Check if provider already in DB
        stmt = select(models.OAuthProvider).where(
            models.OAuthProvider.name == settings.oauth_provider_name
        )
        existing = db.execute(stmt).scalar_one_or_none()
        if not existing:
            # Create provider in database
            provider = models.OAuthProvider(
                id=uuid.uuid4(),
                name=settings.oauth_provider_name,
                provider_type=models.OAuthProviderType.OIDC,
                issuer_url=settings.oauth_issuer_url,
                client_id=settings.oauth_client_id,
                client_secret_encrypted="ENV",  # Marker that secret is in env var
                enabled=True,
                auto_register=settings.oauth_auto_register,
            )
            db.add(provider)
            db.commit()

    # Get all enabled providers from database
    stmt = select(models.OAuthProvider).where(models.OAuthProvider.enabled.is_(True))
    return list(db.execute(stmt).scalars().all())


def get_oauth_provider(db: Session, provider_name: str) -> models.OAuthProvider:
    """Get specific OAuth provider by name.

    If provider is configured via environment variables and doesn't exist in DB,
    it will be created and persisted.

    Raises:
        HTTPException: If provider not found or not enabled.
    """
    settings = get_settings()

    # Check database first
    stmt = select(models.OAuthProvider).where(
        models.OAuthProvider.name == provider_name,
        models.OAuthProvider.enabled.is_(True),
    )
    provider = db.execute(stmt).scalar_one_or_none()

    if provider:
        return provider

    # If env-configured provider, create it in database
    if (
        settings.oauth_enabled
        and provider_name == settings.oauth_provider_name
        and settings.oauth_issuer_url
        and settings.oauth_client_id
        and settings.oauth_client_secret
    ):
        # Create provider in database for proper FK relationships
        provider = models.OAuthProvider(
            id=uuid.uuid4(),
            name=settings.oauth_provider_name,
            provider_type=models.OAuthProviderType.OIDC,
            issuer_url=settings.oauth_issuer_url,
            client_id=settings.oauth_client_id,
            client_secret_encrypted="ENV",  # Marker that secret is in env var
            enabled=True,
            auto_register=settings.oauth_auto_register,
        )
        db.add(provider)
        db.commit()
        db.refresh(provider)
        return provider

    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail=f"OAuth provider '{provider_name}' not found or not enabled",
    )


def get_client_secret(provider: models.OAuthProvider) -> str:
    """Get client secret for provider (from env or decrypt from DB)."""
    settings = get_settings()

    # If secret is stored as "ENV" marker, use env var
    if provider.client_secret_encrypted == "ENV" and settings.oauth_client_secret:
        return settings.oauth_client_secret

    # Otherwise use value from database
    # For now, assume client_secret_encrypted is plain text (TODO: implement encryption)
    return provider.client_secret_encrypted


def generate_authorize_url(
    provider: models.OAuthProvider,
    redirect_uri: str,
    return_url: str | None = None,
) -> tuple[str, str]:
    """Generate OAuth authorize URL with PKCE.

    Args:
        provider: OAuth provider configuration
        redirect_uri: OAuth callback URL
        return_url: Optional URL to redirect to after OAuth callback

    Returns:
        Tuple of (authorize_url, state_token)
    """
    settings = get_settings()

    # Generate PKCE parameters
    code_verifier = generate_code_verifier()
    code_challenge = generate_code_challenge(code_verifier)

    # Create state data (includes return_url if provided)
    state_data = oauth_schema.OAuthStateData(
        code_verifier=code_verifier,
        redirect_uri=redirect_uri,
        return_url=return_url,
    )
    state_token = encode_state(state_data)

    # Build authorize URL
    # For Casdoor, the authorize endpoint is at /login/oauth/authorize
    authorize_endpoint = f"{provider.issuer_url.rstrip('/')}/login/oauth/authorize"

    # Use configurable scopes from settings
    scopes = settings.oauth_scopes if settings.oauth_enabled else "openid profile email"

    params = {
        "client_id": provider.client_id,
        "response_type": "code",
        "redirect_uri": redirect_uri,
        "scope": scopes,
        "state": state_token,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
    }

    # Build query string (URL-encode values)
    from urllib.parse import quote

    query_parts = [f"{k}={quote(str(v), safe='')}" for k, v in params.items()]
    authorize_url = f"{authorize_endpoint}?{'&'.join(query_parts)}"

    return authorize_url, state_token


async def exchange_code_for_tokens(
    provider: models.OAuthProvider,
    code: str,
    redirect_uri: str,
    code_verifier: str,
) -> dict[str, Any]:
    """Exchange authorization code for access token using PKCE.

    Returns:
        Token response dict with access_token, refresh_token, etc.
    """
    client_secret = get_client_secret(provider)
    token_endpoint = f"{provider.issuer_url.rstrip('/')}/api/login/oauth/access_token"

    async with AsyncOAuth2Client(
        client_id=provider.client_id,
        client_secret=client_secret,
        code_challenge_method="S256",
    ) as client:
        token_response = await client.fetch_token(
            token_endpoint,
            grant_type="authorization_code",
            code=code,
            redirect_uri=redirect_uri,
            code_verifier=code_verifier,
        )

    return token_response


async def get_user_info(
    provider: models.OAuthProvider, access_token: str
) -> oauth_schema.OAuthUserInfo:
    """Fetch user profile from OIDC userinfo endpoint.

    Returns:
        User information from OAuth provider.
    """
    userinfo_endpoint = f"{provider.issuer_url.rstrip('/')}/api/userinfo"

    async with AsyncOAuth2Client(
        client_id=provider.client_id,
        token={"access_token": access_token, "token_type": "Bearer"},
    ) as client:
        response = await client.get(userinfo_endpoint)
        response.raise_for_status()
        userinfo = response.json()

    # Parse groups - check various common claims
    groups: list[str] = []
    for groups_claim in ["groups", "roles", "group", "memberOf"]:
        if groups_claim in userinfo:
            groups_value = userinfo[groups_claim]
            if isinstance(groups_value, list):
                groups = [str(g) for g in groups_value]
            elif isinstance(groups_value, str):
                groups = [g.strip() for g in groups_value.split(",") if g.strip()]
            break

    # Map to our schema
    return oauth_schema.OAuthUserInfo(
        sub=userinfo.get("sub") or userinfo.get("id", ""),
        email=userinfo.get("email", ""),
        name=userinfo.get("name"),
        picture=userinfo.get("picture"),
        groups=groups,
    )


def should_be_admin(user_groups: list[str]) -> bool:
    """Check if user should be admin based on IAM groups.

    Args:
        user_groups: List of groups user belongs to in IAM

    Returns:
        True if user should be admin based on OAUTH_ADMIN_GROUPS config
    """
    settings = get_settings()
    if not settings.oauth_admin_groups:
        return False

    admin_groups = [g.strip().lower() for g in settings.oauth_admin_groups.split(",") if g.strip()]
    user_groups_lower = [g.lower() for g in user_groups]

    return any(ag in user_groups_lower for ag in admin_groups)


def get_default_admin_status() -> bool:
    """Get default admin status based on OAUTH_DEFAULT_ROLE config."""
    settings = get_settings()
    return settings.oauth_default_role == "admin"


def sync_user_info(
    db: Session,
    user: models.User,
    userinfo: oauth_schema.OAuthUserInfo,
) -> bool:
    """Sync user information from IAM.

    Updates admin status based on IAM groups.
    Note: User name is stored in UserOAuthAccount, not User table.

    Args:
        db: Database session
        user: User to update
        userinfo: User info from IAM

    Returns:
        True if user was updated
    """
    settings = get_settings()
    if not settings.oauth_sync_user_info:
        return False

    updated = False

    # Update admin status based on groups
    if settings.oauth_admin_groups:
        new_admin_status = should_be_admin(userinfo.groups)
        if user.is_admin != new_admin_status:
            user.is_admin = new_admin_status
            updated = True

    if updated:
        db.commit()
        db.refresh(user)

    return updated


def find_user_by_oauth(
    db: Session,
    provider_id: uuid.UUID,
    provider_user_id: str,
) -> models.User | None:
    """Find user by OAuth account.

    Args:
        db: Database session
        provider_id: OAuth provider ID
        provider_user_id: User ID from OAuth provider

    Returns:
        User if found, None otherwise
    """
    stmt = (
        select(models.User)
        .join(models.UserOAuthAccount)
        .where(
            models.UserOAuthAccount.provider_id == provider_id,
            models.UserOAuthAccount.provider_user_id == provider_user_id,
        )
    )
    return db.execute(stmt).scalar_one_or_none()


def find_user_by_email(db: Session, email: str) -> models.User | None:
    """Find user by email address."""
    stmt = select(models.User).where(models.User.email == email)
    return db.execute(stmt).scalar_one_or_none()


def create_user_from_oauth(
    db: Session,
    email: str,
    name: str | None,
    provider_id: uuid.UUID,
    provider_user_id: str,
    picture_url: str | None = None,
    groups: list[str] | None = None,
) -> models.User:
    """Create new user from OAuth profile (auto-registration).

    Args:
        db: Database session
        email: User email
        name: User display name
        provider_id: OAuth provider ID
        provider_user_id: User ID from OAuth provider
        picture_url: User profile picture URL
        groups: User groups from IAM (for admin role mapping)

    Returns:
        Created user
    """
    # Determine admin status from groups or default role
    is_admin = False
    if groups:
        is_admin = should_be_admin(groups)
    if not is_admin:
        is_admin = get_default_admin_status()

    # Create user without password (OAuth-only account)
    # Note: User name is stored in UserOAuthAccount, not User table
    user = models.User(
        id=uuid.uuid4(),
        email=email,
        password_hash="",  # No password for OAuth-only accounts
        is_admin=is_admin,
        is_active=True,
    )
    db.add(user)
    db.flush()  # Get user ID

    # Link OAuth account
    oauth_account = models.UserOAuthAccount(
        id=uuid.uuid4(),
        user_id=user.id,
        provider_id=provider_id,
        provider_user_id=provider_user_id,
        email=email,
        name=name,
        picture_url=picture_url,
    )
    db.add(oauth_account)
    db.commit()
    db.refresh(user)

    return user


def link_oauth_account(
    db: Session,
    user_id: uuid.UUID,
    provider_id: uuid.UUID,
    provider_user_id: str,
    email: str,
    name: str | None = None,
    picture_url: str | None = None,
) -> models.UserOAuthAccount:
    """Link OAuth account to existing user.

    Args:
        db: Database session
        user_id: User ID to link to
        provider_id: OAuth provider ID
        provider_user_id: User ID from OAuth provider
        email: User email from OAuth
        name: User display name from OAuth
        picture_url: User profile picture URL

    Returns:
        Created OAuth account link

    Raises:
        HTTPException: If OAuth account already linked to different user
    """
    # Check if this OAuth account is already linked
    existing = find_user_by_oauth(db, provider_id, provider_user_id)
    if existing and existing.id != user_id:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This OAuth account is already linked to another user",
        )

    # Create or update OAuth account link
    stmt = select(models.UserOAuthAccount).where(
        models.UserOAuthAccount.user_id == user_id,
        models.UserOAuthAccount.provider_id == provider_id,
    )
    oauth_account = db.execute(stmt).scalar_one_or_none()

    if oauth_account:
        # Update existing link
        oauth_account.provider_user_id = provider_user_id
        oauth_account.email = email
        oauth_account.name = name
        oauth_account.picture_url = picture_url
    else:
        # Create new link
        oauth_account = models.UserOAuthAccount(
            id=uuid.uuid4(),
            user_id=user_id,
            provider_id=provider_id,
            provider_user_id=provider_user_id,
            email=email,
            name=name,
            picture_url=picture_url,
        )
        db.add(oauth_account)

    db.commit()
    db.refresh(oauth_account)
    return oauth_account
