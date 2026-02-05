"""OAuth/OIDC authentication endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db import models
from app.db.session import get_db
from app.schemas import oauth as oauth_schema
from app.services import audit_service, oauth_service, session_service

router = APIRouter(prefix="/auth/oauth", tags=["oauth"])


@router.get("/providers", response_model=list[oauth_schema.OAuthProviderInfo])
def list_providers(db: Session = Depends(get_db)) -> list[oauth_schema.OAuthProviderInfo]:
    """List available OAuth providers for login UI.

    Returns:
        List of OAuth providers with their authorization URLs.
    """
    get_settings()
    providers = oauth_service.get_oauth_providers(db)

    result = []
    for provider in providers:
        # Generate authorize URL template (without state, just for display)
        authorize_url_base = f"{provider.issuer_url.rstrip('/')}/login/oauth/authorize"

        result.append(
            oauth_schema.OAuthProviderInfo(
                name=provider.name,
                display_name=provider.name.title(),
                authorize_url=authorize_url_base,
            )
        )

    return result


@router.get("/{provider}/authorize", response_model=None)
def authorize(
    provider: str,
    redirect_uri: str,
    request: Request,
    db: Session = Depends(get_db),
    return_url: str | None = None,
):
    """Initiate OAuth flow.

    For browser clients: returns 302 redirect to provider authorization page.
    For programmatic clients (Accept: application/json): returns JSON with authorize URL.

    Args:
        provider: OAuth provider name (e.g., 'casdoor')
        redirect_uri: Callback URI after OAuth completes
        return_url: Optional URL to redirect to after OAuth callback
        request: FastAPI request (for Accept header)
        db: Database session

    Returns:
        Redirect to OAuth provider or JSON with authorize URL
    """
    # Get provider config
    provider_obj = oauth_service.get_oauth_provider(db, provider)

    # Normalize redirect_uri to HTTPS if behind proxy
    # This ensures Casdoor gets the correct HTTPS callback URL
    # BUT: do NOT change localhost/127.0.0.1 - they MUST stay HTTP
    forwarded_proto = request.headers.get("x-forwarded-proto")
    is_localhost = "localhost" in redirect_uri or "127.0.0.1" in redirect_uri
    if forwarded_proto == "https" and redirect_uri.startswith("http://") and not is_localhost:
        redirect_uri = redirect_uri.replace("http://", "https://", 1)

    # Normalize return_url to HTTPS if behind proxy
    if return_url and forwarded_proto == "https" and return_url.startswith("http://"):
        return_url = return_url.replace("http://", "https://", 1)

    # Generate authorize URL with PKCE (includes return_url in state if provided)
    authorize_url, state_token = oauth_service.generate_authorize_url(
        provider_obj,
        redirect_uri,
        return_url=return_url,
    )

    # Check if client wants JSON response
    accept_header = request.headers.get("accept", "")
    if "application/json" in accept_header:
        return oauth_schema.OAuthAuthorizeResponse(
            authorize_url=authorize_url,
            state=state_token,
        )

    # Default: redirect user to OAuth provider
    return RedirectResponse(url=authorize_url, status_code=status.HTTP_302_FOUND)


@router.get("/{provider}/callback")
async def callback(
    provider: str,
    code: str,
    state: str,
    request: Request,
    db: Session = Depends(get_db),
):
    """OAuth callback handler - exchanges code for tokens.

    Args:
        provider: OAuth provider name
        code: Authorization code from OAuth provider
        state: State parameter (contains PKCE verifier and optional return_url)
        request: FastAPI request
        db: Database session

    Returns:
        If return_url is present: Redirects to return_url with invite_token cookie
        Otherwise: Returns JSON with access token, refresh token, and user info
    """
    settings = get_settings()

    # Decode and validate state
    state_data = oauth_service.decode_state(state)

    # Get provider config
    provider_obj = oauth_service.get_oauth_provider(db, provider)

    # Exchange authorization code for tokens
    token_response = await oauth_service.exchange_code_for_tokens(
        provider_obj,
        code=code,
        redirect_uri=state_data.redirect_uri,
        code_verifier=state_data.code_verifier,
    )

    # Get user info from OAuth provider
    userinfo = await oauth_service.get_user_info(provider_obj, token_response["access_token"])

    # Find or create user
    user = oauth_service.find_user_by_oauth(db, provider_obj.id, userinfo.sub)

    if not user:
        # Check if user exists with this email
        user = oauth_service.find_user_by_email(db, userinfo.email)

        if user:
            # Link OAuth account to existing user
            oauth_service.link_oauth_account(
                db,
                user_id=user.id,
                provider_id=provider_obj.id,
                provider_user_id=userinfo.sub,
                email=userinfo.email,
                name=userinfo.name,
                picture_url=userinfo.picture,
            )

            # Sync user info from IAM
            oauth_service.sync_user_info(db, user, userinfo)

            # Log account linking
            audit_service.log_action(
                db=db,
                action=models.AuditAction.OAUTH_ACCOUNT_LINKED,
                actor_user_id=user.id,
                details={
                    "provider": provider,
                    "provider_user_id": userinfo.sub,
                    "groups": userinfo.groups,
                },
            )
        elif provider_obj.auto_register:
            # Auto-register new user with group-based admin role
            user = oauth_service.create_user_from_oauth(
                db,
                email=userinfo.email,
                name=userinfo.name,
                provider_id=provider_obj.id,
                provider_user_id=userinfo.sub,
                picture_url=userinfo.picture,
                groups=userinfo.groups,
            )

            # Log user creation
            audit_service.log_action(
                db=db,
                action=models.AuditAction.USER_CREATED,
                actor_user_id=user.id,
                details={
                    "email": userinfo.email,
                    "oauth_provider": provider,
                    "groups": userinfo.groups,
                    "is_admin": user.is_admin,
                },
            )
        else:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Auto-registration is disabled. Please contact administrator.",
            )
    else:
        # Existing OAuth user - sync info on each login
        oauth_service.sync_user_info(db, user, userinfo)

    # Check if user is active
    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User account is inactive",
        )

    # Create access token
    from app.core import security

    access_token = security.create_access_token(str(user.id))

    # Create session with refresh token
    _, refresh_token = session_service.create_session(
        db=db,
        user_id=user.id,
        device_name=f"OAuth ({provider})",
        user_agent=request.headers.get("user-agent"),
        ip_address=request.client.host if request.client else None,
        expires_days=settings.refresh_token_expire_days,
    )

    # Log OAuth login
    audit_service.log_action(
        db=db,
        action=models.AuditAction.OAUTH_LOGIN,
        actor_user_id=user.id,
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
        details={
            "provider": provider,
            "provider_user_id": userinfo.sub,
        },
    )

    db.commit()

    # If return_url is provided, redirect with cookie
    if hasattr(state_data, "return_url") and state_data.return_url:
        response = RedirectResponse(url=state_data.return_url, status_code=status.HTTP_302_FOUND)
        response.set_cookie(
            key="invite_token",
            value=access_token,
            httponly=True,
            max_age=86400,  # 24 hours
            samesite="lax",
        )
        return response

    # Otherwise, return JSON response
    return oauth_schema.OAuthCallbackResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        token_type="bearer",
        expires_in=settings.access_token_expire_minutes * 60,
        user_id=str(user.id),
        user_email=user.email,
        user_name=userinfo.name,
    )
