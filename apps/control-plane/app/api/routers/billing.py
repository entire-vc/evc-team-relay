"""Billing API endpoints for plugin consumption."""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.api import deps
from app.clients.billing_client import BillingServiceError
from app.core.config import get_settings
from app.core.logging import get_logger
from app.db import models
from app.db.session import get_db
from app.services import billing_service
from app.services.instance_settings_service import get_branding

logger = get_logger(__name__)

router = APIRouter(prefix="/billing", tags=["billing"])

# Public router for billing callback (SSR page)
callback_router = APIRouter(prefix="/billing", tags=["billing"])

# Templates for SSR
templates = Jinja2Templates(directory="app/templates")


def _get_casdoor_id(db: Session, user: models.User) -> str:
    """Get Casdoor UUID for a user (for billing API calls).

    Delegates to billing_service.get_billing_identity() — the single place
    this resolution lives, so billing_webhooks.py's reverse lookup
    (find_user_by_billing_identity) can never drift out of sync with it.
    """
    return billing_service.get_billing_identity(db, user)


@router.get("/plan")
async def get_billing_plan(
    db: Session = Depends(get_db),
    current_user: models.User = Depends(deps.get_current_user),
):
    """Get current user's billing plan with usage info.

    Returns plan details, entitlements, and current usage metrics.
    """
    settings = get_settings()
    if not settings.billing_enabled:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Billing is not enabled",
        )

    casdoor_id = _get_casdoor_id(db, current_user)
    return await billing_service.get_billing_plan(casdoor_id, db, current_user.id)


@router.get("/plans")
async def get_billing_plans():
    """Get available billing plans.

    Returns all plans with their entitlements for the upgrade UI.
    No authentication required.
    """
    settings = get_settings()
    if not settings.billing_enabled:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Billing is not enabled",
        )

    plans = await billing_service.get_available_plans()
    return {"plans": plans}


@router.post("/checkout")
async def create_checkout(
    payload: dict,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(deps.get_current_user),
):
    """Create a checkout session for upgrading to a paid plan.

    Expects payload with:
    - product_id: Product ID to purchase
    - price_id: Price ID (optional)

    Returns checkout URL and subscription ID.
    """
    settings = get_settings()
    if not settings.billing_enabled:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Billing is not enabled",
        )

    casdoor_id = _get_casdoor_id(db, current_user)
    try:
        return await billing_service.create_checkout_session(casdoor_id, payload, db, current_user)
    except BillingServiceError as e:
        if 400 <= e.status < 500:
            logger.warning("Billing client error in create_checkout: %s", e)
            raise HTTPException(status_code=e.status, detail=e.message)
        logger.exception("Billing service error in create_checkout")
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Billing service unavailable",
        )


@router.post("/change-plan")
async def change_plan(
    payload: dict,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(deps.get_current_user),
):
    """Change plan on an existing subscription.

    Expects payload with:
    - product_id: New product ID
    - price_id: New price ID

    Returns updated subscription info.
    """
    settings = get_settings()
    if not settings.billing_enabled:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Billing is not enabled",
        )

    product_id = payload.get("product_id")
    price_id = payload.get("price_id")
    if not product_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="product_id is required",
        )
    if not price_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="price_id is required",
        )

    casdoor_id = _get_casdoor_id(db, current_user)
    try:
        return await billing_service.change_subscription_plan(
            casdoor_id,
            current_user.billing_subscription_id,
            product_id,
            price_id,
        )
    except BillingServiceError as e:
        if 400 <= e.status < 500:
            logger.warning("Billing client error in change_plan: %s", e)
            raise HTTPException(status_code=e.status, detail=e.message)
        logger.exception("Billing service error in change_plan")
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Billing service unavailable",
        )


@router.post("/cancel")
async def cancel_subscription(
    db: Session = Depends(get_db),
    current_user: models.User = Depends(deps.get_current_user),
):
    """Cancel current subscription.

    User will retain access until end of billing period (grace period).
    """
    settings = get_settings()
    if not settings.billing_enabled:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Billing is not enabled",
        )

    casdoor_id = _get_casdoor_id(db, current_user)
    try:
        return await billing_service.cancel_user_subscription(
            casdoor_id, current_user.billing_subscription_id
        )
    except BillingServiceError as e:
        if 400 <= e.status < 500:
            logger.warning("Billing client error in cancel_subscription: %s", e)
            raise HTTPException(status_code=e.status, detail=e.message)
        logger.exception("Billing service error in cancel_subscription")
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Billing service unavailable",
        )


@router.post("/portal")
async def create_portal_session(
    payload: dict = {},
    db: Session = Depends(get_db),
    current_user: models.User = Depends(deps.get_current_user),
):
    """Create a Stripe Customer Portal session for subscription management.

    Allows users to manage payment methods, view invoices, change plans.
    Returns a portal_url to open in browser.

    Optional payload:
    - return_url: URL to redirect to after portal session
    """
    settings = get_settings()
    if not settings.billing_enabled:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Billing is not enabled",
        )

    casdoor_id = _get_casdoor_id(db, current_user)
    return_url = payload.get("return_url") or settings.billing_return_url or ""

    try:
        return await billing_service.create_portal_session(casdoor_id, return_url)
    except BillingServiceError as e:
        if 400 <= e.status < 500:
            logger.warning("Billing client error in create_portal_session: %s", e)
            raise HTTPException(status_code=e.status, detail=e.message)
        logger.exception("Billing service error in create_portal_session")
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Billing service unavailable",
        )
    except Exception:
        logger.exception("Unexpected error in create_portal_session")
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Billing service unavailable",
        )


# Billing callback page (SSR) - no auth required, user comes from Stripe
@callback_router.get("/callback", response_class=HTMLResponse)
async def billing_callback(
    request: Request,
    db: Session = Depends(get_db),
    status: Optional[str] = Query(None, description="Payment status (legacy parameter)"),
    payment_status: Optional[str] = Query(None, description="Payment status from Stripe"),
    session_id: Optional[str] = Query(None, description="Stripe checkout session ID (legacy)"),
    subscription_id: Optional[str] = Query(None, description="Stripe subscription ID"),
):
    """Billing callback page shown after Stripe checkout completes.

    This is an SSR page that redirects the user back to the Obsidian plugin
    via deep link (obsidian://evc-team-relay/billing-ok).

    Query params from Stripe redirect (v4.1):
    - payment_status: success | cancelled | (other) [NEW]
    - subscription_id: Stripe subscription ID [NEW]

    Legacy params (backward compat):
    - status: success | canceled | (other)
    - session_id: Stripe checkout session ID

    No authentication required - user just came from Stripe.
    """
    branding = get_branding(db)

    # Use payment_status if provided, otherwise fall back to status
    final_status = payment_status or status

    # Map Stripe's "cancelled" (double-l) to "canceled" (single-l) for template consistency
    if final_status == "cancelled":
        final_status = "canceled"

    # Default to 'unknown' if no status provided
    if not final_status:
        final_status = "unknown"

    # Prefer subscription_id (new) over session_id (legacy)
    final_id = subscription_id or session_id

    return templates.TemplateResponse(
        request=request,
        name="billing_callback.html",
        context={
            "branding": branding,
            "status": final_status,
            "session_id": final_id,  # Template still uses session_id for display
            "subscription_id": final_id,
        },
    )
