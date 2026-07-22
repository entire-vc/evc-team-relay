"""Background data-retention cleanup — runs every 24h as an in-process asyncio task.

email_queue and webhook_deliveries accumulate indefinitely with no purge (TR-48):
email_queue holds recipient addresses and full HTML/text bodies, webhook_deliveries
holds arbitrary event payloads — both are PII/GDPR-relevant and had rows going back
to February with no retention policy. Started via app startup hook in app/main.py,
mirroring the existing run_metrics_collector() pattern.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.logging import get_logger
from app.db import models
from app.db.session import get_sessionmaker

logger = get_logger(__name__)

RETENTION_CLEANUP_INTERVAL_SECONDS = 86400  # 24h — this is maintenance, not a hot path
INITIAL_DELAY_SECONDS = 30  # let startup DB operations (bootstrap admin, etc.) settle first


def cleanup_old_records(db: Session, retention_days: int) -> dict[str, int]:
    """Hard-delete email_queue/webhook_deliveries rows older than retention_days.

    Pure w.r.t. the caller's session (no commit/close here — the caller owns the
    transaction boundary) so this is directly unit-testable against any Session.
    Returns the deleted row count per table.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)

    # synchronize_session=False: this worker owns no in-memory objects that need
    # to stay in sync with the delete, and the default "evaluate" strategy chokes
    # on tz-naive vs tz-aware datetime comparison under SQLite (this repo's test
    # backend) — let the DB evaluate the WHERE clause directly instead.
    email_result = db.execute(
        delete(models.EmailQueue)
        .where(models.EmailQueue.created_at < cutoff)
        .execution_options(synchronize_session=False)
    )
    webhook_result = db.execute(
        delete(models.WebhookDelivery)
        .where(models.WebhookDelivery.created_at < cutoff)
        .execution_options(synchronize_session=False)
    )

    return {
        "email_queue_deleted": email_result.rowcount,
        "webhook_deliveries_deleted": webhook_result.rowcount,
    }


def _run_cleanup() -> None:
    """Open a fresh session, run the purge, commit, and log the result."""
    settings = get_settings()
    db = get_sessionmaker()()
    try:
        counts = cleanup_old_records(db, settings.data_retention_days)
        db.commit()
        logger.info(
            "retention_worker: cleanup complete",
            extra={"retention_days": settings.data_retention_days, **counts},
        )
    except Exception as exc:
        db.rollback()
        logger.error("retention_worker: cleanup failed", extra={"error": str(exc)})
    finally:
        db.close()


async def run_retention_cleanup() -> None:
    """Async loop started at app startup. Purges old PII-bearing rows every 24h.

    Runs promptly after a short startup-settle delay (unlike the metrics collector,
    which sleeps a full interval first — retention correctness doesn't benefit from
    waiting, and there is no reason to leave known-stale rows sitting for up to a
    full day before the first cleanup ever runs).
    """
    logger.info(
        "retention_worker: background cleanup started",
        extra={"interval": RETENTION_CLEANUP_INTERVAL_SECONDS},
    )
    await asyncio.sleep(INITIAL_DELAY_SECONDS)
    while True:
        try:
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, _run_cleanup)
        except Exception as exc:
            logger.error("retention_worker: unexpected error", extra={"error": str(exc)})
        await asyncio.sleep(RETENTION_CLEANUP_INTERVAL_SECONDS)
