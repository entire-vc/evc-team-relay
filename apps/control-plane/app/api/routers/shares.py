from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query, Request, status
from slowapi import Limiter
from slowapi.util import get_remote_address
from sqlalchemy.orm import Session

from app.api import deps
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
