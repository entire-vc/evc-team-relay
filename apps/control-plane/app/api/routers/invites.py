from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from slowapi import Limiter
from slowapi.util import get_remote_address
from sqlalchemy.orm import Session

from app.api import deps
from app.core import security
from app.core.config import get_settings
from app.db import models
from app.db.session import get_db
from app.schemas import invite as invite_schema
from app.services import auth_service, invite_service, share_service
from app.services.notification_service import get_notification_service
from app.utils.url import get_base_url

router = APIRouter(prefix="/shares", tags=["invites"])
limiter = Limiter(key_func=get_remote_address)

# Public routes for invite redemption
public_router = APIRouter(prefix="/invite", tags=["invites"])

# Templates for SSR
templates = Jinja2Templates(directory="app/templates")


@router.post(
    "/{share_id}/invites",
    response_model=invite_schema.InviteRead,
    status_code=status.HTTP_201_CREATED,
)
@limiter.limit("10/minute")
async def create_invite(
    request: Request,
    share_id: uuid.UUID,
    payload: invite_schema.InviteCreate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(deps.get_current_user),
):
    """Create an invite link for a share.

    Requires owner or admin permissions on the share.
    """
    share = share_service.get_share(db, share_id)
    share_service.ensure_owner_or_admin(current_user, share)

    invite = invite_service.create_invite(db, share_id, current_user.id, payload)

    # Build invite URL and queue notifications
    base_url = get_base_url(request)
    invite_url = f"{base_url}/invite/{invite.token}/page"

    notification_service = get_notification_service()
    ip_address = request.client.host if request.client else None
    user_agent = request.headers.get("user-agent")

    # If invite has email field, send notification to that email
    recipient_email = getattr(payload, "email", None) or getattr(invite, "email", None)

    await notification_service.notify_invite_created(
        db, invite, share, current_user, invite_url, recipient_email, ip_address, user_agent
    )

    return invite


@router.get("/{share_id}/invites", response_model=list[invite_schema.InviteRead])
def list_invites(
    share_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(deps.get_current_user),
):
    """List all invites for a share.

    Requires owner or admin permissions on the share.
    """
    share = share_service.get_share(db, share_id)
    share_service.ensure_owner_or_admin(current_user, share)

    invites = invite_service.list_invites(db, share_id)
    return invites


@router.delete("/{share_id}/invites/{invite_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_invite(
    request: Request,
    share_id: uuid.UUID,
    invite_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(deps.get_current_user),
) -> None:
    """Revoke an invite link.

    Requires owner or admin permissions on the share.
    """
    share = share_service.get_share(db, share_id)
    share_service.ensure_owner_or_admin(current_user, share)

    # Get invite before revocation for notifications
    invite = db.query(models.ShareInvite).filter(models.ShareInvite.id == invite_id).first()

    invite_service.revoke_invite(db, invite_id, current_user.id)

    # Queue notification
    if invite:
        notification_service = get_notification_service()
        ip_address = request.client.host if request.client else None
        user_agent = request.headers.get("user-agent")

        await notification_service.notify_invite_revoked(
            db, invite, share, current_user, ip_address, user_agent
        )


@public_router.get("/{token}", response_model=invite_schema.InvitePublicInfo)
def get_invite_info(
    token: str,
    db: Session = Depends(get_db),
):
    """Get public information about an invite link.

    No authentication required. Returns share details and validity status.
    """
    return invite_service.get_invite_public_info(db, token)


@public_router.post("/{token}/redeem", response_model=invite_schema.InviteRedeemResponse)
@limiter.limit("10/minute")
async def redeem_invite(
    request: Request,
    token: str,
    new_user_data: invite_schema.InviteRedeemNewUser | None = None,
    db: Session = Depends(get_db),
    current_user: models.User | None = Depends(deps.get_optional_user),
):
    """Redeem an invite link.

    For existing users: Authenticate with JWT token in Authorization header.
    For new users: Provide email and password in request body to create account.

    Returns user info, share details, and access token (for new users).
    """
    # Get invite before redemption to check if this is a new redemption
    invite = invite_service.get_invite_by_token(db, token)
    old_use_count = invite.use_count if invite else 0

    result = invite_service.redeem_invite(
        db=db,
        token=token,
        user=current_user,
        new_user_data=new_user_data,
    )

    # Reload invite to check if use_count increased (meaning new redemption)
    db.refresh(invite) if invite else None

    if invite and invite.use_count > old_use_count:
        # Get the redeemer user
        redeemer = db.query(models.User).filter(models.User.id == result.user_id).first()
        share = invite.share
        owner = share.owner if share else None

        if redeemer and share and owner:
            notification_service = get_notification_service()
            ip_address = request.client.host if request.client else None
            user_agent = request.headers.get("user-agent")

            await notification_service.notify_invite_redeemed(
                db, invite, share, redeemer, owner, ip_address, user_agent
            )

    return result


# ==================== SSR ENDPOINTS ====================


def get_user_from_cookie(request: Request, db: Session = Depends(get_db)) -> models.User | None:
    """Get current user from invite_token cookie (optional)."""
    token = request.cookies.get("invite_token")
    if not token:
        return None

    try:
        return deps.get_optional_user(db, authorization=f"Bearer {token}")
    except Exception:
        return None


@public_router.get("/{token}/page", response_class=HTMLResponse)
def invite_page(
    request: Request,
    token: str,
    error: str | None = None,
    db: Session = Depends(get_db),
    current_user: models.User | None = Depends(get_user_from_cookie),
):
    """Show invite redemption page (SSR).

    Displays share information and authentication options.
    """
    invite_info = invite_service.get_invite_public_info(db, token)
    settings = get_settings()

    # Build OAuth URLs with correct scheme (HTTPS behind proxy)
    base_url = get_base_url(request)
    oauth_callback_url = f"{base_url}/v1/auth/oauth/{settings.oauth_provider_name}/callback"
    oauth_authorize_url = (
        f"{base_url}/v1/auth/oauth/{settings.oauth_provider_name}/authorize"
        f"?redirect_uri={oauth_callback_url}"
        f"&return_url={request.url}"
    )

    return templates.TemplateResponse(
        "invite.html",
        {
            "request": request,
            "token": token,
            "invite_info": invite_info,
            "current_user": current_user,
            "error": error,
            "success": False,
            "oauth_enabled": settings.oauth_enabled,
            "oauth_provider": settings.oauth_provider_name,
            "oauth_authorize_url": oauth_authorize_url,
        },
    )


@public_router.post("/{token}/accept", response_class=HTMLResponse)
@limiter.limit("10/minute")
def accept_invite(
    request: Request,
    token: str,
    action: Annotated[str | None, Form()] = None,
    email: Annotated[str | None, Form()] = None,
    password: Annotated[str | None, Form()] = None,
    confirm_password: Annotated[str | None, Form()] = None,
    db: Session = Depends(get_db),
    current_user: models.User | None = Depends(get_user_from_cookie),
):
    """Accept invite - handles both authentication and redemption.

    Actions:
    - register: Create new account and redeem
    - login: Authenticate existing user and redeem
    - (no action): Redeem for already authenticated user
    """
    try:
        # Handle registration
        if action == "register":
            if not email or not password or not confirm_password:
                return RedirectResponse(
                    f"/invite/{token}/page?error=All fields are required",
                    status_code=302,
                )

            if password != confirm_password:
                return RedirectResponse(
                    f"/invite/{token}/page?error=Passwords do not match",
                    status_code=302,
                )

            new_user_data = invite_schema.InviteRedeemNewUser(
                email=email,
                password=password,
            )

            result = invite_service.redeem_invite(
                db=db,
                token=token,
                user=None,
                new_user_data=new_user_data,
            )

            # Set auth cookie and show success
            response = templates.TemplateResponse(
                "invite.html",
                {
                    "request": request,
                    "token": token,
                    "success": True,
                    "success_path": result.share_path,
                    "success_kind": "document" if "doc" in result.share_path.lower() else "folder",
                    "success_role": result.role.value,
                    "access_token": result.access_token,
                },
            )
            if result.access_token:
                response.set_cookie(
                    key="invite_token",
                    value=result.access_token,
                    httponly=True,
                    max_age=86400,  # 24 hours
                    samesite="lax",
                )
            return response

        # Handle login
        elif action == "login":
            if not email or not password:
                return RedirectResponse(
                    f"/invite/{token}/page?error=Email and password are required",
                    status_code=302,
                )

            # Authenticate user
            user = auth_service.authenticate_user(db, email, password)
            if not user:
                return RedirectResponse(
                    f"/invite/{token}/page?error=Invalid email or password",
                    status_code=302,
                )

            # Log the login
            auth_service.log_login(
                db,
                user,
                request.client.host if request.client else None,
                request.headers.get("user-agent"),
            )

            # Redeem invite
            result = invite_service.redeem_invite(
                db=db,
                token=token,
                user=user,
                new_user_data=None,
            )

            # Generate token and set cookie
            access_token = security.create_access_token(str(user.id))
            response = templates.TemplateResponse(
                "invite.html",
                {
                    "request": request,
                    "token": token,
                    "success": True,
                    "success_path": result.share_path,
                    "success_kind": "document" if "doc" in result.share_path.lower() else "folder",
                    "success_role": result.role.value,
                    "access_token": None,  # Don't show token for existing users
                },
            )
            response.set_cookie(
                key="invite_token",
                value=access_token,
                httponly=True,
                max_age=86400,  # 24 hours
                samesite="lax",
            )
            return response

        # Handle already authenticated user
        else:
            if not current_user:
                return RedirectResponse(
                    f"/invite/{token}/page?error=Please sign in or create an account",
                    status_code=302,
                )

            result = invite_service.redeem_invite(
                db=db,
                token=token,
                user=current_user,
                new_user_data=None,
            )

            return templates.TemplateResponse(
                "invite.html",
                {
                    "request": request,
                    "token": token,
                    "success": True,
                    "success_path": result.share_path,
                    "success_kind": "document" if "doc" in result.share_path.lower() else "folder",
                    "success_role": result.role.value,
                    "access_token": None,
                },
            )

    except HTTPException as e:
        # Handle specific errors
        error_msg = str(e.detail)
        if "already exists" in error_msg.lower():
            error_msg = "This email is already registered. Please sign in instead."
        elif "already the owner" in error_msg.lower():
            error_msg = "You are already the owner of this share."
        elif "already a member" in error_msg.lower():
            # This is actually success - show success page
            invite_info = invite_service.get_invite_public_info(db, token)
            return templates.TemplateResponse(
                "invite.html",
                {
                    "request": request,
                    "token": token,
                    "success": True,
                    "success_path": invite_info.share_path,
                    "success_kind": invite_info.share_kind,
                    "success_role": invite_info.role.value,
                    "access_token": None,
                },
            )

        return RedirectResponse(
            f"/invite/{token}/page?error={error_msg}",
            status_code=302,
        )


@public_router.get("/{token}/logout", response_class=RedirectResponse)
def logout_invite(token: str):
    """Logout from invite page."""
    response = RedirectResponse(f"/invite/{token}/page", status_code=302)
    response.delete_cookie("invite_token")
    return response
