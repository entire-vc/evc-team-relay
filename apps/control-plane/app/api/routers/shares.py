from __future__ import annotations

import re
import uuid
from datetime import timedelta
from typing import Any
from urllib.parse import quote

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response, status
from jwt.exceptions import InvalidTokenError
from minio import Minio
from minio.error import S3Error
from pydantic import BaseModel
from slowapi import Limiter
from slowapi.util import get_remote_address
from sqlalchemy import select
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from app.api import deps
from app.core import security
from app.core.config import get_settings
from app.db import models
from app.db.session import get_db
from app.schemas import share as share_schema
from app.services import share_service
from app.services.notification_service import get_notification_service

router = APIRouter(prefix="/shares", tags=["shares"])
limiter = Limiter(key_func=get_remote_address)


@router.get("", response_model=list[share_schema.ShareListItem])
def list_shares(
    kind: models.ShareKind | None = Query(
        default=None, description="Filter by share kind (doc or folder)"
    ),
    owned_only: bool = Query(default=False, description="Only return shares owned by current user"),
    member_only: bool = Query(
        default=False, description="Only return shares where user is a member"
    ),
    skip: int = Query(default=0, ge=0, description="Pagination offset"),
    limit: int = Query(default=50, ge=1, le=100, description="Maximum results per page"),
    db: Session = Depends(get_db),
    current_user: models.User = Depends(deps.get_current_user),
):
    """
    List all shares accessible to the current user.

    Returns shares where user is either:
    - Owner of the share
    - Explicit member of the share

    Use filters to narrow down results:
    - kind: Filter by 'doc' or 'folder'
    - owned_only: Only show shares you own
    - member_only: Only show shares where you're a member (not owner)
    - skip/limit: Pagination
    """
    return share_service.list_user_shares(
        db=db,
        user=current_user,
        kind=kind,
        owned_only=owned_only,
        member_only=member_only,
        skip=skip,
        limit=limit,
    )


@router.post("", response_model=share_schema.ShareRead, status_code=status.HTTP_201_CREATED)
@limiter.limit("20/minute")  # Max 20 share creations per minute per IP
async def create_share(
    request: Request,
    payload: share_schema.ShareCreate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(deps.get_current_user),
):
    share = share_service.create_share(db, current_user, payload)

    # Queue notifications (queues to DB, actual delivery is async via workers)
    notification_service = get_notification_service()
    ip_address = request.client.host if request.client else None
    user_agent = request.headers.get("user-agent")

    await notification_service.notify_share_created(db, share, current_user, ip_address, user_agent)

    # Build response with web_url
    response = share_schema.ShareRead.model_validate(share)
    response.web_url = share_service.get_web_url(share)
    return response


@router.get("/{share_id}", response_model=share_schema.ShareRead)
def read_share(
    share_id: uuid.UUID,
    password: str | None = Query(default=None),
    db: Session = Depends(get_db),
    current_user: models.User | None = Depends(deps.get_optional_user),
):
    share = share_service.get_share(db, share_id)
    share_service.ensure_read_access(db, share, current_user, password=password)
    # Build response with web_url
    response = share_schema.ShareRead.model_validate(share)
    response.web_url = share_service.get_web_url(share)
    return response


class SyncArtifactItem(BaseModel):
    path: str
    sha256: str
    size: int
    updated_at: str
    type: str = "sync-artifact"


def _get_minio_client() -> Minio:
    settings = get_settings()
    return Minio(
        settings.minio_endpoint,
        access_key=settings.minio_access_key,
        secret_key=settings.minio_secret_key,
        secure=settings.minio_secure,
    )


def _ensure_minio_bucket(client: Minio, bucket_name: str) -> None:
    try:
        if not client.bucket_exists(bucket_name):
            client.make_bucket(bucket_name)
    except S3Error as e:
        raise HTTPException(status_code=500, detail=f"Failed to access storage: {e}")


@router.get("/{share_id}/files-index", response_model=list[SyncArtifactItem])
def get_share_files_index(
    share_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(deps.get_current_user),
) -> list[SyncArtifactItem]:
    """
    List sync-artifact files for a share (plugin inbound sync, Bearer JWT).

    Returns only items uploaded via sync-upload (source=sync-artifact) in
    SyncArtifactItem format expected by the Obsidian plugin InboundFileDownloader.
    """
    share = share_service.get_share(db, share_id)
    share_service.ensure_read_access(db, share, current_user)

    folder_items = share.web_folder_items or []
    result = []
    for item in folder_items:
        if item.get("source") != "sync-artifact":
            continue
        sha256 = item.get("sha256")
        if not sha256:
            continue
        result.append(
            SyncArtifactItem(
                path=item["path"],
                sha256=sha256,
                size=item.get("size", 0),
                updated_at=item.get("modified_at", ""),
            )
        )
    return result


@router.get("/{share_id}/download")
def download_share_file(
    share_id: uuid.UUID,
    path: str = Query(..., description="Relative file path within share"),
    db: Session = Depends(get_db),
    current_user: models.User = Depends(deps.get_current_user),
) -> Response:
    """
    Download a sync-artifact file by relative path (plugin inbound sync, Bearer JWT).

    Resolution order: inline content in JSONB index → MinIO storage.
    """
    share = share_service.get_share(db, share_id)
    share_service.ensure_read_access(db, share, current_user)

    folder_items = share.web_folder_items or []
    for item in folder_items:
        if item.get("path") != path:
            continue

        content_str = item.get("content")
        if content_str is not None:
            mime = item.get("mime", "text/plain; charset=utf-8")
            content_bytes = (
                content_str.encode("utf-8") if isinstance(content_str, str) else content_str
            )
            return Response(content=content_bytes, media_type=mime)

        storage_key = item.get("storage_key")
        if storage_key:
            settings = get_settings()
            minio_client = _get_minio_client()
            try:
                resp = minio_client.get_object(settings.minio_bucket, storage_key)
                try:
                    data = resp.read()
                finally:
                    resp.close()
                    resp.release_conn()
            except S3Error as e:
                if e.code == "NoSuchKey":
                    raise HTTPException(
                        status_code=status.HTTP_404_NOT_FOUND,
                        detail="File not found in storage",
                    )
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail=f"Failed to retrieve file: {e}",
                )
            mime = item.get("mime", "application/octet-stream")
            return Response(content=data, media_type=mime)

    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail=f"File not found: {path}",
    )


# --- Attachment (CAS) file-token routes -------------------------------------
#
# TR-09: gives the plugin's CAS.ts a JWT-authed presigned-URL contract for
# real Obsidian users, matching the shape the agent-key path already uses
# (web.py's upload_mesh_artifact, same web-assets/{share_id}/{path} bucket
# layout) — extended to a second auth mode rather than a new architecture.
#
# CAS.ts requests exactly one token per operation via a single call shape
# (path, sha256, content_type, content_length — no read/write intent), then
# reuses that token across HEAD (verify) / GET .../download-url (readFile) /
# POST .../upload-url (writeFile). So the token itself only proves "this user
# has SOME access to this exact share+path right now" — each of the three
# consuming routes below independently re-checks read vs write access via
# ensure_read_access/ensure_write_access, rather than trusting a mode baked
# into the token. Least-privilege, short expiry (10 min), single path scope.

_ALLOWED_FILE_PATH_RE = re.compile(r"^[a-zA-Z0-9._\-/А-Яа-я ]+$")
FILE_TOKEN_EXPIRE_MINUTES = 10


class FileTokenRequest(BaseModel):
    path: str
    sha256: str
    content_type: str
    content_length: int


class FileTokenResponse(BaseModel):
    token: str
    base_url: str
    expires_at: str


class DownloadUrlResponse(BaseModel):
    downloadUrl: str


class UploadUrlResponse(BaseModel):
    uploadUrl: str


def _validate_file_path(path: str) -> str:
    """Same rules as web.py's _validate_upload_path (mesh-artifact path), kept
    as a local copy since neither router currently shares such helpers."""
    if not path:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="path is required")
    if path.startswith("/"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="path must be relative (no leading /)"
        )
    if len(path) > 512:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="path too long (max 512 chars)"
        )
    segments = path.split("/")
    if ".." in segments:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="path traversal not allowed"
        )
    if path.count("/") > 6:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="path too deep (max 6 levels)"
        )
    if not _ALLOWED_FILE_PATH_RE.match(path):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="path contains invalid characters"
        )
    return path


def _decode_file_token(share_id: uuid.UUID, path: str, authorization: str | None) -> dict[str, Any]:
    """Decode+validate the Bearer file-token on HEAD/download-url/upload-url.

    Deliberately NOT deps.get_current_user — that accepts normal session
    JWTs, and (per its own guard) now explicitly REJECTS scope=file tokens.
    This is the file-token-only counterpart.
    """
    if not authorization:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing credentials")
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid Authorization header"
        )
    try:
        payload = security.decode_access_token(token)
    except InvalidTokenError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token"
        ) from exc
    if payload.get("scope") != "file":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")
    if payload.get("share_id") != str(share_id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Token not valid for this share"
        )
    if payload.get("path") != path:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Token not valid for this path"
        )
    return payload


def _user_from_file_token(db: Session, payload: dict[str, Any]) -> models.User:
    subject = payload.get("sub")
    try:
        user_id = uuid.UUID(str(subject))
    except (TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token payload"
        ) from exc
    user = db.execute(select(models.User).where(models.User.id == user_id)).scalar_one_or_none()
    if not user or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="User is inactive or missing"
        )
    return user


@router.post("/{share_id}/file-token", response_model=FileTokenResponse)
def create_file_token(
    share_id: uuid.UUID,
    payload: FileTokenRequest,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(deps.get_current_user),
) -> FileTokenResponse:
    """Mint a short-lived, path-scoped token for attachment (CAS) access.

    Minimum bar to mint at all is read access — write is re-checked
    independently at upload-url, so a viewer can verify/download but a
    write-url request 403s unless they actually have write access.
    """
    share = share_service.get_share(db, share_id)
    share_service.ensure_read_access(db, share, current_user)
    path = _validate_file_path(payload.path)

    token = security.create_file_token(
        subject=str(current_user.id),
        share_id=str(share_id),
        path=path,
        sha256=payload.sha256,
        content_type=payload.content_type,
        content_length=payload.content_length,
        expires_minutes=FILE_TOKEN_EXPIRE_MINUTES,
    )
    expires_at = (security.utcnow() + timedelta(minutes=FILE_TOKEN_EXPIRE_MINUTES)).isoformat()
    settings = get_settings()
    base = settings.control_plane_public_url.rstrip("/")
    base_url = f"{base}/shares/{share_id}/files/{quote(path, safe='/')}"
    return FileTokenResponse(token=token, base_url=base_url, expires_at=expires_at)


@router.head("/{share_id}/files/{path:path}")
def head_share_file(
    share_id: uuid.UUID,
    path: str,
    db: Session = Depends(get_db),
    authorization: str | None = Header(default=None, alias="Authorization"),
) -> Response:
    """CAS.ts verify(): does this attachment already exist in storage?"""
    token_payload = _decode_file_token(share_id, path, authorization)
    user = _user_from_file_token(db, token_payload)
    share = share_service.get_share(db, share_id)
    share_service.ensure_read_access(db, share, user)

    settings = get_settings()
    object_name = f"web-assets/{share_id}/{path}"
    minio_client = _get_minio_client()
    try:
        minio_client.stat_object(settings.minio_bucket, object_name)
    except S3Error as e:
        if e.code in ("NoSuchKey", "NoSuchObject"):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="File not found"
            ) from e
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to stat file: {e}"
        ) from e
    return Response(status_code=status.HTTP_200_OK)


@router.get("/{share_id}/files/{path:path}/download-url", response_model=DownloadUrlResponse)
def get_file_download_url(
    share_id: uuid.UUID,
    path: str,
    db: Session = Depends(get_db),
    authorization: str | None = Header(default=None, alias="Authorization"),
) -> DownloadUrlResponse:
    """CAS.ts readFile() step 1: mint a presigned MinIO GET for this attachment."""
    token_payload = _decode_file_token(share_id, path, authorization)
    user = _user_from_file_token(db, token_payload)
    share = share_service.get_share(db, share_id)
    share_service.ensure_read_access(db, share, user)

    settings = get_settings()
    object_name = f"web-assets/{share_id}/{path}"
    minio_client = _get_minio_client()
    try:
        # Confirm the object exists before minting the URL — a presigned GET
        # for a missing key 404s opaquely from MinIO, not from this API.
        minio_client.stat_object(settings.minio_bucket, object_name)
        url = minio_client.presigned_get_object(
            settings.minio_bucket, object_name, expires=timedelta(minutes=10)
        )
    except S3Error as e:
        if e.code in ("NoSuchKey", "NoSuchObject"):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="File not found"
            ) from e
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to retrieve file: {e}",
        ) from e
    return DownloadUrlResponse(downloadUrl=url)


@router.post("/{share_id}/files/{path:path}/upload-url", response_model=UploadUrlResponse)
def get_file_upload_url(
    share_id: uuid.UUID,
    path: str,
    db: Session = Depends(get_db),
    authorization: str | None = Header(default=None, alias="Authorization"),
) -> UploadUrlResponse:
    """CAS.ts writeFile() step 1: mint a presigned MinIO PUT for this attachment.

    The client PUTs directly to the returned URL and never calls back to
    confirm success, so this is the only point where control-plane can index
    the attachment — written optimistically here (same as web.py's
    mesh-artifact/sync-artifact uploads, which write bytes+index together;
    here the storage write happens client-side a moment later instead).
    """
    token_payload = _decode_file_token(share_id, path, authorization)
    user = _user_from_file_token(db, token_payload)
    share = share_service.get_share(db, share_id)
    share_service.ensure_write_access(db, share, user)

    settings = get_settings()
    minio_client = _get_minio_client()
    _ensure_minio_bucket(minio_client, settings.minio_bucket)
    object_name = f"web-assets/{share_id}/{path}"
    content_type = token_payload.get("content_type") or "application/octet-stream"
    url = minio_client.presigned_put_object(
        settings.minio_bucket, object_name, expires=timedelta(minutes=10)
    )

    now_iso = security.utcnow().isoformat()
    folder_items = list(share.web_folder_items or [])
    entry = {
        "path": path,
        "name": path.split("/")[-1],
        "type": "asset",
        "source": "user-upload",
        "mime": content_type,
        "size": token_payload.get("content_length") or 0,
        "sha256": token_payload.get("sha256"),
        "modified_at": now_iso,
        "storage_key": object_name,
        "content": None,
    }
    updated = False
    for item in folder_items:
        if item.get("path") == path:
            item.update(entry)
            updated = True
            break
    if not updated:
        folder_items.append(entry)
    share.web_folder_items = folder_items
    flag_modified(share, "web_folder_items")
    share.web_content_updated_at = security.utcnow()
    db.commit()

    return UploadUrlResponse(uploadUrl=url)


@router.patch("/{share_id}", response_model=share_schema.ShareRead)
async def update_share(
    share_id: uuid.UUID,
    request: Request,
    payload: share_schema.ShareUpdate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(deps.get_current_user),
):
    share = share_service.get_share(db, share_id)
    share_service.ensure_owner_or_admin(current_user, share)

    # Capture old values for notification
    old_values = {
        "kind": share.kind.value if share.kind else None,
        "path": share.path,
        "visibility": share.visibility.value if share.visibility else None,
    }

    updated_share = share_service.update_share(db, share, payload, actor_user_id=current_user.id)

    # Build changes dict
    changes = {}
    if payload.kind and old_values["kind"] != payload.kind.value:
        changes["kind"] = {"old": old_values["kind"], "new": payload.kind.value}
    if payload.path and old_values["path"] != payload.path:
        changes["path"] = {"old": old_values["path"], "new": payload.path}
    if payload.visibility and old_values["visibility"] != payload.visibility.value:
        changes["visibility"] = {"old": old_values["visibility"], "new": payload.visibility.value}

    # Queue notifications
    if changes:
        notification_service = get_notification_service()
        ip_address = request.client.host if request.client else None
        user_agent = request.headers.get("user-agent")

        await notification_service.notify_share_updated(
            db, updated_share, current_user, changes, ip_address, user_agent
        )

    # Build response with web_url
    response = share_schema.ShareRead.model_validate(updated_share)
    response.web_url = share_service.get_web_url(updated_share)
    return response


@router.delete("/{share_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_share(
    share_id: uuid.UUID,
    request: Request,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(deps.get_current_user),
) -> None:
    share = share_service.get_share(db, share_id)
    share_service.ensure_owner_or_admin(current_user, share)

    # Get members before deletion for notifications
    members = list(share.members) if share.members else []

    # Queue notifications before deletion (will email members)
    notification_service = get_notification_service()
    ip_address = request.client.host if request.client else None
    user_agent = request.headers.get("user-agent")

    await notification_service.notify_share_deleted(
        db, share, members, current_user, ip_address, user_agent
    )

    share_service.delete_share(db, share, actor_user_id=current_user.id)


@router.get("/{share_id}/members", response_model=list[share_schema.ShareMemberRead])
def list_members(
    share_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(deps.get_current_user),
):
    """List all members of a share. Requires owner/admin or member access."""
    share = share_service.get_share(db, share_id)
    # Allow owner, admin, or current members to view member list
    share_service.ensure_read_access(db, share, current_user)
    return share_service.list_members(db, share)


@router.post(
    "/{share_id}/members",
    response_model=share_schema.ShareMemberRead,
    status_code=status.HTTP_201_CREATED,
)
@limiter.limit("30/minute")  # Max 30 member additions per minute per IP
async def add_member(
    request: Request,
    share_id: uuid.UUID,
    payload: share_schema.ShareMemberCreate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(deps.get_current_user),
):
    share = share_service.get_share(db, share_id)
    share_service.ensure_owner_or_admin(current_user, share)
    # User validation is now done inside add_member service
    result = share_service.add_member(db, share, payload, actor_user_id=current_user.id)

    # Get the member for notifications
    member = (
        db.query(models.ShareMember)
        .filter(
            models.ShareMember.share_id == share.id,
            models.ShareMember.user_id == payload.user_id,
        )
        .first()
    )

    if member:
        notification_service = get_notification_service()
        ip_address = request.client.host if request.client else None
        user_agent = request.headers.get("user-agent")

        await notification_service.notify_member_added(
            db, share, member, current_user, ip_address, user_agent
        )

    return result


@router.patch("/{share_id}/members/{user_id}", response_model=share_schema.ShareMemberRead)
async def update_member_role(
    share_id: uuid.UUID,
    user_id: uuid.UUID,
    request: Request,
    payload: share_schema.ShareMemberUpdate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(deps.get_current_user),
):
    """Update a member's role. Requires owner/admin access."""
    share = share_service.get_share(db, share_id)
    share_service.ensure_owner_or_admin(current_user, share)

    # Get old role before update
    member = (
        db.query(models.ShareMember)
        .filter(
            models.ShareMember.share_id == share.id,
            models.ShareMember.user_id == user_id,
        )
        .first()
    )
    old_role = member.role.value if member else None

    result = share_service.update_member_role(
        db, share, user_id, payload, actor_user_id=current_user.id
    )

    # Reload member after update
    db.refresh(member) if member else None

    if member and old_role:
        notification_service = get_notification_service()
        ip_address = request.client.host if request.client else None
        user_agent = request.headers.get("user-agent")

        await notification_service.notify_member_updated(
            db, share, member, old_role, current_user, ip_address, user_agent
        )

    return result


@router.delete("/{share_id}/members/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_member(
    share_id: uuid.UUID,
    user_id: uuid.UUID,
    request: Request,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(deps.get_current_user),
) -> None:
    share = share_service.get_share(db, share_id)
    share_service.ensure_owner_or_admin(current_user, share)

    # Get member email before removal
    member = (
        db.query(models.ShareMember)
        .filter(
            models.ShareMember.share_id == share.id,
            models.ShareMember.user_id == user_id,
        )
        .first()
    )
    member_email = member.user.email if member and member.user else None

    # Queue notification before deletion
    notification_service = get_notification_service()
    ip_address = request.client.host if request.client else None
    user_agent = request.headers.get("user-agent")

    await notification_service.notify_member_removed(
        db, share, user_id, member_email, current_user, ip_address, user_agent
    )

    share_service.remove_member(db, share, user_id, actor_user_id=current_user.id)
