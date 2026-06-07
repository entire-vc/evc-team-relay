#!/usr/bin/env python3
"""Listmonk subscriber sync worker for team-relay-users list.

Polls for new TR registrations (via users.created_at cursor) and upserts them
into Listmonk list 8. Also runs a nightly full backfill to catch any drift.

Cursor is persisted in instance_settings.listmonk_sync_cursor so restarts
are safe and don't re-send already-synced users.

Usage:
    python -m app.workers.listmonk_sync_worker

Environment:
    DATABASE_URL: PostgreSQL connection string
    LISTMONK_URL: Listmonk base URL (e.g. https://lists.entire.host)
    LISTMONK_API_USER: Listmonk API username (default: api)
    LISTMONK_API_PASSWORD: Listmonk API password
    LISTMONK_LIST_ID: List ID (default: 8)
    LISTMONK_SYNC_INTERVAL: Poll interval in seconds (default: 300)
"""

from __future__ import annotations

import asyncio
import os
import signal
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

from sqlalchemy import text

from app.core.config import get_settings
from app.core.logging import get_logger
from app.db.session import get_sessionmaker
from app.services.listmonk_service import get_listmonk_service

logger = get_logger(__name__)

# How often to run the nightly reconcile full backfill
_BACKFILL_INTERVAL_HOURS = 24

_CURSOR_KEY = "listmonk_sync_cursor"
_LAST_BACKFILL_KEY = "listmonk_last_backfill"

shutdown_flag = False


def _signal_handler(signum, frame) -> None:
    global shutdown_flag
    logger.info("Received signal %s — initiating graceful shutdown", signum)
    shutdown_flag = True


def _get_setting(db, key: str) -> str | None:
    row = db.execute(
        text("SELECT value FROM instance_settings WHERE key = :k"), {"k": key}
    ).fetchone()
    return row.value if row else None


def _set_setting(db, key: str, value: str) -> None:
    db.execute(
        text(
            """
            INSERT INTO instance_settings (key, value)
            VALUES (:k, :v)
            ON CONFLICT (key) DO UPDATE SET value = :v, updated_at = NOW()
            """
        ),
        {"k": key, "v": value},
    )
    db.commit()


async def _run_incremental(svc, db, settings) -> None:
    """Poll for new users since last cursor and sync them."""
    cursor_str = _get_setting(db, _CURSOR_KEY)

    if cursor_str:
        since = datetime.fromisoformat(cursor_str)
    else:
        # First run: start from epoch so all existing users get picked up
        since = datetime(1970, 1, 1, tzinfo=timezone.utc)

    synced, new_cursor = await svc.sync_new_users(db, since)

    if synced > 0 or new_cursor is not None:
        logger.info("Incremental sync: synced=%d new users since %s", synced, since.isoformat())

    if new_cursor is not None:
        _set_setting(db, _CURSOR_KEY, new_cursor.isoformat())


async def _run_backfill_if_due(svc, db) -> None:
    """Run full backfill if it hasn't run in the last 24 hours."""
    last_str = _get_setting(db, _LAST_BACKFILL_KEY)

    if last_str:
        last = datetime.fromisoformat(last_str)
        if datetime.now(tz=timezone.utc) - last < timedelta(hours=_BACKFILL_INTERVAL_HOURS):
            return

    logger.info("Starting nightly full backfill to Listmonk")
    count = await svc.backfill_all(db)

    now_iso = datetime.now(tz=timezone.utc).isoformat()
    _set_setting(db, _LAST_BACKFILL_KEY, now_iso)

    # After backfill, advance cursor to now so incremental sync doesn't re-process
    _set_setting(db, _CURSOR_KEY, now_iso)

    # Log current list count for acceptance criteria verification
    try:
        list_count = await svc.get_list_subscriber_count()
        logger.info(
            "Backfill done: synced=%d, Listmonk list count=%s", count, list_count
        )
    except Exception:
        logger.warning("Could not fetch list subscriber count after backfill")


async def main() -> None:
    global shutdown_flag

    signal.signal(signal.SIGTERM, _signal_handler)
    signal.signal(signal.SIGINT, _signal_handler)

    settings = get_settings()

    if not settings.listmonk_enabled:
        logger.error(
            "Listmonk sync disabled — set LISTMONK_URL and LISTMONK_API_PASSWORD. Exiting."
        )
        sys.exit(1)

    svc = get_listmonk_service()
    await svc.start()

    logger.info(
        "Listmonk sync worker started",
        extra={
            "listmonk_url": settings.listmonk_url,
            "list_id": settings.listmonk_list_id,
            "sync_interval": settings.listmonk_sync_interval,
        },
    )

    try:
        while not shutdown_flag:
            db = get_sessionmaker()()
            try:
                await _run_backfill_if_due(svc, db)
                await _run_incremental(svc, db, settings)
            except Exception:
                logger.exception("Error in Listmonk sync cycle")
            finally:
                db.close()

            for _ in range(settings.listmonk_sync_interval):
                if shutdown_flag:
                    break
                await asyncio.sleep(1)
    finally:
        await svc.stop()

    logger.info("Listmonk sync worker stopped gracefully")


if __name__ == "__main__":
    asyncio.run(main())
