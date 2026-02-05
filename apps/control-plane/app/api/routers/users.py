from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.api import deps
from app.db import models
from app.db.session import get_db
from app.schemas import user as user_schema
from app.services import user_service

router = APIRouter(prefix="/users", tags=["users"])


@router.get("/search", response_model=user_schema.UserRead)
def search_user_by_email(
    email: str = Query(..., description="Email address to search for"),
    db: Session = Depends(get_db),
    _: models.User = Depends(deps.get_current_user),
):
    """
    Search for a user by email address.

    Returns user information if found, 404 if not found.
    Requires authentication.
    """
    user = user_service.get_user_by_email(db, email)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"User with email '{email}' not found"
        )
    return user
