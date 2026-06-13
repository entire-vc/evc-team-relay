"""Argus CRM integration for Team Relay (S7/S2-3).

Two public functions:
  register_product_user(email, display_name, registered_at, casdoor_id) —
    fire-and-forget on TR registration. Upserts the Argus contact, logs an
    ownership interaction with occurred_at=registered_at, and stores casdoor_id.

  is_suppressed(email) -> bool — blocking pre-send check in lifecycle worker.
    Returns True if the contact has a cross-product opt-out (SO-3 rule-5).
    Fails open on timeout / Argus unavailability (returns False, logs warning).
"""

from __future__ import annotations

import logging
import threading
from datetime import datetime

import httpx

from app.core.config import get_settings

logger = logging.getLogger(__name__)

_PRODUCT = "team-relay"


def _client() -> httpx.Client:
    settings = get_settings()
    headers: dict[str, str] = {}
    if settings.argus_service_key:
        headers["X-Argus-Service-Key"] = settings.argus_service_key
    return httpx.Client(
        base_url=settings.argus_api_url,
        headers=headers,
        timeout=settings.argus_timeout_seconds,
    )


def _register_blocking(
    email: str,
    display_name: str,
    registered_at: datetime | None = None,
    casdoor_id: str | None = None,
) -> None:
    try:
        with _client() as client:
            payload: dict = {"email": email, "display_name": display_name, "product": _PRODUCT}
            if registered_at is not None:
                payload["registered_at"] = registered_at.isoformat()
            if casdoor_id is not None:
                payload["casdoor_id"] = casdoor_id
            resp = client.post(
                "/api/outreach/register_product_user",
                json=payload,
            )
            if resp.status_code not in (200, 201):
                logger.warning(
                    "argus register_product_user failed: %s — %s",
                    resp.status_code,
                    resp.text[:200],
                )
    except Exception:
        logger.warning("argus register_product_user error", exc_info=True)


def register_product_user(
    email: str,
    display_name: str,
    registered_at: datetime | None = None,
    casdoor_id: str | None = None,
) -> None:
    """Non-blocking: register TR user in Argus CRM.

    Spawns a daemon thread so registration latency never blocks the HTTP response.
    Errors are logged but never raised.

    Args:
        email: User email address.
        display_name: Display name (used as Argus contact name).
        registered_at: Timestamp of TR/Casdoor registration; Argus uses this as
            occurred_at for the ownership interaction. Defaults to now() in Argus.
        casdoor_id: Casdoor subject ID (provider_user_id from UserOAuthAccount).
            Stored as an identity in Argus to support cross-product dedup.
    """
    settings = get_settings()
    if not settings.argus_enabled:
        return
    threading.Thread(
        target=_register_blocking,
        kwargs={
            "email": email,
            "display_name": display_name,
            "registered_at": registered_at,
            "casdoor_id": casdoor_id,
        },
        daemon=True,
        name="argus-register",
    ).start()


def is_suppressed(email: str) -> bool:
    """Check cross-product hard suppression in Argus (SO-3 rule-5).

    Returns True only when Argus explicitly says suppressed=true.
    Returns False on timeout, network error, or unknown contact (fail-open).
    """
    settings = get_settings()
    if not settings.argus_enabled:
        return False
    try:
        with _client() as client:
            resp = client.post(
                "/api/outreach/suppression_check",
                json={"email": email},
            )
            if resp.status_code == 200:
                return bool(resp.json().get("suppressed"))
            logger.warning(
                "argus suppression_check unexpected status: %s — %s",
                resp.status_code,
                resp.text[:200],
            )
    except httpx.TimeoutException:
        logger.warning("argus suppression_check timed out for %s, failing open", email)
    except Exception:
        logger.warning("argus suppression_check error", exc_info=True)
    return False
