"""Web publishing public API endpoints."""

from __future__ import annotations

import base64
import io
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from minio import Minio
from minio.error import S3Error
from pydantic import BaseModel
from slowapi import Limiter
from slowapi.util import get_remote_address
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core import security
from app.core.config import get_settings
from app.db import models
from app.db.session import get_db
from app.services.web_session_service import WebSessionService

router = APIRouter(prefix="/v1/web", tags=["web"])
limiter = Limiter(key_func=get_remote_address)


class WebFolderItem(BaseModel):
    """Item in a folder for navigation."""

    path: str
    name: str
    type: str


class WebSharePublic(BaseModel):
    """Public share data for web rendering."""

    id: str
    kind: str
    path: str
    visibility: str
    web_slug: str
    web_noindex: bool
    created_at: datetime
    updated_at: datetime
    web_content: str | None = None
    web_content_updated_at: datetime | None = None
    web_folder_items: list[WebFolderItem] | None = None
    web_doc_id: str | None = None  # Y-sweet doc ID for real-time sync


class WebShareAuthRequest(BaseModel):
    """Request to authenticate for a protected share."""

    password: str


class WebSessionValidation(BaseModel):
    """Response for session validation."""

    valid: bool
    share_id: str | None = None


class WebFileSyncRequest(BaseModel):
    """Request to sync individual file content within a folder share."""

    content: str


class WebContentUpdateRequest(BaseModel):
    """Request to update document content."""

    content: str


class WebAssetUploadRequest(BaseModel):
    """Request to upload a web asset (image)."""

    path: str
    data: str  # base64-encoded binary data
    content_type: str


@router.get("/shares/{slug}", response_model=WebSharePublic)
def get_share_by_slug(
    slug: str,
    db: Session = Depends(get_db),
) -> WebSharePublic:
    """
    Get share information by web slug (public API).

    This endpoint is used by the web publishing frontend to fetch share metadata.
    Access control is handled separately - this only returns metadata for published shares.

    Returns 404 if:
    - Share does not exist
    - Share is not web published
    - Share's web_slug doesn't match

    For PROTECTED shares, the client must call POST /web/shares/{slug}/auth separately.
    For PRIVATE shares, the client must authenticate via normal login flow.
    """
    settings = get_settings()
    if not settings.web_publish_enabled:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Web publishing is not enabled on this server",
        )

    # Find share by slug
    stmt = select(models.Share).where(
        models.Share.web_slug == slug,
        models.Share.web_published == True,  # noqa: E712
    )
    share = db.execute(stmt).scalar_one_or_none()

    if not share:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Share not found or not published",
        )

    # Parse folder items if present
    folder_items = None
    if share.web_folder_items:
        folder_items = [WebFolderItem(**item) for item in share.web_folder_items]

    return WebSharePublic(
        id=str(share.id),
        kind=share.kind.value,
        path=share.path,
        visibility=share.visibility.value,
        web_slug=share.web_slug,
        web_noindex=share.web_noindex,
        created_at=share.created_at,
        updated_at=share.updated_at,
        web_content=share.web_content,
        web_content_updated_at=share.web_content_updated_at,
        web_folder_items=folder_items,
        web_doc_id=share.web_doc_id,
    )


@router.post("/shares/{slug}/auth", status_code=status.HTTP_200_OK)
@limiter.limit("5/minute")  # Max 5 password attempts per minute per IP
def authenticate_protected_share(
    request: Request,
    slug: str,
    payload: WebShareAuthRequest,
    response: Response,
    db: Session = Depends(get_db),
) -> dict:
    """
    Authenticate for a protected share using password.

    On success, sets a secure HttpOnly cookie with a session token.
    Rate limited to 5 attempts per minute per IP to prevent brute force attacks.

    Returns:
        Success message with share_id
        Cookie: web_session={token}; HttpOnly; Secure; SameSite=Strict
    """
    settings = get_settings()
    if not settings.web_publish_enabled:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Web publishing is not enabled on this server",
        )

    # Find share
    stmt = select(models.Share).where(
        models.Share.web_slug == slug,
        models.Share.web_published == True,  # noqa: E712
    )
    share = db.execute(stmt).scalar_one_or_none()

    if not share:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Share not found or not published",
        )

    # Verify it's a protected share
    if share.visibility != models.ShareVisibility.PROTECTED:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="This endpoint is only for protected shares",
        )

    # Verify password
    if not share.password_hash or not security.verify_password(
        payload.password, share.password_hash
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid password",
        )

    # Create session token (24h expiry)
    session_token = WebSessionService.create_web_session(share.id, hours=24)

    # Set secure cookie
    # Note: In production, Secure flag is set automatically via HTTPS
    response.set_cookie(
        key="web_session",
        value=session_token,
        max_age=86400,  # 24 hours in seconds
        httponly=True,
        secure=True,  # Only send over HTTPS
        samesite="strict",  # CSRF protection
        path="/",
    )

    return {"message": "Authentication successful", "share_id": str(share.id)}


@router.get("/shares/{slug}/validate", response_model=WebSessionValidation)
def validate_share_session(
    slug: str,
    request: Request,
    db: Session = Depends(get_db),
) -> WebSessionValidation:
    """
    Validate if user has a valid session for a protected share.

    Checks the web_session cookie and returns validation status.
    This endpoint is called by the web frontend to determine if password prompt is needed.
    """
    settings = get_settings()
    if not settings.web_publish_enabled:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Web publishing is not enabled on this server",
        )

    # Find share
    stmt = select(models.Share).where(
        models.Share.web_slug == slug,
        models.Share.web_published == True,  # noqa: E712
    )
    share = db.execute(stmt).scalar_one_or_none()

    if not share:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Share not found or not published",
        )

    # Only validate sessions for protected shares
    if share.visibility != models.ShareVisibility.PROTECTED:
        return WebSessionValidation(valid=True, share_id=str(share.id))

    # Check for session cookie
    session_token = request.cookies.get("web_session")
    if not session_token:
        return WebSessionValidation(valid=False, share_id=None)

    # Validate token
    try:
        is_valid = WebSessionService.validate_web_session(session_token, share.id)
        return WebSessionValidation(valid=is_valid, share_id=str(share.id) if is_valid else None)
    except HTTPException:
        return WebSessionValidation(valid=False, share_id=None)


class WebRelayTokenResponse(BaseModel):
    """Response with relay token for real-time sync."""

    relay_url: str
    token: str
    doc_id: str
    expires_at: datetime


@router.get("/shares/{slug}/token", response_model=WebRelayTokenResponse)
def get_web_relay_token(
    slug: str,
    request: Request,
    db: Session = Depends(get_db),
) -> WebRelayTokenResponse:
    """
    Get a relay token for real-time sync via WebSocket.

    Returns a read-only Ed25519-signed JWT token for connecting to y-sweet relay server.
    Access is validated based on share visibility:
    - PUBLIC: Anyone can get a token
    - PROTECTED: Requires valid web_session cookie
    - PRIVATE: Not supported via web (returns 403)

    Returns 404 if:
    - Share not found or not published
    - Share has no web_doc_id configured

    Returns 403 if:
    - Share is private
    - Share is protected but no valid session
    """
    from datetime import timedelta

    settings = get_settings()
    if not settings.web_publish_enabled:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Web publishing is not enabled on this server",
        )

    # Find share
    stmt = select(models.Share).where(
        models.Share.web_slug == slug,
        models.Share.web_published == True,  # noqa: E712
    )
    share = db.execute(stmt).scalar_one_or_none()

    if not share:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Share not found or not published",
        )

    # Check if share has doc_id for real-time sync
    if not share.web_doc_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Real-time sync not configured for this share. Sync from Obsidian plugin first.",
        )

    # Check access based on visibility
    if share.visibility == models.ShareVisibility.PRIVATE:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Private shares do not support web real-time sync",
        )

    if share.visibility == models.ShareVisibility.PROTECTED:
        # Validate session cookie
        session_token = request.cookies.get("web_session")
        if not session_token:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Authentication required for protected share",
            )
        if not WebSessionService.validate_web_session(session_token, share.id):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Invalid or expired session",
            )

    # Generate read-only relay token
    expires_in = timedelta(minutes=settings.relay_token_ttl_minutes)
    expires_at = security.utcnow() + expires_in

    private_key = request.app.state.relay_private_key
    key_id = request.app.state.relay_key_id

    token = security.create_relay_token(
        private_key=private_key,
        key_id=key_id,
        doc_id=share.web_doc_id,
        mode="read",  # Web viewers only get read access
        expires_minutes=settings.relay_token_ttl_minutes,
    )

    return WebRelayTokenResponse(
        relay_url=str(settings.relay_public_url).rstrip("/"),
        token=token,
        doc_id=share.web_doc_id,
        expires_at=expires_at,
    )


@router.post("/shares/{slug}/files", status_code=status.HTTP_200_OK)
@limiter.limit("30/minute")  # Rate limit for content sync
def sync_folder_file_content(
    request: Request,
    slug: str,
    path: str = Query(..., description="File path within folder"),
    payload: WebFileSyncRequest = ...,
    db: Session = Depends(get_db),
) -> dict:
    """
    Sync individual file content within a folder share.

    This endpoint is called by the Obsidian plugin to sync content of files
    within a folder share. The content is stored in the web_folder_items JSONB field.

    Access control:
    - This is an authenticated endpoint (requires valid session or user token)
    - Only the share owner can sync content
    """
    settings = get_settings()
    if not settings.web_publish_enabled:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Web publishing is not enabled on this server",
        )

    # Find share
    stmt = select(models.Share).where(
        models.Share.web_slug == slug,
        models.Share.web_published == True,  # noqa: E712
    )
    share = db.execute(stmt).scalar_one_or_none()

    if not share:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Share not found or not published",
        )

    # Must be a folder share
    if share.kind != models.ShareKind.FOLDER:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="This endpoint is only for folder shares",
        )

    # For now, require authentication via session cookie
    # TODO: Support plugin authentication via JWT token
    session_token = request.cookies.get("web_session")
    if not session_token:
        # Try to validate JWT token from Authorization header
        auth_header = request.headers.get("Authorization")
        if not auth_header or not auth_header.startswith("Bearer "):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Authentication required",
            )
        # For now, just check if token exists - proper validation would require
        # checking user ownership of the share
        # This will be handled by plugin sending proper JWT tokens

    # Update folder items with content
    folder_items = share.web_folder_items or []
    updated = False

    for item in folder_items:
        if item.get("path") == path:
            item["content"] = payload.content
            updated = True
            break

    if not updated:
        # File not in folder items - add it
        folder_items.append(
            {
                "path": path,
                "name": path.split("/")[-1],
                "type": "doc",
                "content": payload.content,
            }
        )

    # Mark as modified to trigger SQLAlchemy update
    from sqlalchemy.orm.attributes import flag_modified

    share.web_folder_items = folder_items
    flag_modified(share, "web_folder_items")
    db.commit()

    return {"message": "File content synced", "path": path}


@router.get("/shares/{slug}/files", response_model=dict)
def get_folder_file_content(
    slug: str,
    path: str = Query(..., description="File path within folder"),
    request: Request = None,
    db: Session = Depends(get_db),
) -> dict:
    """
    Get individual file content from a folder share.

    Returns the content stored for a specific file within a folder share.
    Access control is based on share visibility.
    """
    settings = get_settings()
    if not settings.web_publish_enabled:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Web publishing is not enabled on this server",
        )

    # Find share
    stmt = select(models.Share).where(
        models.Share.web_slug == slug,
        models.Share.web_published == True,  # noqa: E712
    )
    share = db.execute(stmt).scalar_one_or_none()

    if not share:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Share not found or not published",
        )

    # Must be a folder share
    if share.kind != models.ShareKind.FOLDER:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="This endpoint is only for folder shares",
        )

    # Check access based on visibility
    if share.visibility == models.ShareVisibility.PROTECTED:
        session_token = request.cookies.get("web_session")
        if not session_token:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Authentication required for protected share",
            )
        if not WebSessionService.validate_web_session(session_token, share.id):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Invalid or expired session",
            )

    if share.visibility == models.ShareVisibility.PRIVATE:
        # Validate user JWT token
        auth_header = request.headers.get("Authorization")
        if not auth_header or not auth_header.startswith("Bearer "):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Private shares require user authentication",
            )

        # Validate JWT and check user has access to share
        token = auth_header.split(" ")[1]
        try:
            from uuid import UUID

            from app.core import security as sec_module

            payload = sec_module.decode_access_token(token)
            user_id_str = payload.get("sub")
            if not user_id_str:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Invalid token",
                )

            user_id = UUID(user_id_str)
            # Check if user is owner or member of the share
            if share.owner_user_id != user_id:
                member_stmt = select(models.ShareMember).where(
                    models.ShareMember.share_id == share.id,
                    models.ShareMember.user_id == user_id,
                )
                member = db.execute(member_stmt).scalar_one_or_none()
                if not member:
                    raise HTTPException(
                        status_code=status.HTTP_403_FORBIDDEN,
                        detail="You do not have access to this share",
                    )
        except HTTPException:
            raise
        except Exception:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Invalid or expired token",
            )

    # Find file in folder items
    folder_items = share.web_folder_items or []
    for item in folder_items:
        if item.get("path") == path:
            return {
                "path": path,
                "name": item.get("name"),
                "type": item.get("type"),
                "content": item.get("content", ""),
            }

    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail="File not found in folder share",
    )


@router.put("/shares/{slug}/content", status_code=status.HTTP_200_OK)
@limiter.limit("30/minute")  # Rate limit for content updates
def update_share_content(
    request: Request,
    slug: str,
    content_update: WebContentUpdateRequest,
    db: Session = Depends(get_db),
) -> dict:
    """
    Update document content for web editing.

    This endpoint allows users with editor role to update the content of a document.
    Only works for document shares (not folders).

    Access control:
    - Requires authentication via session cookie (for protected shares) or JWT token
    - User must have editor role on the share
    """
    settings = get_settings()
    if not settings.web_publish_enabled:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Web publishing is not enabled on this server",
        )

    # Find share
    stmt = select(models.Share).where(
        models.Share.web_slug == slug,
        models.Share.web_published == True,  # noqa: E712
    )
    share = db.execute(stmt).scalar_one_or_none()

    if not share:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Share not found or not published",
        )

    # Must be a document share
    if share.kind != models.ShareKind.DOC:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Only document shares can be edited via web",
        )

    # Check authentication and editor role
    # For protected shares, validate session
    if share.visibility == models.ShareVisibility.PROTECTED:
        session_token = request.cookies.get("web_session")
        if not session_token:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Authentication required",
            )
        if not WebSessionService.validate_web_session(session_token, share.id):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Invalid or expired session",
            )
        # For protected shares with password, we can't determine editor role
        # So we allow editing if authenticated
    elif share.visibility == models.ShareVisibility.PRIVATE:
        # For private shares, require JWT token and check editor role
        auth_header = request.headers.get("Authorization")
        if not auth_header or not auth_header.startswith("Bearer "):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Authentication required",
            )

        # Validate JWT and get user
        token = auth_header.split(" ")[1]
        try:
            from app.core import security as sec_module

            payload = sec_module.decode_access_token(token)
            user_id_str = payload.get("sub")
            if not user_id_str:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Invalid token",
                )

            # Check if user is owner or has editor role
            from uuid import UUID

            user_id = UUID(user_id_str)
            if share.owner_user_id == user_id:
                # Owner can always edit
                pass
            else:
                # Check if user has editor role
                member_stmt = select(models.ShareMember).where(
                    models.ShareMember.share_id == share.id,
                    models.ShareMember.user_id == user_id,
                    models.ShareMember.role == models.ShareMemberRole.EDITOR,
                )
                member = db.execute(member_stmt).scalar_one_or_none()
                if not member:
                    raise HTTPException(
                        status_code=status.HTTP_403_FORBIDDEN,
                        detail="Editor role required to edit this share",
                    )
        except Exception:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid token",
            )
    else:
        # Public shares - anyone can view but editing requires authentication
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Public shares cannot be edited via web. Use protected or private visibility.",
        )

    # Update content
    share.web_content = content_update.content
    share.web_content_updated_at = security.utcnow()
    db.commit()

    return {
        "message": "Content updated successfully",
        "updated_at": share.web_content_updated_at.isoformat(),
    }


@router.get("/robots.txt", response_class=Response)
def get_robots_txt(db: Session = Depends(get_db)) -> Response:
    """
    Dynamic robots.txt for web publishing domain.

    Default behavior: Disallow all (Disallow: /)
    For shares with web_noindex=false: Add Allow: /{slug}

    This ensures only shares explicitly marked for indexing are crawlable.
    """
    settings = get_settings()
    if not settings.web_publish_enabled:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Web publishing is not enabled",
        )

    # Start with default deny all
    lines = [
        "User-agent: *",
        "Disallow: /",
        "",
    ]

    # Find all published shares that allow indexing
    stmt = select(models.Share).where(
        models.Share.web_published == True,  # noqa: E712
        models.Share.web_noindex == False,  # noqa: E712
        models.Share.web_slug.isnot(None),
    )
    indexable_shares = db.execute(stmt).scalars().all()

    # Add Allow rules for indexable shares
    if indexable_shares:
        lines.append("# Indexable shares")
        for share in indexable_shares:
            lines.append(f"Allow: /{share.web_slug}")
        lines.append("")

    # Add sitemap reference (for future implementation)
    if indexable_shares:
        domain = settings.web_publish_domain
        if not domain.startswith("http"):
            domain = f"https://{domain}"
        lines.append(f"Sitemap: {domain.rstrip('/')}/sitemap.xml")

    content = "\n".join(lines)
    return Response(content=content, media_type="text/plain")


def _get_minio_client() -> Minio:
    """Create MinIO client from settings."""
    settings = get_settings()
    return Minio(
        settings.minio_endpoint,
        access_key=settings.minio_access_key,
        secret_key=settings.minio_secret_key,
        secure=settings.minio_secure,
    )


def _ensure_minio_bucket(client: Minio, bucket_name: str) -> None:
    """Ensure MinIO bucket exists, create if not."""
    try:
        if not client.bucket_exists(bucket_name):
            client.make_bucket(bucket_name)
    except S3Error as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to access storage: {e}",
        )


# Allowed image content types for web assets
ALLOWED_IMAGE_TYPES = {
    "image/png",
    "image/jpeg",
    "image/gif",
    "image/webp",
    "image/svg+xml",
    "image/bmp",
    "image/x-icon",
}


@router.post("/shares/{slug}/assets", status_code=status.HTTP_201_CREATED)
@limiter.limit("20/minute")  # Rate limit for asset uploads
def upload_web_asset(
    request: Request,
    slug: str,
    payload: WebAssetUploadRequest,
    db: Session = Depends(get_db),
) -> dict:
    """
    Upload a web asset (image) for a folder share.

    Stores the binary data in MinIO under key `web-assets/{share_id}/{path}`.
    Only authenticated users who are admin or owner/member of the share can upload.

    Max size: 5MB
    Allowed content types: PNG, JPEG, GIF, WebP, SVG, BMP, ICO
    """
    settings = get_settings()
    if not settings.web_publish_enabled:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Web publishing is not enabled on this server",
        )

    # Validate content type
    if payload.content_type not in ALLOWED_IMAGE_TYPES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid content type. Allowed: {', '.join(ALLOWED_IMAGE_TYPES)}",
        )

    # Decode base64 data
    try:
        binary_data = base64.b64decode(payload.data)
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid base64 data",
        )

    # Check max size (5MB)
    if len(binary_data) > 5 * 1024 * 1024:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail="Asset too large. Max size: 5MB",
        )

    # Find share
    stmt = select(models.Share).where(
        models.Share.web_slug == slug,
        models.Share.web_published == True,  # noqa: E712
    )
    share = db.execute(stmt).scalar_one_or_none()

    if not share:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Share not found or not published",
        )

    # Authenticate user via JWT token
    auth_header = request.headers.get("Authorization")
    if not auth_header or not auth_header.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
        )

    token = auth_header.split(" ")[1]
    try:
        from uuid import UUID

        from app.core import security as sec_module

        payload_jwt = sec_module.decode_access_token(token)
        user_id_str = payload_jwt.get("sub")
        if not user_id_str:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid token",
            )

        user_id = UUID(user_id_str)

        # Load user to check if admin
        user_stmt = select(models.User).where(models.User.id == user_id)
        user = db.execute(user_stmt).scalar_one_or_none()
        if not user:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="User not found",
            )

        # Check if user is admin, owner, or member of the share
        is_authorized = user.is_admin or share.owner_user_id == user_id
        if not is_authorized:
            member_stmt = select(models.ShareMember).where(
                models.ShareMember.share_id == share.id,
                models.ShareMember.user_id == user_id,
            )
            member = db.execute(member_stmt).scalar_one_or_none()
            is_authorized = member is not None

        if not is_authorized:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You do not have access to this share",
            )
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
        )

    # Upload to MinIO
    client = _get_minio_client()
    bucket_name = settings.minio_bucket
    _ensure_minio_bucket(client, bucket_name)

    object_name = f"web-assets/{share.id}/{payload.path}"
    data_stream = io.BytesIO(binary_data)

    try:
        client.put_object(
            bucket_name,
            object_name,
            data_stream,
            length=len(binary_data),
            content_type=payload.content_type,
        )
    except S3Error as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to upload asset: {e}",
        )

    return {
        "message": "Asset uploaded successfully",
        "path": payload.path,
        "size": len(binary_data),
    }


@router.get("/shares/{slug}/assets")
def serve_web_asset(
    slug: str,
    path: str = Query(..., description="Asset path within share"),
    request: Request = None,
    db: Session = Depends(get_db),
) -> Response:
    """
    Serve a web asset (image) from a folder share.

    Reads from MinIO bucket key `web-assets/{share_id}/{path}`.
    No auth required for public shares, session/JWT required for protected/private.
    """
    settings = get_settings()
    if not settings.web_publish_enabled:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Web publishing is not enabled on this server",
        )

    # Find share
    stmt = select(models.Share).where(
        models.Share.web_slug == slug,
        models.Share.web_published == True,  # noqa: E712
    )
    share = db.execute(stmt).scalar_one_or_none()

    if not share:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Share not found or not published",
        )

    # Check access based on visibility (same logic as get_folder_file_content)
    if share.visibility == models.ShareVisibility.PROTECTED:
        session_token = request.cookies.get("web_session")
        if not session_token:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Authentication required for protected share",
            )
        if not WebSessionService.validate_web_session(session_token, share.id):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Invalid or expired session",
            )

    if share.visibility == models.ShareVisibility.PRIVATE:
        # Validate user JWT token
        auth_header = request.headers.get("Authorization")
        if not auth_header or not auth_header.startswith("Bearer "):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Private shares require user authentication",
            )

        # Validate JWT and check user has access to share
        token = auth_header.split(" ")[1]
        try:
            from uuid import UUID

            from app.core import security as sec_module

            payload_jwt = sec_module.decode_access_token(token)
            user_id_str = payload_jwt.get("sub")
            if not user_id_str:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Invalid token",
                )

            user_id = UUID(user_id_str)
            # Check if user is owner or member of the share
            if share.owner_user_id != user_id:
                member_stmt = select(models.ShareMember).where(
                    models.ShareMember.share_id == share.id,
                    models.ShareMember.user_id == user_id,
                )
                member = db.execute(member_stmt).scalar_one_or_none()
                if not member:
                    raise HTTPException(
                        status_code=status.HTTP_403_FORBIDDEN,
                        detail="You do not have access to this share",
                    )
        except HTTPException:
            raise
        except Exception:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Invalid or expired token",
            )

    # Read from MinIO
    client = _get_minio_client()
    bucket_name = settings.minio_bucket
    object_name = f"web-assets/{share.id}/{path}"

    try:
        response = client.get_object(bucket_name, object_name)
        try:
            data = response.read()
            content_type = response.headers.get("Content-Type", "application/octet-stream")
        finally:
            response.close()
            response.release_conn()

        # Set cache headers for public shares
        headers = {}
        if share.visibility == models.ShareVisibility.PUBLIC:
            headers["Cache-Control"] = "public, max-age=86400"

        return Response(content=data, media_type=content_type, headers=headers)
    except S3Error as e:
        if e.code == "NoSuchKey":
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Asset not found",
            )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to retrieve asset: {e}",
        )
