"""Argus CRM integration for Team Relay (S7).

Two public functions:
  register_product_user(email, display_name) — fire-and-forget on TR registration.
    Upserts the Argus contact and logs an ownership interaction (60d window).

  is_suppressed(email) -> bool — blocking pre-send check in lifecycle worker.
    Returns True if the contact has a cross-product opt-out (SO-3 rule-5).
    Fails open on timeout / Argus unavailability (returns False, logs warning).
"""

from __future__ import annotations

import logging
import threading

import httpx

from app.core.config import get_settings

logger = logging.getLogger(__name__)

_PRODUCT = "team-relay"
_CHANNEL = "email"


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


def _register_blocking(email: str, display_name: str) -> None:
    try:
        with _client() as client:
            resp = client.post(
                "/api/outreach/register_product_user",
                json={"email": email, "display_name": display_name, "product": _PRODUCT},
            )
            if resp.status_code not in (200, 201):
                logger.warning(
                    "argus register_product_user failed: %s — %s",
                    resp.status_code,
                    resp.text[:200],
                )
    except Exception:
        logger.warning("argus register_product_user error", exc_info=True)


def register_product_user(email: str, display_name: str) -> None:
    """Non-blocking: register TR user in Argus CRM.

    Spawns a daemon thread so registration latency never blocks the HTTP response.
    Errors are logged but never raised.
    """
    settings = get_settings()
    if not settings.argus_enabled:
        return
    threading.Thread(
        target=_register_blocking,
        args=(email, display_name),
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
