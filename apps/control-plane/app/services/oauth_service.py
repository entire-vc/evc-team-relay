"""OAuth/OIDC authentication service."""

from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import secrets
import uuid
from typing import Any

import httpx
import jwt
from authlib.integrations.httpx_client import AsyncOAuth2Client
from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from app.core.config import get_settings
from app.db import models
from app.schemas import oauth as oauth_schema
from app.services import argus_service

logger = logging.getLogger(__name__)


def generate_code_verifier() -> str:
    """Generate PKCE code verifier (43-128 characters, URL-safe)."""
    return base64.urlsafe_b64encode(secrets.token_bytes(32)).decode("utf-8").rstrip("=")


def generate_code_challenge(code_verifier: str) -> str:
    """Generate PKCE code challenge from verifier."""
    digest = hashlib.sha256(code_verifier.encode("utf-8")).digest()
    return base64.urlsafe_b64encode(digest).decode("utf-8").rstrip("=")


_STATE_HMAC_SEP = "."


def _compute_state_hmac(payload_b64: str, secret: str) -> str:
    """Return HMAC-SHA256 hex digest for a base64-encoded state payload.

    Uses constant-time comparison in decode_state to prevent timing attacks.
    The separator '.' is safe because urlsafe_b64 only uses A-Z a-z 0-9 - _.
    """
    return hmac.new(secret.encode("utf-8"), payload_b64.encode("utf-8"), hashlib.sha256).hexdigest()


def encode_state(state_data: oauth_schema.OAuthStateData) -> str:
    """Encode state data as base64 JSON, appending an HMAC-SHA256 signature when configured."""
    settings = get_settings()
    payload_b64 = base64.urlsafe_b64encode(state_data.model_dump_json().encode("utf-8")).decode(
        "utf-8"
    )
    if settings.oauth_state_secret:
        sig = _compute_state_hmac(payload_b64, settings.oauth_state_secret)
        return f"{payload_b64}{_STATE_HMAC_SEP}{sig}"
    return payload_b64


def decode_state(state: str) -> oauth_schema.OAuthStateData:
    """Decode and verify state data. Rejects unsigned or tampered state when HMAC is configured."""
    settings = get_settings()
    try:
        if settings.oauth_state_secret:
            if _STATE_HMAC_SEP not in state:
                raise ValueError("Missing HMAC signature in state parameter")
            payload_b64, received_sig = state.rsplit(_STATE_HMAC_SEP, 1)
            expected_sig = _compute_state_hmac(payload_b64, settings.oauth_state_secret)
            if not hmac.compare_digest(received_sig, expected_sig):
                raise ValueError("State HMAC signature mismatch — possible CSRF attempt")
        else:
            payload_b64 = state
        json_str = base64.urlsafe_b64decode(payload_b64.encode("utf-8")).decode("utf-8")
        return oauth_schema.OAuthStateData.model_validate_json(json_str)
    except HTTPException:
        raise
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


# Cache of PyJWKClient instances, one per JWKS URI. PyJWKClient itself caches
# the fetched keys (default lifespan), so this only saves re-instantiating the
# client — it never lets a genuinely-stale key set persist across a real
# key-rotation window, that's PyJWKClient's own concern.
_jwks_clients: dict[str, jwt.PyJWKClient] = {}


def _get_jwks_client(jwks_uri: str) -> jwt.PyJWKClient:
    client = _jwks_clients.get(jwks_uri)
    if client is None:
        client = jwt.PyJWKClient(jwks_uri)
        _jwks_clients[jwks_uri] = client
    return client


async def _discover_oidc_config(issuer_url: str) -> dict[str, Any]:
    """Fetch the provider's OIDC discovery document.

    No caching here (unlike the JWKS client): this is one small JSON GET per
    login, and caching it would need its own invalidation story for zero
    real benefit — jwks_uri essentially never changes for a live provider.
    """
    discovery_url = f"{issuer_url.rstrip('/')}/.well-known/openid-configuration"
    async with httpx.AsyncClient(timeout=10) as client:
        response = await client.get(discovery_url)
        response.raise_for_status()
        return response.json()


async def validate_id_token(provider: models.OAuthProvider, id_token: str) -> dict[str, Any]:
    """Validate an OIDC id_token's signature and standard claims, returning it decoded.

    Security-critical (#f1e6f0dc): this is what turns an id_token from "text
    that arrived over TLS" into "a claim we can act on". Verifies the
    signature against the provider's published JWKS, and checks `iss`, `aud`
    (must equal our own client_id), and `exp` — all via PyJWT's own checks,
    not hand-rolled comparisons.

    Raises on ANY failure: network error, no matching key, bad signature,
    wrong issuer/audience, expired token. Callers MUST treat every exception
    here as "unverified" (fail closed) — never catch-and-proceed as if the
    token were valid, and never skip calling this at all.
    """
    discovery = await _discover_oidc_config(provider.issuer_url)
    jwks_uri = discovery["jwks_uri"]
    algorithms = discovery.get("id_token_signing_alg_values_supported") or ["RS256"]

    jwks_client = _get_jwks_client(jwks_uri)
    # PyJWKClient does its own blocking HTTP fetch (with internal caching) —
    # push it off the event loop rather than stalling every other request
    # in this worker for the duration of a cache-miss fetch.
    signing_key = await run_in_threadpool(jwks_client.get_signing_key_from_jwt, id_token)

    return jwt.decode(
        id_token,
        signing_key.key,
        algorithms=algorithms,
        audience=provider.client_id,
        issuer=discovery.get("issuer") or provider.issuer_url,
    )


async def get_user_info(
    provider: models.OAuthProvider, token_response: dict[str, Any]
) -> oauth_schema.OAuthUserInfo:
    """Fetch user profile from OIDC userinfo endpoint, with `email_verified`
    sourced from the validated id_token instead.

    Args:
        provider: OAuth provider configuration.
        token_response: The full token-endpoint response (as returned by
            exchange_code_for_tokens) — needs both `access_token` (for the
            userinfo call) and `id_token` (for email_verified).

    Returns:
        User information from OAuth provider.
    """
    access_token = token_response["access_token"]
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

    # email_verified: NEVER trust userinfo's copy of this field (Casdoor
    # hardcodes it to `true` whenever the `email` scope is granted — see
    # OAuthUserInfo.email_verified docstring / #970e22f4). The only source
    # of truth is the signature-validated id_token. Any failure to obtain or
    # validate it — no id_token in the response, network error, bad
    # signature, wrong issuer/audience, expired — fails closed to False.
    email_verified = False
    id_token = token_response.get("id_token")
    if id_token:
        try:
            id_token_claims = await validate_id_token(provider, id_token)
            email_verified = bool(id_token_claims.get("email_verified", False))
        except Exception:
            logger.warning(
                "id_token validation failed for provider '%s' — treating email_verified as False",
                provider.name,
                exc_info=True,
            )
    else:
        logger.warning(
            "No id_token in token response for provider '%s' — treating email_verified as "
            "False (was the 'openid' scope granted?)",
            provider.name,
        )

    # Map to our schema
    return oauth_schema.OAuthUserInfo(
        sub=userinfo.get("sub") or userinfo.get("id", ""),
        email=userinfo.get("email", ""),
        name=userinfo.get("name"),
        picture=userinfo.get("picture"),
        groups=groups,
        email_verified=email_verified,
    )


def should_be_admin(user_groups: list[str]) -> bool:
    """Check if user should be admin based on IAM groups.

    Supports both exact match and org/group format (e.g., "entire_vc/admin").
    - If admin_group contains "/", requires exact match (e.g., "entire_vc/admin")
    - If admin_group is simple name, matches both exact and "org/name" format

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

    # Check each admin group against user groups
    for admin_group in admin_groups:
        for user_group in user_groups_lower:
            # Exact match always works
            if user_group == admin_group:
                return True

            # Suffix match only if admin_group is a simple name (no "/" in it)
            # This allows "admin" to match "org/admin" but not "other_org/admin"
            if "/" not in admin_group and user_group.endswith(f"/{admin_group}"):
                return True

    return False


def get_default_admin_status() -> bool:
    """Get default admin status based on OAUTH_DEFAULT_ROLE config."""
    settings = get_settings()
    return settings.oauth_default_role == "admin"


def sync_user_info(
    db: Session,
    user: models.User,
    userinfo: oauth_schema.OAuthUserInfo,
) -> tuple[bool, dict[str, Any]]:
    """Sync user information from IAM.

    Updates admin status based on IAM groups. Only ELEVATES admin rights, never revokes them.
    This prevents bootstrap admins from losing admin status during OAuth sync.

    Note: User name is stored in UserOAuthAccount, not User table.

    Args:
        db: Database session
        user: User to update
        userinfo: User info from IAM

    Returns:
        Tuple of (updated, changes_dict) where updated is True if user was changed,
        and changes_dict contains details of what changed
    """
    settings = get_settings()
    if not settings.oauth_sync_user_info:
        return False, {}

    updated = False
    changes: dict[str, Any] = {}

    # Update admin status based on groups (only elevate, never revoke)
    if settings.oauth_admin_groups:
        should_have_admin = should_be_admin(userinfo.groups)
        # Only ELEVATE admin rights, never revoke them
        if should_have_admin and not user.is_admin:
            user.is_admin = True
            updated = True
            changes["is_admin"] = {"old": False, "new": True, "reason": "oauth_group_match"}

    if updated:
        db.commit()
        db.refresh(user)

    return updated, changes


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

    # Register in Argus CRM with casdoor_id for cross-product dedup (S2-3).
    argus_service.register_product_user(
        email=email,
        display_name=name or email.split("@")[0],
        registered_at=user.created_at,
        casdoor_id=provider_user_id,
    )

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
    # Check if this exact external identity is already linked to someone.
    existing_user = find_user_by_oauth(db, provider_id, provider_user_id)
    if existing_user and existing_user.id != user_id:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This OAuth account is already linked to another user",
        )

    # Resolve (or create) the row for THIS EXACT identity, keyed on
    # (provider_id, provider_user_id) — the same pair uq_provider_user
    # enforces uniqueness on. Looking this up by (user_id, provider_id)
    # instead (the old behavior) meant a user with more than one identity
    # at the same provider had them collapsed onto a single row: a second
    # sign-in with a DIFFERENT subject at the same provider silently
    # overwrote provider_user_id on the user's existing row rather than
    # creating a second one — the account-takeover path this function is
    # the fix for (a login callback that resolves an existing user by email
    # would then re-point that user's row at the new subject, and the
    # original subject would no longer resolve to anyone).
    stmt = select(models.UserOAuthAccount).where(
        models.UserOAuthAccount.provider_id == provider_id,
        models.UserOAuthAccount.provider_user_id == provider_user_id,
    )
    oauth_account = db.execute(stmt).scalar_one_or_none()

    if oauth_account:
        # The 409 check above guarantees this row's user_id already equals
        # user_id (find_user_by_oauth() and this query select the same row
        # by construction) — this is a profile refresh of an
        # already-linked identity, never a new link, so provider_user_id
        # is never reassigned here.
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
