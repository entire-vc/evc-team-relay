#!/usr/bin/env python3
"""Background worker for lifecycle email nudges.

Polls for trigger conditions (T1 no_share_24h, T2 inactive_after_share) on a
configurable interval. Guarded by LIFECYCLE_ENABLED feature flag — worker runs
but is a no-op when the flag is false, enabling zero-risk forward-only rollout.

Usage:
    python -m app.workers.lifecycle_worker

Environment:
    DATABASE_URL: PostgreSQL connection string
    LIFECYCLE_ENABLED: Set to 'true' to activate nudges (default: false)
    LIFECYCLE_LAUNCH_DATE: ISO datetime cutoff; only users registered on/after
        this date are nudged (forward-only guard)
    LIFECYCLE_WORKER_INTERVAL: Poll interval in seconds (default: 3600)
"""

from __future__ import annotations

import asyncio
import os
import signal
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

from app.core.config import get_settings
from app.core.logging import get_logger
from app.db.session import get_sessionmaker
from app.services.lifecycle_service import (
    process_t1_no_share_24h,
    process_t2_inactive_after_share,
)

logger = get_logger(__name__)

WORKER_INTERVAL = int(os.getenv("LIFECYCLE_WORKER_INTERVAL", "3600"))

shutdown_flag = False


def _signal_handler(signum, frame) -> None:
    global shutdown_flag
    logger.info("Received signal %s — initiating graceful shutdown", signum)
    shutdown_flag = True


async def run_lifecycle_cycle() -> dict[str, int]:
    """Run one detection cycle. Returns counts per trigger."""
    settings = get_settings()

    if not settings.lifecycle_enabled:
        logger.debug("Lifecycle worker disabled (LIFECYCLE_ENABLED=false)")
        return {}

    if not settings.lifecycle_launch_date:
        logger.warning("LIFECYCLE_LAUNCH_DATE not set — skipping cycle")
        return {}

    try:
        launch_cutoff = datetime.fromisoformat(settings.lifecycle_launch_date)
        if launch_cutoff.tzinfo is None:
            launch_cutoff = launch_cutoff.replace(tzinfo=timezone.utc)
    except ValueError as e:
        logger.error("Invalid LIFECYCLE_LAUNCH_DATE %r: %s", settings.lifecycle_launch_date, e)
        return {}

    db = get_sessionmaker()()
    try:
        t1 = process_t1_no_share_24h(db, launch_cutoff)
        t2 = process_t2_inactive_after_share(db, launch_cutoff)
        logger.info(
            "Lifecycle cycle complete",
            extra={"t1_no_share_24h": t1, "t2_inactive_after_share": t2},
        )
        return {"t1_no_share_24h": t1, "t2_inactive_after_share": t2}
    except Exception:
        logger.exception("Error in lifecycle cycle")
        return {}
    finally:
        db.close()


async def main() -> None:
    global shutdown_flag

    signal.signal(signal.SIGTERM, _signal_handler)
    signal.signal(signal.SIGINT, _signal_handler)

    settings = get_settings()
    logger.info(
        "Lifecycle worker started",
        extra={
            "interval": WORKER_INTERVAL,
            "lifecycle_enabled": settings.lifecycle_enabled,
            "launch_date": settings.lifecycle_launch_date,
        },
    )

    if not settings.lifecycle_enabled:
        logger.warning(
            "Lifecycle nudges disabled (LIFECYCLE_ENABLED=false). "
            "Set LIFECYCLE_ENABLED=true to activate after E2E smoke passes."
        )

    while not shutdown_flag:
        try:
            await run_lifecycle_cycle()
        except Exception:
            logger.exception("Unhandled error in lifecycle worker main loop")

        for _ in range(WORKER_INTERVAL):
            if shutdown_flag:
                break
            await asyncio.sleep(1)

    logger.info("Lifecycle worker stopped gracefully")


if __name__ == "__main__":
    asyncio.run(main())
