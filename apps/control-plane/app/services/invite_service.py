from __future__ import annotations

import secrets
import uuid
from datetime import timedelta

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.core import security
from app.core.config import get_settings
from app.db import models
from app.schemas import invite as invite_schema
from app.schemas import user as user_schema
from app.services import audit_service, share_service, user_service


def generate_secure_token() -> str:
    """Generate cryptographically secure token for invite links.

    Returns 32 bytes (256 bits) as 64-character hex string.
    """
    return secrets.token_hex(32)


def create_invite(
    db: Session,
    share_id: uuid.UUID,
    user_id: uuid.UUID,
    payload: invite_schema.InviteCreate,
) -> models.ShareInvite:
    """Create a new invite link for a share.

    Args:
        db: Database session
        share_id: Share to create invite for
        user_id: User creating the invite (must be owner or admin)
        payload: Invite creation parameters

    Returns:
        Created ShareInvite instance
    """
    # Verify share exists (raises 404 if not found)
    share_service.get_share(db, share_id)

    # Calculate expiration. The schema's own default (7) only applies when the
    # field is absent from the request body — a caller that sends an explicit
    # null (or 0) bypasses it, which is how invites ended up with no expiry at
    # all. Apply a config-driven default in that case too (mirrors TR-45's
    # agent-key fix in app/api/routers/agent_keys.py).
    if payload.expires_in_days:
        expires_at = security.utcnow() + timedelta(days=payload.expires_in_days)
    else:
        expires_at = security.utcnow() + timedelta(days=get_settings().invite_default_ttl_days)

    # Generate unique token
    token = generate_secure_token()

    invite = models.ShareInvite(
        share_id=share_id,
        token=token,
        created_by=user_id,
        role=payload.role,
        expires_at=expires_at,
        max_uses=payload.max_uses,
        use_count=0,
    )

    db.add(invite)
    db.commit()
    db.refresh(invite)

    # Log invite creation
    audit_service.log_action(
        db=db,
        action=models.AuditAction.INVITE_CREATED,
        actor_user_id=user_id,
        target_share_id=share_id,
        details={
            "invite_id": str(invite.id),
            "role": invite.role.value,
            "expires_at": invite.expires_at.isoformat() if invite.expires_at else None,
            "max_uses": invite.max_uses,
        },
    )

    return invite


def get_invite_by_token(db: Session, token: str) -> models.ShareInvite | None:
    """Get invite by token.

    Args:
        db: Database session
        token: Invite token

    Returns:
        ShareInvite instance or None if not found
    """
    stmt = (
        select(models.ShareInvite)
        .where(models.ShareInvite.token == token)
        .options(
            joinedload(models.ShareInvite.share).joinedload(models.Share.owner),
            joinedload(models.ShareInvite.creator),
        )
    )
    return db.execute(stmt).unique().scalar_one_or_none()


def list_invites(db: Session, share_id: uuid.UUID) -> list[models.ShareInvite]:
    """List all invites for a share.

    Args:
        db: Database session
        share_id: Share ID

    Returns:
        List of ShareInvite instances
    """
    stmt = (
        select(models.ShareInvite)
        .where(models.ShareInvite.share_id == share_id)
        .order_by(models.ShareInvite.created_at.desc())
    )
    return list(db.execute(stmt).scalars().all())


def revoke_invite(
    db: Session,
    invite_id: uuid.UUID,
    user_id: uuid.UUID,
) -> bool:
    """Revoke an invite link.

    Args:
        db: Database session
        invite_id: Invite ID to revoke
        user_id: User revoking the invite

    Returns:
        True if revoked, False if not found

    Raises:
        HTTPException: If invite not found
    """
    stmt = select(models.ShareInvite).where(models.ShareInvite.id == invite_id)
    invite = db.execute(stmt).scalar_one_or_none()

    if not invite:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Invite not found",
        )

    invite.revoked_at = security.utcnow()
    db.add(invite)
    db.commit()

    # Log invite revocation
    audit_service.log_action(
        db=db,
        action=models.AuditAction.INVITE_REVOKED,
        actor_user_id=user_id,
        target_share_id=invite.share_id,
        details={"invite_id": str(invite.id)},
    )

    return True


def validate_invite(invite: models.ShareInvite) -> tuple[bool, str | None]:
    """Validate if an invite can be redeemed.

    Args:
        invite: ShareInvite instance

    Returns:
        Tuple of (is_valid, error_message)
    """
    # Check if revoked
    if invite.revoked_at:
        return False, "This invite link has been revoked"

    # Check expiration (handle both timezone-aware and naive datetimes for SQLite compatibility)
    if invite.expires_at:
        now = security.utcnow()
        expires_at = invite.expires_at
        # Make both timezone-naive for comparison if needed (SQLite returns naive datetimes)
        if expires_at.tzinfo is None and now.tzinfo is not None:
            now = now.replace(tzinfo=None)
        elif expires_at.tzinfo is not None and now.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=None)

        if expires_at < now:
            return False, "This invite link has expired"

    # Check usage limit
    if invite.max_uses and invite.use_count >= invite.max_uses:
        return False, "This invite link has reached its usage limit"

    return True, None


def get_invite_public_info(db: Session, token: str) -> invite_schema.InvitePublicInfo:
    """Get public information about an invite (no auth required).

    Args:
        db: Database session
        token: Invite token

    Returns:
        InvitePublicInfo with share details and validity
    """
    invite = get_invite_by_token(db, token)

    if not invite:
        return invite_schema.InvitePublicInfo(
            share_path="",
            share_kind="",
            owner_email="",
            role=models.ShareMemberRole.VIEWER,
            is_valid=False,
            error="Invalid invite link",
        )

    is_valid, error = validate_invite(invite)

    return invite_schema.InvitePublicInfo(
        share_path=invite.share.path,
        share_kind=invite.share.kind.value,
        owner_email=invite.share.owner.email,
        role=invite.role,
        is_valid=is_valid,
        error=error,
        expires_at=invite.expires_at,
    )


def redeem_invite(
    db: Session,
    token: str,
    user: models.User | None = None,
    new_user_data: invite_schema.InviteRedeemNewUser | None = None,
) -> invite_schema.InviteRedeemResponse:
    """Redeem an invite link.

    Args:
        db: Database session
        token: Invite token
        user: Existing authenticated user (optional)
        new_user_data: New user registration data (optional)

    Returns:
        InviteRedeemResponse with user and share details

    Raises:
        HTTPException: If invite is invalid or user creation fails
    """
    invite = get_invite_by_token(db, token)

    if not invite:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Invalid invite link",
        )

    # Validate invite
    is_valid, error = validate_invite(invite)
    if not is_valid:
        raise HTTPException(
            status_code=status.HTTP_410_GONE,
            detail=error or "Invite is no longer valid",
        )

    # TR-46: an invite targeted at a specific email (invite.email) previously let
    # ANY bearer of the link redeem it — the email column was stored but never
    # checked, creating a false sense of targeting. Checked here, before any new
    # user is registered, so a mismatch never leaves behind an orphan account
    # that was created but not granted membership. Case-insensitive; a null/blank
    # invite.email (the common case today — no invite has ever set it) keeps the
    # existing untargeted behavior unchanged.
    if invite.email:
        redeeming_email = user.email if user else (new_user_data.email if new_user_data else None)
        if redeeming_email and invite.email.strip().lower() != redeeming_email.strip().lower():
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="This invite is restricted to a different email address",
            )

    # Handle new user registration or existing user
    access_token = None
    if not user:
        if not new_user_data:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Either authenticate or provide registration details",
            )

        # Create new user
        user_payload = user_schema.UserCreate(
            email=new_user_data.email,
            password=new_user_data.password,
            is_admin=False,
            is_active=True,
        )

        try:
            user = user_service.create_user(db, user_payload)
            # Generate access token for new user
            access_token = security.create_access_token(str(user.id))
        except HTTPException as e:
            if "already exists" in str(e.detail).lower():
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="An account with this email already exists. Please log in instead.",
                ) from e
            raise

    # Check if user is the owner
    if user.id == invite.share.owner_user_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="You are already the owner of this share",
        )

    # Check if user is already a member (idempotent - just return success)
    existing_member = db.execute(
        select(models.ShareMember).where(
            models.ShareMember.share_id == invite.share_id,
            models.ShareMember.user_id == user.id,
        )
    ).scalar_one_or_none()

    if not existing_member:
        # Add user as member with atomic use count increment
        member = models.ShareMember(
            share_id=invite.share_id,
            user_id=user.id,
            role=invite.role,
        )
        db.add(member)

        # Atomic increment of use_count
        invite.use_count += 1
        db.add(invite)

        # Log redemption
        audit_service.log_action(
            db=db,
            action=models.AuditAction.INVITE_REDEEMED,
            actor_user_id=user.id,
            target_share_id=invite.share_id,
            details={
                "invite_id": str(invite.id),
                "role": invite.role.value,
                "is_new_user": access_token is not None,
            },
        )

        db.commit()

    return invite_schema.InviteRedeemResponse(
        user_id=user.id,
        user_email=user.email,
        share_id=invite.share_id,
        share_path=invite.share.path,
        role=invite.role,
        access_token=access_token,
    )
