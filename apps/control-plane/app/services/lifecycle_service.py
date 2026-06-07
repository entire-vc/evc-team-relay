"""Lifecycle nudge engine — trigger detection and email queuing.

Detects two triggers based on TR Postgres data (no new instrumentation needed):
- T1 no_share_24h: registered >=24h ago, still no shares
- T2 inactive_after_share: created share 7+ days ago, no SHARE_UPDATED activity since

Forward-only guard: only fires for users registered on or after LIFECYCLE_LAUNCH_DATE.
Idempotency: lifecycle_state table (unique on user_id, trigger_key) prevents double-sends.
Cap: max LIFECYCLE_CAP total nudges sent per user across all triggers.
Pre-send: lifecycle_emails preference checked (missing row = ON).

TODO(S7): add Argus suppression check pre-send (fast-follow, hook marked below).
"""

from __future__ import annotations

import hashlib
import hmac
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape
from sqlalchemy import exists, func, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.logging import get_logger
from app.db.models import (
    AuditAction,
    AuditLog,
    LifecycleState,
    Share,
    User,
    UserEmailPreferences,
)
from app.services.email_service import (
    EMAIL_TYPE_LIFECYCLE_INACTIVE_AFTER_SHARE,
    EMAIL_TYPE_LIFECYCLE_NO_SHARE_24H,
    get_email_service,
)

logger = get_logger(__name__)

LIFECYCLE_CAP = 2  # max total nudges per user across all triggers

_TEMPLATE_DIR = Path(__file__).parent.parent / "templates" / "emails"


def _jinja_env() -> Environment:
    return Environment(
        loader=FileSystemLoader(str(_TEMPLATE_DIR)),
        autoescape=select_autoescape(["html"]),
    )


# ------------------------------------------------------------------
# Unsubscribe token helpers (also used by the unsubscribe router)
# ------------------------------------------------------------------


def make_unsubscribe_token(user_id: uuid.UUID, secret: str) -> str:
    return hmac.new(secret.encode(), str(user_id).encode(), hashlib.sha256).hexdigest()


def verify_unsubscribe_token(user_id: uuid.UUID, token: str, secret: str) -> bool:
    expected = make_unsubscribe_token(user_id, secret)
    return hmac.compare_digest(expected, token)


# ------------------------------------------------------------------
# DB helpers
# ------------------------------------------------------------------


def _lifecycle_opt_in(db: Session, user_id: uuid.UUID) -> bool:
    """True if user has lifecycle emails ON (missing row = ON by default)."""
    prefs = db.execute(
        select(UserEmailPreferences).where(UserEmailPreferences.user_id == user_id)
    ).scalar_one_or_none()
    if prefs is None:
        return True
    return bool(prefs.lifecycle_emails)


def _total_sent_count(db: Session, user_id: uuid.UUID) -> int:
    """Count of lifecycle nudges already sent to this user."""
    return db.execute(
        select(func.count())
        .select_from(LifecycleState)
        .where(LifecycleState.user_id == user_id, LifecycleState.state == "sent")
    ).scalar_one()


def _already_handled(db: Session, user_id: uuid.UUID, trigger_key: str) -> bool:
    """True if this (user, trigger) was already sent or suppressed."""
    row = db.execute(
        select(LifecycleState).where(
            LifecycleState.user_id == user_id,
            LifecycleState.trigger_key == trigger_key,
        )
    ).scalar_one_or_none()
    return row is not None and row.state in ("sent", "suppressed")


def _mark_sent(db: Session, user_id: uuid.UUID, trigger_key: str) -> None:
    """Upsert lifecycle_state to sent (idempotent via unique constraint)."""
    now = datetime.now(timezone.utc)
    existing = db.execute(
        select(LifecycleState).where(
            LifecycleState.user_id == user_id,
            LifecycleState.trigger_key == trigger_key,
        )
    ).scalar_one_or_none()
    if existing is None:
        db.add(
            LifecycleState(
                user_id=user_id,
                trigger_key=trigger_key,
                state="sent",
                sent_at=now,
            )
        )
    else:
        existing.state = "sent"
        existing.sent_at = now
    db.commit()


# ------------------------------------------------------------------
# Template rendering
# ------------------------------------------------------------------


def _render_template(template_name: str, context: dict) -> tuple[str, str]:
    """Render both html and txt variants. Returns (html, text)."""
    env = _jinja_env()
    html = env.get_template(f"{template_name}.html").render(**context)
    text = env.get_template(f"{template_name}.txt").render(**context)
    return html, text


def _build_unsubscribe_url(user: User) -> str:
    settings = get_settings()
    token = make_unsubscribe_token(user.id, settings.lifecycle_unsubscribe_secret)
    base = (
        str(settings.relay_public_url)
        .rstrip("/")
        .replace("wss://", "https://")
        .replace("ws://", "http://")
    )
    return f"{base}/api/v1/public/unsubscribe/lifecycle?user={user.id}&token={token}"


def _template_context(user: User) -> dict:
    settings = get_settings()
    return {
        "name": user.email.split("@")[0],
        "recipient_email": user.email,
        "server_name": settings.server_name,
        "base_url": (
            str(settings.relay_public_url)
            .rstrip("/")
            .replace("wss://", "https://")
            .replace("ws://", "http://")
        ),
        "unsubscribe_url": _build_unsubscribe_url(user),
    }


# ------------------------------------------------------------------
# Trigger processors
# ------------------------------------------------------------------


def process_t1_no_share_24h(db: Session, launch_cutoff: datetime) -> int:
    """T1: registered >=24h ago, no shares yet. Forward-only (>= launch_cutoff)."""
    threshold = datetime.now(timezone.utc) - timedelta(hours=24)
    users = (
        db.execute(
            select(User).where(
                User.created_at >= launch_cutoff,
                User.created_at <= threshold,
                ~exists(select(Share.id).where(Share.owner_user_id == User.id)),
            )
        )
        .scalars()
        .all()
    )

    sent = 0
    email_svc = get_email_service()

    for user in users:
        trigger_key = "no_share_24h"
        if _already_handled(db, user.id, trigger_key):
            continue
        if not _lifecycle_opt_in(db, user.id):
            continue
        if _total_sent_count(db, user.id) >= LIFECYCLE_CAP:
            continue
        # TODO(S7): check Argus suppression here (fast-follow)

        ctx = _template_context(user)
        html, text = _render_template("lifecycle-no-share-24h", ctx)
        subject = "Шара в Team Relay — нужна помощь с подключением?"

        email_svc.queue_email(
            db, user.email, subject, text, html, EMAIL_TYPE_LIFECYCLE_NO_SHARE_24H
        )
        _mark_sent(db, user.id, trigger_key)
        sent += 1

    return sent


def process_t2_inactive_after_share(db: Session, launch_cutoff: datetime) -> int:
    """T2: created share 7+ days ago, no SHARE_UPDATED activity since.

    NOTE(v1): AuditAction.DOCUMENT_WRITE doesn't exist yet — using SHARE_UPDATED
    as a proxy for "user actively working with their share". Swap when
    DOCUMENT_WRITE audit action is added.
    """
    threshold = datetime.now(timezone.utc) - timedelta(days=7)

    # Users who have shares created >=7 days ago (forward-only)
    rows = db.execute(
        select(User, Share)
        .join(Share, Share.owner_user_id == User.id)
        .where(
            User.created_at >= launch_cutoff,
            Share.created_at <= threshold,
        )
    ).all()

    sent = 0
    email_svc = get_email_service()
    seen_users: set[uuid.UUID] = set()

    for user, share in rows:
        if user.id in seen_users:
            continue

        trigger_key = f"inactive_after_share_{share.id}"
        if len(trigger_key) > 64:
            trigger_key = trigger_key[:64]

        if _already_handled(db, user.id, trigger_key):
            seen_users.add(user.id)
            continue

        # Check for any SHARE_UPDATED activity by this user after share creation
        has_activity = db.execute(
            select(AuditLog.id)
            .where(
                AuditLog.actor_user_id == user.id,
                AuditLog.action == AuditAction.SHARE_UPDATED,
                AuditLog.timestamp > share.created_at,
            )
            .limit(1)
        ).scalar_one_or_none()

        if has_activity:
            seen_users.add(user.id)
            continue

        if not _lifecycle_opt_in(db, user.id):
            continue
        if _total_sent_count(db, user.id) >= LIFECYCLE_CAP:
            continue
        # TODO(S7): check Argus suppression here (fast-follow)

        ctx = _template_context(user)
        html, text = _render_template("lifecycle-inactive-after-share", ctx)
        subject = "Как дела с шарой в Team Relay?"

        email_svc.queue_email(
            db, user.email, subject, text, html, EMAIL_TYPE_LIFECYCLE_INACTIVE_AFTER_SHARE
        )
        _mark_sent(db, user.id, trigger_key)
        seen_users.add(user.id)
        sent += 1

    return sent
