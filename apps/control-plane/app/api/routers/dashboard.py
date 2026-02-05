from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api import deps
from app.db import models
from app.db.session import get_db
from app.schemas import dashboard as dashboard_schema
from app.services import dashboard_service

router = APIRouter(prefix="/dashboard", tags=["dashboard"])


@router.get("/admin/stats", response_model=dashboard_schema.AdminStats)
def get_admin_dashboard_stats(
    db: Session = Depends(get_db),
    _: models.User = Depends(deps.get_current_admin),
):
    """
    Get admin dashboard statistics.

    Returns aggregated statistics including:
    - User counts (total, active, admin)
    - Share counts (total, by kind, by visibility)
    - Recent activity (logins, shares created in last 24h)

    **Requires admin privileges.**
    """
    return dashboard_service.get_admin_stats(db)


@router.get("/user/stats", response_model=dashboard_schema.UserStats)
def get_user_dashboard_stats(
    db: Session = Depends(get_db),
    current_user: models.User = Depends(deps.get_current_user),
):
    """
    Get user dashboard statistics.

    Returns user-specific statistics including:
    - Owned and member share counts
    - Shares breakdown by kind
    - Total members across owned shares
    - Recent activity count (last 7 days)
    """
    return dashboard_service.get_user_stats(db, current_user)
