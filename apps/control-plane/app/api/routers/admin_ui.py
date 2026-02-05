"""Admin UI router - Server-side rendered admin panel."""

from __future__ import annotations

import logging
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Form, HTTPException, Request, UploadFile, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.api import deps
from app.core.config import get_settings
from app.db import models
from app.db.session import get_db
from app.schemas import share as share_schema
from app.schemas import user as user_schema
from app.services import (
    auth_service,
    dashboard_service,
    instance_settings_service,
    oauth_service,
    share_service,
    user_service,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin-ui", tags=["admin-ui"])
templates = Jinja2Templates(directory="app/templates")


# Custom exception for admin auth redirect
class AdminAuthRedirect(Exception):
    """Raised when admin authentication fails and redirect is needed."""

    pass


# Authentication helper for cookie-based auth
def get_current_admin_from_cookie(
    request: Request,
    db: Session = Depends(get_db),
) -> models.User:
    """Get current admin user from cookie."""
    token = request.cookies.get("admin_token")
    if not token:
        raise AdminAuthRedirect()

    try:
        user = deps.get_current_user(db, token)
        if not user.is_admin:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Admin privileges required",
            )
        return user
    except HTTPException:
        raise AdminAuthRedirect()


# Helper to render templates with flash messages
def render_template(
    request: Request,
    template: str,
    context: dict | None = None,
    flash_message: str | None = None,
    flash_type: str = "success",
    db: Session | None = None,
) -> HTMLResponse:
    """Render template with common context."""
    ctx = context or {}
    ctx.update(
        {
            "request": request,
            "flash_message": flash_message,
            "flash_type": flash_type,
        }
    )
    # Add branding to all templates if db is available
    if db is not None and "branding" not in ctx:
        try:
            ctx["branding"] = instance_settings_service.get_branding(db)
        except Exception:
            ctx["branding"] = None
    return templates.TemplateResponse(template, ctx)


@router.get("", response_class=HTMLResponse)
def admin_ui_root(request: Request):
    """Redirect to login or dashboard."""
    token = request.cookies.get("admin_token")
    if token:
        return RedirectResponse("/admin-ui/dashboard", status_code=302)
    return RedirectResponse("/admin-ui/login", status_code=302)


@router.get("/login", response_class=HTMLResponse)
def login_page(request: Request, error: str | None = None):
    """Show login form."""
    # If already logged in, redirect to dashboard
    token = request.cookies.get("admin_token")
    if token:
        return RedirectResponse("/admin-ui/dashboard", status_code=302)

    return render_template(request, "admin/login.html", {"error": error})


@router.post("/login", response_class=HTMLResponse)
def login_submit(
    request: Request,
    email: Annotated[str, Form()],
    password: Annotated[str, Form()],
    db: Session = Depends(get_db),
):
    """Process login form."""
    try:
        # Authenticate user
        user = auth_service.authenticate_user(db, email, password)
        if not user or not user.is_admin:
            return render_template(
                request,
                "admin/login.html",
                {"error": "Invalid credentials or insufficient privileges", "email": email},
            )

        # Generate token
        token = auth_service.create_access_token(user.id)

        # Log the login
        auth_service.log_login(
            db,
            user,
            request.client.host if request.client else None,
            request.headers.get("user-agent"),
        )

        # Set cookie and redirect
        response = RedirectResponse("/admin-ui/dashboard", status_code=302)
        response.set_cookie(
            key="admin_token",
            value=token,
            httponly=True,
            max_age=3600,  # 1 hour
            samesite="lax",
        )
        return response

    except Exception as e:
        logger.error(f"Login failed for {email}: {type(e).__name__}: {e}", exc_info=True)
        return render_template(
            request,
            "admin/login.html",
            {"error": "Login failed. Please try again.", "email": email},
        )


@router.post("/logout", response_class=RedirectResponse)
def logout(
    request: Request,
    db: Session = Depends(get_db),
):
    """Logout user."""
    try:
        user = get_current_admin_from_cookie(request, db)
        # Log the logout
        auth_service.log_logout(
            db,
            user,
            request.client.host if request.client else None,
            request.headers.get("user-agent"),
        )
    except Exception:
        pass  # Continue with logout even if logging fails

    response = RedirectResponse("/admin-ui/login", status_code=302)
    response.delete_cookie("admin_token")
    return response


@router.get("/dashboard", response_class=HTMLResponse)
def dashboard(
    request: Request,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_admin_from_cookie),
):
    """Show admin dashboard."""
    stats = dashboard_service.get_admin_stats(db)

    return render_template(
        request,
        "admin/dashboard.html",
        {
            "user": current_user,
            "stats": stats,
            "active_page": "dashboard",
        },
        db=db,
    )


@router.get("/users", response_class=HTMLResponse)
def users_list(
    request: Request,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_admin_from_cookie),
):
    """Show users list."""
    users = user_service.list_users(db)

    return render_template(
        request,
        "admin/users.html",
        {
            "user": current_user,
            "users": users,
            "active_page": "users",
        },
        db=db,
    )


@router.get("/users/new", response_class=HTMLResponse)
def user_create_form(
    request: Request,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_admin_from_cookie),
):
    """Show create user form."""
    return render_template(
        request,
        "admin/user_form.html",
        {
            "user": current_user,
            "user_data": None,
            "active_page": "users",
        },
        db=db,
    )


@router.post("/users/new", response_class=RedirectResponse)
def user_create_submit(
    request: Request,
    email: Annotated[str, Form()],
    password: Annotated[str, Form()],
    is_admin: Annotated[str | None, Form()] = None,
    is_active: Annotated[str | None, Form()] = None,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_admin_from_cookie),
):
    """Process create user form."""
    try:
        payload = user_schema.UserCreate(
            email=email,
            password=password,
            is_admin=is_admin == "true",
            is_active=is_active == "true",
        )
        user_service.create_user(db, payload, actor_user_id=current_user.id)
        return RedirectResponse(
            "/admin-ui/users?success=User created successfully", status_code=302
        )
    except HTTPException as e:
        # Return to form with error
        return RedirectResponse(f"/admin-ui/users?error={e.detail}", status_code=302)


@router.get("/users/{user_id}/edit", response_class=HTMLResponse)
def user_edit_form(
    request: Request,
    user_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_admin_from_cookie),
):
    """Show edit user form."""
    user_data = user_service.get_user(db, user_id)

    return render_template(
        request,
        "admin/user_form.html",
        {
            "user": current_user,
            "user_data": user_data,
            "active_page": "users",
        },
        db=db,
    )


@router.post("/users/{user_id}/edit", response_class=RedirectResponse)
def user_edit_submit(
    request: Request,
    user_id: uuid.UUID,
    email: Annotated[str, Form()],
    password: Annotated[str | None, Form()] = None,
    is_admin: Annotated[str | None, Form()] = None,
    is_active: Annotated[str | None, Form()] = None,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_admin_from_cookie),
):
    """Process edit user form."""
    try:
        payload = user_schema.UserUpdate(
            email=email if email else None,
            password=password if password else None,
            is_admin=is_admin == "true",
            is_active=is_active == "true",
        )
        user_service.update_user(db, user_id, payload, actor_user_id=current_user.id)
        return RedirectResponse(
            "/admin-ui/users?success=User updated successfully", status_code=302
        )
    except HTTPException as e:
        return RedirectResponse(f"/admin-ui/users?error={e.detail}", status_code=302)


@router.post("/users/{user_id}/delete", response_class=RedirectResponse)
def user_delete(
    user_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_admin_from_cookie),
):
    """Delete user."""
    try:
        user_service.delete_user(db, user_id, actor_user_id=current_user.id)
        return RedirectResponse(
            "/admin-ui/users?success=User deleted successfully", status_code=302
        )
    except HTTPException as e:
        return RedirectResponse(f"/admin-ui/users?error={e.detail}", status_code=302)


@router.post("/users/{user_id}/toggle-admin", response_class=RedirectResponse)
def user_toggle_admin(
    user_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_admin_from_cookie),
):
    """Toggle admin role."""
    try:
        user_data = user_service.get_user(db, user_id)
        user_service.set_admin(db, user_id, not user_data.is_admin)
        return RedirectResponse("/admin-ui/users?success=Admin role toggled", status_code=302)
    except HTTPException as e:
        return RedirectResponse(f"/admin-ui/users?error={e.detail}", status_code=302)


@router.post("/users/{user_id}/toggle-active", response_class=RedirectResponse)
def user_toggle_active(
    user_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_admin_from_cookie),
):
    """Toggle active status."""
    try:
        user_data = user_service.get_user(db, user_id)
        payload = user_schema.UserUpdate(is_active=not user_data.is_active)
        user_service.update_user(db, user_id, payload, actor_user_id=current_user.id)
        return RedirectResponse("/admin-ui/users?success=User status toggled", status_code=302)
    except HTTPException as e:
        return RedirectResponse(f"/admin-ui/users?error={e.detail}", status_code=302)


# ==================== SHARES MANAGEMENT ====================


@router.get("/shares", response_class=HTMLResponse)
def shares_list(
    request: Request,
    kind: str | None = None,
    visibility: str | None = None,
    owner_id: str | None = None,
    search: str | None = None,
    page: str = "1",
    limit: str = "20",
    success: str | None = None,
    error: str | None = None,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_admin_from_cookie),
):
    """Show shares list with filters."""
    # Convert string params to proper types (handle invalid values gracefully)
    kind_enum: models.ShareKind | None = None
    visibility_enum: models.ShareVisibility | None = None
    owner_uuid: uuid.UUID | None = None
    page_int: int = 1
    limit_int: int = 20

    if kind:
        try:
            kind_enum = models.ShareKind(kind)
        except ValueError:
            kind = None  # Invalid value, ignore filter

    if visibility:
        try:
            visibility_enum = models.ShareVisibility(visibility)
        except ValueError:
            visibility = None  # Invalid value, ignore filter

    if owner_id:
        try:
            owner_uuid = uuid.UUID(owner_id)
        except ValueError:
            owner_id = None  # Invalid value, ignore filter

    try:
        page_int = int(page)
        if page_int < 1:
            page_int = 1
    except ValueError:
        page_int = 1

    try:
        limit_int = int(limit)
        if limit_int < 1 or limit_int > 100:
            limit_int = 20
    except ValueError:
        limit_int = 20

    # Pagination
    skip = (page_int - 1) * limit_int

    # Get shares and total count
    shares, total = share_service.list_all_shares_admin(
        db=db,
        kind=kind_enum,
        visibility=visibility_enum,
        owner_id=owner_uuid,
        search=search,
        skip=skip,
        limit=limit_int,
    )

    # Calculate pagination
    total_pages = (total + limit_int - 1) // limit_int if total > 0 else 1

    # Get all users for owner filter dropdown
    all_users = user_service.list_users(db)

    return render_template(
        request,
        "admin/shares.html",
        {
            "user": current_user,
            "shares": shares,
            "total": total,
            "page": page_int,
            "limit": limit_int,
            "total_pages": total_pages,
            "kind_filter": kind,
            "visibility_filter": visibility,
            "owner_filter": owner_uuid,
            "search_filter": search,
            "all_users": all_users,
            "active_page": "shares",
            "flash_message": success or error,
            "flash_type": "success" if success else "error" if error else None,
        },
        db=db,
    )


@router.get("/shares/{share_id}", response_class=HTMLResponse)
def share_detail(
    request: Request,
    share_id: uuid.UUID,
    success: str | None = None,
    error: str | None = None,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_admin_from_cookie),
):
    """Show share details with members."""
    try:
        share = share_service.get_share(db, share_id)
        members = share_service.list_members(db, share)
        all_users = user_service.list_users(db)

        # Filter out users who are already members or the owner
        member_user_ids = {m.user_id for m in members}
        available_users = [
            u for u in all_users if u.id not in member_user_ids and u.id != share.owner_user_id
        ]

        return render_template(
            request,
            "admin/share_detail.html",
            {
                "user": current_user,
                "share": share,
                "members": members,
                "available_users": available_users,
                "active_page": "shares",
                "flash_message": success or error,
                "flash_type": "success" if success else "error" if error else None,
            },
            db=db,
        )
    except HTTPException as e:
        return RedirectResponse(f"/admin-ui/shares?error={e.detail}", status_code=302)


@router.post("/shares/{share_id}/delete", response_class=RedirectResponse)
def share_delete(
    share_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_admin_from_cookie),
):
    """Delete share."""
    try:
        share = share_service.get_share(db, share_id)
        share_service.delete_share(db, share, actor_user_id=current_user.id)
        return RedirectResponse(
            "/admin-ui/shares?success=Share deleted successfully", status_code=302
        )
    except HTTPException as e:
        return RedirectResponse(f"/admin-ui/shares?error={e.detail}", status_code=302)


@router.post("/shares/{share_id}/edit", response_class=RedirectResponse)
def share_edit(
    share_id: uuid.UUID,
    visibility: Annotated[str, Form()],
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_admin_from_cookie),
):
    """Update share visibility."""
    try:
        share = share_service.get_share(db, share_id)
        # Map string to enum
        visibility_enum = models.ShareVisibility(visibility)
        share_service.update_share(
            db,
            share,
            share_schema.ShareUpdate(visibility=visibility_enum),
            actor_user_id=current_user.id,
        )
        return RedirectResponse(
            f"/admin-ui/shares/{share_id}?success=Share updated successfully", status_code=302
        )
    except ValueError:
        return RedirectResponse(
            f"/admin-ui/shares/{share_id}?error=Invalid visibility value", status_code=302
        )
    except HTTPException as e:
        return RedirectResponse(f"/admin-ui/shares/{share_id}?error={e.detail}", status_code=302)


@router.post("/shares/{share_id}/members", response_class=RedirectResponse)
def share_add_member(
    request: Request,
    share_id: uuid.UUID,
    user_id: Annotated[uuid.UUID, Form()],
    role: Annotated[models.ShareMemberRole, Form()],
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_admin_from_cookie),
):
    """Add member to share."""
    try:
        share = share_service.get_share(db, share_id)
        from app.schemas import share as share_schema

        payload = share_schema.ShareMemberCreate(user_id=user_id, role=role)
        share_service.add_member(db, share, payload, actor_user_id=current_user.id)
        return RedirectResponse(
            f"/admin-ui/shares/{share_id}?success=Member added successfully", status_code=302
        )
    except HTTPException as e:
        return RedirectResponse(f"/admin-ui/shares/{share_id}?error={e.detail}", status_code=302)


@router.post("/shares/{share_id}/members/{member_user_id}/role", response_class=RedirectResponse)
def share_update_member_role(
    share_id: uuid.UUID,
    member_user_id: uuid.UUID,
    role: Annotated[models.ShareMemberRole, Form()],
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_admin_from_cookie),
):
    """Update member role."""
    try:
        share = share_service.get_share(db, share_id)
        from app.schemas import share as share_schema

        payload = share_schema.ShareMemberUpdate(role=role)
        share_service.update_member_role(
            db, share, member_user_id, payload, actor_user_id=current_user.id
        )
        return RedirectResponse(
            f"/admin-ui/shares/{share_id}?success=Member role updated", status_code=302
        )
    except HTTPException as e:
        return RedirectResponse(f"/admin-ui/shares/{share_id}?error={e.detail}", status_code=302)


@router.post("/shares/{share_id}/members/{member_user_id}/remove", response_class=RedirectResponse)
def share_remove_member(
    share_id: uuid.UUID,
    member_user_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_admin_from_cookie),
):
    """Remove member from share."""
    try:
        share = share_service.get_share(db, share_id)
        share_service.remove_member(db, share, member_user_id, actor_user_id=current_user.id)
        return RedirectResponse(
            f"/admin-ui/shares/{share_id}?success=Member removed successfully", status_code=302
        )
    except HTTPException as e:
        return RedirectResponse(f"/admin-ui/shares/{share_id}?error={e.detail}", status_code=302)


# ==================== SETTINGS ====================


@router.get("/settings/oauth", response_class=HTMLResponse)
def oauth_settings(
    request: Request,
    success: str | None = None,
    error: str | None = None,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_admin_from_cookie),
):
    """Show OAuth/IAM configuration status (read-only)."""
    settings = get_settings()

    # Get OAuth providers from database
    providers = oauth_service.get_oauth_providers(db)

    # Build configuration status
    oauth_config = {
        "enabled": settings.oauth_enabled,
        "provider_name": settings.oauth_provider_name,
        "issuer_url": settings.oauth_issuer_url,
        "client_id": settings.oauth_client_id,
        "client_secret_set": bool(settings.oauth_client_secret),
        "scopes": settings.oauth_scopes,
        "auto_register": settings.oauth_auto_register,
        "sync_user_info": settings.oauth_sync_user_info,
        "admin_groups": settings.oauth_admin_groups,
        "default_role": settings.oauth_default_role,
    }

    # Configuration diagnostics
    diagnostics = []
    if settings.oauth_enabled:
        if not settings.oauth_issuer_url:
            diagnostics.append({"level": "error", "message": "OAUTH_ISSUER_URL is not set"})
        if not settings.oauth_client_id:
            diagnostics.append({"level": "error", "message": "OAUTH_CLIENT_ID is not set"})
        if not settings.oauth_client_secret:
            diagnostics.append({"level": "error", "message": "OAUTH_CLIENT_SECRET is not set"})
        if settings.oauth_default_role not in ("user", "admin"):
            diagnostics.append(
                {
                    "level": "warning",
                    "message": f"OAUTH_DEFAULT_ROLE should be 'user' or 'admin', "
                    f"got '{settings.oauth_default_role}'",
                }
            )
        if not diagnostics:
            diagnostics.append({"level": "success", "message": "OAuth configuration is valid"})
    else:
        diagnostics.append({"level": "info", "message": "OAuth is disabled (OAUTH_ENABLED=false)"})

    return render_template(
        request,
        "admin/settings_oauth.html",
        {
            "user": current_user,
            "oauth_config": oauth_config,
            "providers": providers,
            "diagnostics": diagnostics,
            "active_page": "settings",
            "flash_message": success or error,
            "flash_type": "success" if success else "error" if error else None,
        },
        db=db,
    )


@router.get("/settings/branding", response_class=HTMLResponse)
def branding_settings(
    request: Request,
    success: str | None = None,
    error: str | None = None,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_admin_from_cookie),
):
    """Show branding configuration page."""
    return render_template(
        request,
        "admin/settings_branding.html",
        {
            "user": current_user,
            "active_page": "settings",
            "flash_message": success or error,
            "flash_type": "success" if success else "error" if error else None,
        },
        db=db,
    )


@router.get("/api/branding")
def get_branding_api(
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_admin_from_cookie),
):
    """Get branding settings via cookie auth for admin UI."""
    branding_data = instance_settings_service.get_branding(db)
    return branding_data


@router.patch("/api/branding")
async def update_branding_api(
    request: Request,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_admin_from_cookie),
):
    """Update branding settings via cookie auth for admin UI."""
    import json

    body = await request.body()
    payload = json.loads(body)

    # Get current branding
    current = instance_settings_service.get_branding(db)

    # Update only provided fields
    name = payload.get("name", current["name"]) or current["name"]
    logo_url = payload.get("logo_url", current["logo_url"]) or current["logo_url"]
    favicon_url = payload.get("favicon_url", current["favicon_url"]) or current["favicon_url"]

    branding_data = instance_settings_service.set_branding(
        db=db,
        name=name,
        logo_url=logo_url,
        favicon_url=favicon_url,
    )
    return branding_data


@router.post("/api/upload")
async def upload_file(
    file: UploadFile,
    current_user: models.User = Depends(get_current_admin_from_cookie),
):
    """Upload a file (logo or favicon) for branding."""
    from pathlib import Path

    # Validate file type
    allowed_types = {
        "image/png",
        "image/jpeg",
        "image/gif",
        "image/svg+xml",
        "image/x-icon",
        "image/vnd.microsoft.icon",
    }
    if file.content_type not in allowed_types:
        raise HTTPException(status_code=400, detail=f"Invalid file type: {file.content_type}")

    # Validate file size (max 1MB)
    content = await file.read()
    if len(content) > 1024 * 1024:
        raise HTTPException(status_code=400, detail="File too large (max 1MB)")

    # Generate unique filename
    ext = Path(file.filename).suffix.lower() if file.filename else ".png"
    if ext not in {".png", ".jpg", ".jpeg", ".gif", ".svg", ".ico"}:
        ext = ".png"
    filename = f"{uuid.uuid4().hex}{ext}"

    # Save file - use absolute path based on working directory
    # In Docker: working dir is /app, static files are at /app/app/static
    upload_dir = Path("app/static/uploads")
    try:
        upload_dir.mkdir(parents=True, exist_ok=True)
        file_path = upload_dir / filename

        with open(file_path, "wb") as f:
            f.write(content)

        logger.info(f"File uploaded successfully: {file_path}")
        # Return URL
        return {"url": f"/static/uploads/{filename}"}
    except Exception as e:
        logger.error(f"Failed to save file: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to save file: {str(e)}")
