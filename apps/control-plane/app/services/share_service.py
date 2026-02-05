from __future__ import annotations

import re
import uuid

from fastapi import HTTPException, status
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, joinedload

from app.core import security
from app.core.config import get_settings
from app.db import models
from app.schemas import share as share_schema
from app.services import audit_service
from app.utils import slug as slug_utils


def get_web_url(share: models.Share) -> str | None:
    """
    Compute the web URL for a share if web publishing is enabled.

    Args:
        share: Share object

    Returns:
        Full web URL or None if not web published
    """
    if not share.web_published or not share.web_slug:
        return None

    settings = get_settings()
    web_domain = getattr(settings, "web_publish_domain", None)
    if not web_domain:
        return None

    # Ensure domain has https:// prefix
    if not web_domain.startswith("http://") and not web_domain.startswith("https://"):
        web_domain = f"https://{web_domain}"

    return f"{web_domain.rstrip('/')}/{share.web_slug}"


def validate_share_path_safety(path: str, kind: models.ShareKind) -> None:
    """
    Validate path format and safety.

    Raises HTTPException if path is unsafe or invalid format.
    """
    if not path or path.strip() == "":
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Path cannot be empty")

    # Check for path traversal attempts
    if ".." in path:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Path traversal detected: '..' is not allowed in paths",
        )

    # Check for null bytes
    if "\x00" in path:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Path contains invalid null byte character",
        )

    # Check for absolute paths (should be relative to vault)
    if path.startswith("/") or path.startswith("\\"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Path must be relative to vault root, not absolute",
        )

    # Check for Windows drive letters (C:, D:, etc.)
    if re.match(r"^[a-zA-Z]:", path):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Path cannot contain drive letters"
        )

    # For document shares, validate file extension
    if kind == models.ShareKind.DOC:
        valid_extensions = [".md", ".canvas"]
        has_valid_ext = any(path.lower().endswith(ext) for ext in valid_extensions)
        if not has_valid_ext:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Document shares must have .md or .canvas extension, got: {path}",
            )

    # Check path length (max 1000 chars to prevent abuse)
    if len(path) > 1000:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Path exceeds maximum length of 1000 characters",
        )


def ensure_owner_or_admin(user: models.User, share: models.Share) -> None:
    if user.is_admin or share.owner_user_id == user.id:
        return
    raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient permissions")


def _get_member(db: Session, share_id: uuid.UUID, user_id: uuid.UUID) -> models.ShareMember | None:
    stmt = select(models.ShareMember).where(
        models.ShareMember.share_id == share_id,
        models.ShareMember.user_id == user_id,
    )
    return db.execute(stmt).scalar_one_or_none()


def create_share(
    db: Session, owner: models.User, payload: share_schema.ShareCreate
) -> models.Share:
    # Validate path safety and format
    validate_share_path_safety(payload.path, payload.kind)

    password_hash = None
    if payload.visibility == models.ShareVisibility.PROTECTED:
        if not payload.password:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Password is required for protected shares",
            )
        password_hash = security.get_password_hash(payload.password)

    # Handle web publishing slug
    web_slug = None
    if payload.web_published:
        if payload.web_slug:
            # Custom slug provided
            slug_candidate = slug_utils.slugify(payload.web_slug)
            if not slug_utils.is_slug_available(db, slug_candidate):
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Slug '{slug_candidate}' is already taken or reserved",
                )
            web_slug = slug_candidate
        else:
            # Auto-generate slug from path
            web_slug = slug_utils.generate_unique_slug(db, payload.path)

    share = models.Share(
        kind=payload.kind,
        path=payload.path,
        visibility=payload.visibility,
        password_hash=password_hash,
        owner_user_id=owner.id,
        web_published=payload.web_published,
        web_slug=web_slug,
        web_noindex=payload.web_noindex,
        web_sync_mode=payload.web_sync_mode,
    )
    db.add(share)
    db.commit()
    db.refresh(share)

    # Log share creation
    audit_service.log_action(
        db=db,
        action=models.AuditAction.SHARE_CREATED,
        actor_user_id=owner.id,
        target_share_id=share.id,
        details={
            "kind": share.kind.value,
            "path": share.path,
            "visibility": share.visibility.value,
            "web_published": share.web_published,
        },
    )

    return share


def get_share(db: Session, share_id: uuid.UUID) -> models.Share:
    stmt = select(models.Share).where(models.Share.id == share_id)
    share = db.execute(stmt).scalar_one_or_none()
    if not share:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Share not found")
    return share


def update_share(
    db: Session,
    share: models.Share,
    payload: share_schema.ShareUpdate,
    actor_user_id: uuid.UUID | None = None,
) -> models.Share:
    changes = {}
    if payload.kind:
        changes["kind"] = {"old": share.kind.value, "new": payload.kind.value}
        share.kind = payload.kind
    if payload.path:
        changes["path"] = {"old": share.path, "new": payload.path}
        share.path = payload.path
    if payload.visibility:
        changes["visibility"] = {"old": share.visibility.value, "new": payload.visibility.value}
        share.visibility = payload.visibility
        if payload.visibility != models.ShareVisibility.PROTECTED:
            share.password_hash = None
        elif payload.password:
            share.password_hash = security.get_password_hash(payload.password)
        elif not share.password_hash:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Password required for protected share",
            )
    if payload.password and share.visibility == models.ShareVisibility.PROTECTED:
        changes["password"] = "updated"
        share.password_hash = security.get_password_hash(payload.password)

    # Handle web publishing updates
    if payload.web_published is not None:
        changes["web_published"] = {"old": share.web_published, "new": payload.web_published}
        share.web_published = payload.web_published

        # If enabling web publishing, ensure we have a slug
        if payload.web_published and not share.web_slug:
            if payload.web_slug:
                slug_candidate = slug_utils.slugify(payload.web_slug)
                if not slug_utils.is_slug_available(db, slug_candidate, str(share.id)):
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail=f"Slug '{slug_candidate}' is already taken or reserved",
                    )
                share.web_slug = slug_candidate
            else:
                share.web_slug = slug_utils.generate_unique_slug(db, share.path, str(share.id))

        # If disabling web publishing, optionally clear slug (keep it for re-enabling)
        # We'll keep the slug for now to allow toggling without losing the URL

    if payload.web_slug is not None:
        slug_candidate = slug_utils.slugify(payload.web_slug)
        if slug_candidate != share.web_slug:
            if not slug_utils.is_slug_available(db, slug_candidate, str(share.id)):
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Slug '{slug_candidate}' is already taken or reserved",
                )
            changes["web_slug"] = {"old": share.web_slug, "new": slug_candidate}
            share.web_slug = slug_candidate

    if payload.web_noindex is not None:
        changes["web_noindex"] = {"old": share.web_noindex, "new": payload.web_noindex}
        share.web_noindex = payload.web_noindex

    if payload.web_sync_mode is not None:
        changes["web_sync_mode"] = {"old": share.web_sync_mode, "new": payload.web_sync_mode}
        share.web_sync_mode = payload.web_sync_mode

    # Handle web content update
    if payload.web_content is not None:
        from datetime import datetime, timezone

        # Only log that content was updated, not the actual content (could be large)
        old_has_content = share.web_content is not None
        new_has_content = payload.web_content != ""
        changes["web_content"] = {"old": old_has_content, "new": new_has_content}
        share.web_content = payload.web_content if payload.web_content else None
        share.web_content_updated_at = datetime.now(timezone.utc) if payload.web_content else None

    # Handle web folder items update
    if payload.web_folder_items is not None:
        old_has_items = share.web_folder_items is not None
        new_has_items = len(payload.web_folder_items) > 0
        changes["web_folder_items"] = {"old": old_has_items, "new": new_has_items}
        if payload.web_folder_items:
            share.web_folder_items = [item.model_dump() for item in payload.web_folder_items]
        else:
            share.web_folder_items = None

    # Handle web doc_id update (y-sweet document ID for real-time sync)
    if payload.web_doc_id is not None:
        old_doc_id = share.web_doc_id
        changes["web_doc_id"] = {"old": old_doc_id is not None, "new": payload.web_doc_id != ""}
        share.web_doc_id = payload.web_doc_id if payload.web_doc_id else None

    db.add(share)
    db.commit()
    db.refresh(share)

    # Log share update
    if changes:
        audit_service.log_action(
            db=db,
            action=models.AuditAction.SHARE_UPDATED,
            actor_user_id=actor_user_id,
            target_share_id=share.id,
            details={"changes": changes},
        )

    return share


def delete_share(
    db: Session,
    share: models.Share,
    actor_user_id: uuid.UUID | None = None,
) -> None:
    # Log before deletion
    audit_service.log_action(
        db=db,
        action=models.AuditAction.SHARE_DELETED,
        actor_user_id=actor_user_id,
        target_share_id=share.id,
        details={"kind": share.kind.value, "path": share.path},
    )

    db.delete(share)
    db.commit()


def add_member(
    db: Session,
    share: models.Share,
    payload: share_schema.ShareMemberCreate,
    actor_user_id: uuid.UUID | None = None,
) -> dict:
    # Validate user exists BEFORE any DB modifications
    user = db.get(models.User, payload.user_id)
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    if payload.user_id == share.owner_user_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Owner already has full access"
        )

    member = _get_member(db, share.id, payload.user_id)
    is_update = member is not None
    if member:
        member.role = payload.role
    else:
        member = models.ShareMember(share_id=share.id, user_id=payload.user_id, role=payload.role)
        db.add(member)
    db.commit()
    db.refresh(member)

    # Log member addition or update
    action = (
        models.AuditAction.SHARE_MEMBER_UPDATED
        if is_update
        else models.AuditAction.SHARE_MEMBER_ADDED
    )
    audit_service.log_action(
        db=db,
        action=action,
        actor_user_id=actor_user_id,
        target_user_id=payload.user_id,
        target_share_id=share.id,
        details={"role": payload.role.value},
    )

    return {
        "id": member.id,
        "share_id": member.share_id,
        "user_id": member.user_id,
        "user_email": user.email,
        "role": member.role,
    }


def list_members(db: Session, share: models.Share) -> list[dict]:
    """
    List all members of a share with user emails.
    Returns dicts with ShareMember data + user_email for better UX.
    """
    stmt = (
        select(models.ShareMember, models.User.email)
        .join(models.User, models.ShareMember.user_id == models.User.id)
        .where(models.ShareMember.share_id == share.id)
    )
    results = db.execute(stmt).all()

    # Convert to dict format that matches ShareMemberRead schema
    members = []
    for member, email in results:
        members.append(
            {
                "id": member.id,
                "share_id": member.share_id,
                "user_id": member.user_id,
                "user_email": email,
                "role": member.role,
            }
        )
    return members


def update_member_role(
    db: Session,
    share: models.Share,
    user_id: uuid.UUID,
    payload: share_schema.ShareMemberUpdate,
    actor_user_id: uuid.UUID | None = None,
) -> dict:
    """Update a member's role."""
    member = _get_member(db, share.id, user_id)
    if not member:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Member not found")

    old_role = member.role
    member.role = payload.role
    db.add(member)
    db.commit()
    db.refresh(member)

    # Log role update
    audit_service.log_action(
        db=db,
        action=models.AuditAction.SHARE_MEMBER_UPDATED,
        actor_user_id=actor_user_id,
        target_user_id=user_id,
        target_share_id=share.id,
        details={"role": {"old": old_role.value, "new": payload.role.value}},
    )

    # Fetch user email to return in response
    user = db.get(models.User, user_id)
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    return {
        "id": member.id,
        "share_id": member.share_id,
        "user_id": member.user_id,
        "user_email": user.email,
        "role": member.role,
    }


def remove_member(
    db: Session,
    share: models.Share,
    user_id: uuid.UUID,
    actor_user_id: uuid.UUID | None = None,
) -> None:
    member = _get_member(db, share.id, user_id)
    if not member:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Member not found")

    # Log before deletion
    audit_service.log_action(
        db=db,
        action=models.AuditAction.SHARE_MEMBER_REMOVED,
        actor_user_id=actor_user_id,
        target_user_id=user_id,
        target_share_id=share.id,
        details={"role": member.role.value},
    )

    db.delete(member)
    db.commit()


def ensure_read_access(
    db: Session,
    share: models.Share,
    user: models.User | None,
    password: str | None = None,
) -> None:
    if user and (user.is_admin or share.owner_user_id == user.id):
        return

    member = None
    if user:
        member = _get_member(db, share.id, user.id)
        if member:
            return

    if share.visibility == models.ShareVisibility.PUBLIC:
        return

    if share.visibility == models.ShareVisibility.PROTECTED:
        if (
            password
            and share.password_hash
            and security.verify_password(password, share.password_hash)
        ):
            return
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Password required for protected share"
        )

    raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Share is private")


def ensure_write_access(db: Session, share: models.Share, user: models.User | None) -> None:
    if user and (user.is_admin or share.owner_user_id == user.id):
        return
    if not user:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Authentication required for write"
        )
    member = _get_member(db, share.id, user.id)
    if member and member.role == models.ShareMemberRole.EDITOR:
        return
    raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Write access denied")


def list_user_shares(
    db: Session,
    user: models.User,
    kind: models.ShareKind | None = None,
    owned_only: bool = False,
    member_only: bool = False,
    skip: int = 0,
    limit: int = 100,
) -> list[share_schema.ShareListItem]:
    """
    List shares accessible to user with role information.

    Args:
        db: Database session
        user: Current user
        kind: Filter by share kind (doc or folder)
        owned_only: Only return shares owned by user
        member_only: Only return shares where user is a member (not owner)
        skip: Pagination offset
        limit: Maximum number of results (max 100)

    Returns:
        List of ShareListItem with user's role information
    """
    limit = min(limit, 100)  # Cap at 100

    # Build query for shares user can access
    if owned_only:
        # Only shares owned by user
        stmt = select(models.Share).where(models.Share.owner_user_id == user.id)
    elif member_only:
        # Only shares where user is a member
        stmt = (
            select(models.Share)
            .join(models.ShareMember, models.Share.id == models.ShareMember.share_id)
            .where(models.ShareMember.user_id == user.id)
        )
    else:
        # All shares: owned OR member
        stmt = (
            select(models.Share)
            .outerjoin(models.ShareMember, models.Share.id == models.ShareMember.share_id)
            .where(
                or_(
                    models.Share.owner_user_id == user.id,
                    models.ShareMember.user_id == user.id,
                )
            )
            .distinct()
        )

    # Apply kind filter if specified
    if kind:
        stmt = stmt.where(models.Share.kind == kind)

    # Eagerly load members for role lookup
    stmt = stmt.options(joinedload(models.Share.members))

    # Pagination
    stmt = stmt.offset(skip).limit(limit)

    shares = db.execute(stmt).unique().scalars().all()

    # Build response with role information
    result = []
    for share in shares:
        is_owner = share.owner_user_id == user.id
        user_role = None

        if not is_owner:
            # Find user's role in members
            for member in share.members:
                if member.user_id == user.id:
                    user_role = member.role
                    break

        result.append(
            share_schema.ShareListItem(
                id=share.id,
                kind=share.kind,
                path=share.path,
                visibility=share.visibility,
                owner_user_id=share.owner_user_id,
                created_at=share.created_at,
                updated_at=share.updated_at,
                is_owner=is_owner,
                user_role=user_role,
                web_published=share.web_published,
                web_slug=share.web_slug,
                web_noindex=share.web_noindex,
                web_sync_mode=share.web_sync_mode,
                web_url=get_web_url(share),
            )
        )

    return result


def list_all_shares_admin(
    db: Session,
    kind: models.ShareKind | None = None,
    visibility: models.ShareVisibility | None = None,
    owner_id: uuid.UUID | None = None,
    search: str | None = None,
    skip: int = 0,
    limit: int = 50,
) -> tuple[list[models.Share], int]:
    """
    List all shares in the system (admin only).

    Args:
        db: Database session
        kind: Filter by share kind
        visibility: Filter by visibility
        owner_id: Filter by owner
        search: Search by path (case-insensitive)
        skip: Pagination offset
        limit: Maximum number of results

    Returns:
        Tuple of (shares list, total count)
    """
    limit = min(limit, 100)  # Cap at 100

    # Build query
    stmt = select(models.Share)

    # Apply filters
    if kind:
        stmt = stmt.where(models.Share.kind == kind)
    if visibility:
        stmt = stmt.where(models.Share.visibility == visibility)
    if owner_id:
        stmt = stmt.where(models.Share.owner_user_id == owner_id)
    if search:
        stmt = stmt.where(models.Share.path.ilike(f"%{search}%"))

    # Eagerly load relations
    stmt = stmt.options(
        joinedload(models.Share.owner),
        joinedload(models.Share.members),
    )

    # Count total (before pagination)
    count_stmt = select(func.count()).select_from(stmt.subquery())
    total = db.execute(count_stmt).scalar() or 0

    # Pagination
    stmt = stmt.order_by(models.Share.created_at.desc()).offset(skip).limit(limit)

    shares = db.execute(stmt).unique().scalars().all()

    return list(shares), total


def validate_path_within_folder(folder_path: str, file_path: str) -> bool:
    """
    Validate that file_path is within folder_path boundaries.

    Args:
        folder_path: The folder share path (e.g., "Projects/")
        file_path: The file path to check (e.g., "Projects/doc.md")

    Returns:
        True if file_path is within folder_path, False otherwise

    Examples:
        validate_path_within_folder("Projects/", "Projects/doc.md") -> True
        validate_path_within_folder("Projects/", "Projects/sub/doc.md") -> True
        validate_path_within_folder("Projects/", "Other/doc.md") -> False
        validate_path_within_folder("Projects/", "Projects") -> False (exact match, not within)
    """
    # Normalize paths: remove leading/trailing slashes
    folder_path = folder_path.strip("/")
    file_path = file_path.strip("/")

    # Ensure folder path ends with / for proper prefix matching
    if not folder_path.endswith("/"):
        folder_path_normalized = folder_path + "/"
    else:
        folder_path_normalized = folder_path

    # File must start with folder path and have more content after
    return file_path.startswith(folder_path_normalized)


def find_share_for_path(db: Session, user: models.User, file_path: str) -> models.Share | None:
    """
    Find the most specific share that covers a file path.

    Priority:
    1. Direct doc share matching exact path
    2. Folder share where file_path starts with share.path (most specific/longest match)

    Args:
        db: Database session
        user: User requesting access
        file_path: Path to the file (e.g., "Projects/subdir/file.md")

    Returns:
        Share object if found and user has access, None otherwise

    Examples:
        - Direct doc share "Projects/doc.md" takes precedence over folder "Projects/"
        - Folder "Projects/subproject/" takes precedence over "Projects/"
        - User must be owner or member of the share
    """
    # Normalize file path
    file_path = file_path.strip("/")

    # First, check for exact doc share match
    stmt = select(models.Share).where(
        models.Share.kind == models.ShareKind.DOC, models.Share.path == file_path
    )
    doc_share = db.execute(stmt).scalar_one_or_none()

    # If doc share exists, check user access
    if doc_share:
        is_owner = doc_share.owner_user_id == user.id
        is_member = _get_member(db, doc_share.id, user.id) is not None
        if is_owner or is_member or user.is_admin:
            return doc_share

    # No direct doc share, look for folder shares
    # Find all folder shares ordered by path length (longest first = most specific)
    stmt = (
        select(models.Share)
        .where(models.Share.kind == models.ShareKind.FOLDER)
        .options(joinedload(models.Share.members))
    )
    folder_shares = db.execute(stmt).unique().scalars().all()

    # Filter to shares where file is within folder and user has access
    accessible_folders = []
    for share in folder_shares:
        if validate_path_within_folder(share.path, file_path):
            is_owner = share.owner_user_id == user.id
            is_member = any(m.user_id == user.id for m in share.members)
            if is_owner or is_member or user.is_admin:
                accessible_folders.append(share)

    # Return most specific (longest path) folder share
    if accessible_folders:
        return max(accessible_folders, key=lambda s: len(s.path.strip("/")))

    return None
