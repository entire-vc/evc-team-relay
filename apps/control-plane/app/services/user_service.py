from __future__ import annotations

import uuid

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core import security
from app.db import models
from app.schemas import user as user_schema
from app.services import audit_service


def _normalize_email(email: str) -> str:
    return email.strip().lower()


def get_user_by_email(db: Session, email: str) -> models.User | None:
    stmt = select(models.User).where(models.User.email == _normalize_email(email))
    return db.execute(stmt).scalar_one_or_none()


def create_user(
    db: Session,
    payload: user_schema.UserCreate,
    actor_user_id: uuid.UUID | None = None,
) -> models.User:
    if get_user_by_email(db, payload.email):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Email already registered"
        )
    user = models.User(
        email=_normalize_email(payload.email),
        password_hash=security.get_password_hash(payload.password),
        is_admin=payload.is_admin,
        is_active=payload.is_active,
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    # Log user creation
    audit_service.log_action(
        db=db,
        action=models.AuditAction.USER_CREATED,
        actor_user_id=actor_user_id,
        target_user_id=user.id,
        details={"email": user.email, "is_admin": user.is_admin},
    )

    return user


def update_user(
    db: Session,
    user_id: uuid.UUID,
    payload: user_schema.UserUpdate,
    actor_user_id: uuid.UUID | None = None,
) -> models.User:
    stmt = select(models.User).where(models.User.id == user_id)
    user = db.execute(stmt).scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    changes = {}
    if payload.email and payload.email.lower() != user.email:
        if get_user_by_email(db, payload.email):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail="Email already registered"
            )
        changes["email"] = {"old": user.email, "new": _normalize_email(payload.email)}
        user.email = _normalize_email(payload.email)

    if payload.password:
        changes["password"] = "updated"
        user.password_hash = security.get_password_hash(payload.password)

    if payload.is_active is not None:
        changes["is_active"] = {"old": user.is_active, "new": payload.is_active}
        user.is_active = payload.is_active

    if payload.is_admin is not None:
        changes["is_admin"] = {"old": user.is_admin, "new": payload.is_admin}
        user.is_admin = payload.is_admin

    db.add(user)
    db.commit()
    db.refresh(user)

    # Log user update
    if changes:
        audit_service.log_action(
            db=db,
            action=models.AuditAction.USER_UPDATED,
            actor_user_id=actor_user_id,
            target_user_id=user.id,
            details={"changes": changes},
        )

    return user


def delete_user(
    db: Session,
    user_id: uuid.UUID,
    actor_user_id: uuid.UUID | None = None,
) -> None:
    stmt = select(models.User).where(models.User.id == user_id)
    user = db.execute(stmt).scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    # Log before deletion
    audit_service.log_action(
        db=db,
        action=models.AuditAction.USER_DELETED,
        actor_user_id=actor_user_id,
        target_user_id=user.id,
        details={"email": user.email},
    )

    db.delete(user)
    db.commit()


def list_users(db: Session) -> list[models.User]:
    stmt = select(models.User).order_by(models.User.created_at.asc())
    return list(db.execute(stmt).scalars().all())


def get_user(db: Session, user_id: uuid.UUID) -> models.User | None:
    stmt = select(models.User).where(models.User.id == user_id)
    return db.execute(stmt).scalar_one_or_none()


def set_admin(db: Session, user_id: uuid.UUID, value: bool) -> models.User:
    user = get_user(db, user_id)
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    user.is_admin = value
    db.add(user)
    db.commit()
    db.refresh(user)
    return user
