#!/usr/bin/env python3
"""Background worker for processing email queue.

This worker polls the database for pending emails and attempts to send them
via SMTP. Failed deliveries are retried with exponential backoff.

Usage:
    python -m app.workers.email_worker

Environment:
    DATABASE_URL: PostgreSQL connection string
    WORKER_INTERVAL: Polling interval in seconds (default: 60)
    WORKER_BATCH_SIZE: Max emails to process per cycle (default: 100)
"""

from __future__ import annotations

import asyncio
import os
import signal
import sys

# Add the app directory to the path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

from app.core.config import get_settings
from app.core.logging import get_logger
from app.db.session import get_sessionmaker
from app.services.email_service import get_email_service

logger = get_logger(__name__)

# Configuration
WORKER_INTERVAL = int(os.getenv("WORKER_INTERVAL", "60"))
WORKER_BATCH_SIZE = int(os.getenv("WORKER_BATCH_SIZE", "100"))

# Graceful shutdown flag
shutdown_flag = False


def signal_handler(signum, frame):
    """Handle shutdown signals."""
    global shutdown_flag
    logger.info(f"Received signal {signum}, initiating graceful shutdown...")
    shutdown_flag = True


async def process_pending_emails() -> int:
    """Process pending emails from the queue.

    Returns:
        Number of emails processed
    """
    db = get_sessionmaker()()
    processed = 0
    email_service = get_email_service()

    try:
        # Get pending emails
        emails = email_service.get_pending_emails(db, limit=WORKER_BATCH_SIZE)

        if not emails:
            return 0

        logger.info(f"Processing {len(emails)} pending emails")

        for email in emails:
            if shutdown_flag:
                logger.info("Shutdown requested, stopping email processing")
                break

            try:
                # Attempt to send email
                success = await email_service.process_queued_email(db, email)
                processed += 1

                if success:
                    logger.debug(
                        f"Email {email.id} sent successfully",
                        extra={"to": email.to_email, "type": email.email_type},
                    )
                else:
                    logger.debug(
                        f"Email {email.id} failed/scheduled for retry",
                        extra={"to": email.to_email, "type": email.email_type},
                    )

            except Exception as e:
                logger.error(
                    f"Error processing email {email.id}: {e}",
                    exc_info=True,
                )

        return processed

    except Exception as e:
        logger.error(f"Error in process_pending_emails: {e}", exc_info=True)
        return processed

    finally:
        db.close()


async def main():
    """Main worker loop."""
    global shutdown_flag

    # Setup signal handlers
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)

    settings = get_settings()

    logger.info(
        "Email worker started",
        extra={
            "interval": WORKER_INTERVAL,
            "batch_size": WORKER_BATCH_SIZE,
            "email_enabled": settings.email_enabled,
        },
    )

    if not settings.email_enabled:
        logger.warning(
            "Email sending is disabled (EMAIL_ENABLED=false). "
            "Emails will be logged but not sent via SMTP."
        )

    while not shutdown_flag:
        try:
            processed = await process_pending_emails()

            if processed > 0:
                logger.info(f"Processed {processed} emails")

        except Exception as e:
            logger.error(f"Error in email worker main loop: {e}", exc_info=True)

        # Wait for next cycle
        for _ in range(WORKER_INTERVAL):
            if shutdown_flag:
                break
            await asyncio.sleep(1)

    logger.info("Email worker stopped gracefully")


if __name__ == "__main__":
    asyncio.run(main())
