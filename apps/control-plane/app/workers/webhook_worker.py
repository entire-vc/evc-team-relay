#!/usr/bin/env python3
"""Background worker for processing webhook deliveries.

This worker polls the database for pending webhook deliveries and attempts
to deliver them. Failed deliveries are retried with exponential backoff.

Usage:
    python -m app.workers.webhook_worker

Environment:
    DATABASE_URL: PostgreSQL connection string
    WORKER_INTERVAL: Polling interval in seconds (default: 30)
    WORKER_BATCH_SIZE: Max deliveries to process per cycle (default: 50)
"""

from __future__ import annotations

import asyncio
import os
import signal
import sys

# Add the app directory to the path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

from app.core.logging import get_logger
from app.db.session import get_sessionmaker
from app.services import webhook_service

logger = get_logger(__name__)

# Configuration
WORKER_INTERVAL = int(os.getenv("WORKER_INTERVAL", "30"))
WORKER_BATCH_SIZE = int(os.getenv("WORKER_BATCH_SIZE", "50"))

# Graceful shutdown flag
shutdown_flag = False


def signal_handler(signum, frame):
    """Handle shutdown signals."""
    global shutdown_flag
    logger.info(f"Received signal {signum}, initiating graceful shutdown...")
    shutdown_flag = True


async def process_pending_deliveries() -> int:
    """Process pending webhook deliveries.

    Returns:
        Number of deliveries processed
    """
    db = get_sessionmaker()()
    processed = 0

    try:
        # Get pending deliveries
        deliveries = webhook_service.get_pending_deliveries(db, limit=WORKER_BATCH_SIZE)

        if not deliveries:
            return 0

        logger.info(f"Processing {len(deliveries)} pending webhook deliveries")

        for delivery in deliveries:
            if shutdown_flag:
                logger.info("Shutdown requested, stopping delivery processing")
                break

            try:
                # Attempt delivery
                success = await webhook_service.deliver_webhook(db, delivery)
                processed += 1

                if success:
                    logger.debug(
                        f"Webhook delivery {delivery.id} succeeded",
                        extra={"webhook_id": str(delivery.webhook_id)},
                    )
                else:
                    logger.debug(
                        f"Webhook delivery {delivery.id} failed/scheduled for retry",
                        extra={"webhook_id": str(delivery.webhook_id)},
                    )

            except Exception as e:
                logger.error(
                    f"Error processing webhook delivery {delivery.id}: {e}",
                    exc_info=True,
                )

        return processed

    except Exception as e:
        logger.error(f"Error in process_pending_deliveries: {e}", exc_info=True)
        return processed

    finally:
        db.close()


async def main():
    """Main worker loop."""
    global shutdown_flag

    # Setup signal handlers
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)

    logger.info(
        "Webhook worker started",
        extra={
            "interval": WORKER_INTERVAL,
            "batch_size": WORKER_BATCH_SIZE,
        },
    )

    while not shutdown_flag:
        try:
            processed = await process_pending_deliveries()

            if processed > 0:
                logger.info(f"Processed {processed} webhook deliveries")

        except Exception as e:
            logger.error(f"Error in webhook worker main loop: {e}", exc_info=True)

        # Wait for next cycle
        for _ in range(WORKER_INTERVAL):
            if shutdown_flag:
                break
            await asyncio.sleep(1)

    logger.info("Webhook worker stopped gracefully")


if __name__ == "__main__":
    asyncio.run(main())
