from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.api import deps
from app.db import models
from app.db.models import EmailStatus
from app.db.session import get_db
from app.schemas import audit as audit_schema
from app.schemas import branding as branding_schema
from app.schemas import email as email_schema
from app.schemas import user as user_schema
from app.services import audit_service, email_service, instance_settings_service, user_service

router = APIRouter(prefix="/admin", tags=["admin"])


@router.get("/users", response_model=list[user_schema.UserRead])
def list_users(
    db: Session = Depends(get_db),
    _: models.User = Depends(deps.get_current_admin),
):
    return user_service.list_users(db)


@router.post("/users", response_model=user_schema.UserRead, status_code=status.HTTP_201_CREATED)
def create_user(
    payload: user_schema.UserCreate,
    db: Session = Depends(get_db),
    current_admin: models.User = Depends(deps.get_current_admin),
):
    return user_service.create_user(db, payload, actor_user_id=current_admin.id)


@router.patch("/users/{user_id}", response_model=user_schema.UserRead)
def update_user(
    user_id: uuid.UUID,
    payload: user_schema.UserUpdate,
    db: Session = Depends(get_db),
    current_admin: models.User = Depends(deps.get_current_admin),
):
    return user_service.update_user(db, user_id, payload, actor_user_id=current_admin.id)


@router.delete("/users/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_user(
    user_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_admin: models.User = Depends(deps.get_current_admin),
) -> None:
    user_service.delete_user(db, user_id, actor_user_id=current_admin.id)


@router.post("/users/{user_id}/make-admin", response_model=user_schema.UserRead)
def grant_admin(
    user_id: uuid.UUID,
    db: Session = Depends(get_db),
    _: models.User = Depends(deps.get_current_admin),
):
    return user_service.set_admin(db, user_id, True)


@router.post("/users/{user_id}/remove-admin", response_model=user_schema.UserRead)
def revoke_admin(
    user_id: uuid.UUID,
    db: Session = Depends(get_db),
    _: models.User = Depends(deps.get_current_admin),
):
    return user_service.set_admin(db, user_id, False)


@router.get("/audit-logs", response_model=list[audit_schema.AuditLogRead])
def list_audit_logs(
    action: models.AuditAction | None = Query(default=None, description="Filter by action type"),
    actor_user_id: uuid.UUID | None = Query(default=None, description="Filter by actor user ID"),
    target_user_id: uuid.UUID | None = Query(default=None, description="Filter by target user ID"),
    target_share_id: uuid.UUID | None = Query(
        default=None, description="Filter by target share ID"
    ),
    start_date: datetime | None = Query(
        default=None, description="Filter logs after this timestamp"
    ),
    end_date: datetime | None = Query(
        default=None, description="Filter logs before this timestamp"
    ),
    skip: int = Query(default=0, ge=0, description="Pagination offset"),
    limit: int = Query(default=50, ge=1, le=100, description="Maximum results per page"),
    db: Session = Depends(get_db),
    _: models.User = Depends(deps.get_current_admin),
):
    """
    List audit logs with optional filters. Admin only.

    Filter by:
    - action: Specific action type (e.g., user_created, share_deleted)
    - actor_user_id: User who performed the action
    - target_user_id: User affected by the action
    - target_share_id: Share affected by the action
    - start_date/end_date: Time range
    - skip/limit: Pagination (max 100 per page)

    Results are ordered by timestamp descending (most recent first).
    """
    return audit_service.list_audit_logs(
        db=db,
        action=action,
        actor_user_id=actor_user_id,
        target_user_id=target_user_id,
        target_share_id=target_share_id,
        start_date=start_date,
        end_date=end_date,
        skip=skip,
        limit=limit,
    )


@router.get("/settings/branding", response_model=branding_schema.BrandingRead)
def get_branding(
    db: Session = Depends(get_db),
    _: models.User = Depends(deps.get_current_admin),
):
    """
    Get instance branding settings. Admin only.

    Returns the current instance branding configuration including:
    - name: Instance display name
    - logo_url: URL to the instance logo
    - favicon_url: URL to the instance favicon
    """
    branding_data = instance_settings_service.get_branding(db)
    return branding_schema.BrandingRead(**branding_data)


@router.patch("/settings/branding", response_model=branding_schema.BrandingRead)
def update_branding(
    payload: branding_schema.BrandingUpdate,
    db: Session = Depends(get_db),
    _: models.User = Depends(deps.get_current_admin),
):
    """
    Update instance branding settings. Admin only.

    Updates the instance branding configuration. All fields are required.
    """
    branding_data = instance_settings_service.set_branding(
        db=db,
        name=payload.name,
        logo_url=payload.logo_url,
        favicon_url=payload.favicon_url,
        custom_head_code=payload.custom_head_code,
        custom_body_code=payload.custom_body_code,
    )
    return branding_schema.BrandingRead(**branding_data)


@router.get("/emails", response_model=list[email_schema.EmailQueueRead])
def list_emails(
    status_filter: EmailStatus | None = Query(default=None, alias="status"),
    email_type: str | None = Query(default=None),
    skip: int = Query(default=0, ge=0, description="Pagination offset"),
    limit: int = Query(default=50, ge=1, le=100, description="Maximum results per page"),
    db: Session = Depends(get_db),
    _: models.User = Depends(deps.get_current_admin),
):
    """
    List queued/sent/failed emails. Admin only. TR-35.

    A FAILED email exhausted MAX_EMAIL_RETRIES (currently 6 attempts over
    ~24h, matching the webhook worker) — this is the admin-visible view of
    that dead-letter state; see POST /admin/emails/{email_id}/requeue.
    """
    svc = email_service.get_email_service()
    return svc.list_emails(
        db=db,
        status_filter=status_filter,
        email_type=email_type,
        skip=skip,
        limit=limit,
    )


@router.post("/emails/{email_id}/requeue", response_model=email_schema.EmailQueueRead)
def requeue_email(
    email_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_admin: models.User = Depends(deps.get_current_admin),
):
    """
    Manually requeue a FAILED email for another delivery attempt. Admin only. TR-35.

    Resets attempt_count to 0 and schedules an immediate retry. Only valid
    for emails currently in FAILED state — PENDING (still retrying) or SENT
    emails are left untouched (409).
    """
    svc = email_service.get_email_service()
    email = svc.get_email(db, email_id)
    if not email:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Email not found")
    if email.status != EmailStatus.FAILED:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Email is not in failed state (current: {email.status.value})",
        )

    email = svc.requeue_email(db, email)

    audit_service.log_action(
        db=db,
        # no dedicated EMAIL_REQUEUED enum value yet, see webhooks.py precedent
        action=models.AuditAction.USER_UPDATED,
        actor_user_id=current_admin.id,
        details={
            "action": "email_requeued",
            "email_id": str(email.id),
            "to_email": email.to_email,
            "email_type": email.email_type,
        },
    )

    return email
