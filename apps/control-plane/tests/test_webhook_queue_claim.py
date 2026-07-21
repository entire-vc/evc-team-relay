"""Tests for TR-11 (#8a509876): row-claim in the webhook delivery queue.

Same root cause and fix shape as tests/test_email_queue_claim.py — see that
file's module docstring for the full incident background and the note on
why the actual cross-connection non-overlap guarantee (SELECT ... FOR
UPDATE SKIP LOCKED) can't be exercised on this suite's SQLite test DB.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from app.db import models
from app.db.models import WebhookDelivery, WebhookDeliveryStatus
from app.services import webhook_service
from app.services.webhook_service import WEBHOOK_CLAIM_LEASE_SECONDS


def _make_webhook(db: Session) -> models.Webhook:
    webhook = models.Webhook(
        id=uuid.uuid4(),
        user_id=None,
        name="test-hook",
        url="https://example.com/hook",
        secret=webhook_service.generate_webhook_secret(),
        events=["share.created"],
        active=True,
        failure_count=0,
    )
    db.add(webhook)
    db.commit()
    db.refresh(webhook)
    return webhook


def _make_delivery(
    db: Session,
    webhook: models.Webhook,
    status: WebhookDeliveryStatus = WebhookDeliveryStatus.PENDING,
    next_retry_at: datetime | None = None,
    claimed_at: datetime | None = None,
) -> WebhookDelivery:
    now = datetime.now(timezone.utc)
    delivery = WebhookDelivery(
        webhook_id=webhook.id,
        event_id=uuid.uuid4(),
        event_type="share.created",
        payload={"share_id": str(uuid.uuid4())},
        status=status,
        next_retry_at=next_retry_at if next_retry_at is not None else now,
        claimed_at=claimed_at,
    )
    db.add(delivery)
    db.commit()
    db.refresh(delivery)
    return delivery


class TestClaimPendingDeliveries:
    def test_claims_a_due_pending_delivery(self, db_session: Session):
        webhook = _make_webhook(db_session)
        delivery = _make_delivery(db_session, webhook)

        claimed = webhook_service.claim_pending_deliveries(db_session, limit=10)

        assert [d.id for d in claimed] == [delivery.id]
        db_session.refresh(delivery)
        assert delivery.status == WebhookDeliveryStatus.SENDING
        assert delivery.claimed_at is not None

    def test_does_not_claim_a_not_yet_due_delivery(self, db_session: Session):
        webhook = _make_webhook(db_session)
        future = datetime.now(timezone.utc) + timedelta(minutes=5)
        _make_delivery(db_session, webhook, next_retry_at=future)

        claimed = webhook_service.claim_pending_deliveries(db_session, limit=10)

        assert claimed == []

    def test_does_not_claim_a_row_already_sending_within_lease(self, db_session: Session):
        """The exact split-brain scenario: another worker claimed this
        delivery moments ago — it must not be claimed again while live."""
        webhook = _make_webhook(db_session)
        recent = datetime.now(timezone.utc) - timedelta(seconds=5)
        _make_delivery(db_session, webhook, status=WebhookDeliveryStatus.SENDING, claimed_at=recent)

        claimed = webhook_service.claim_pending_deliveries(db_session, limit=10)

        assert claimed == []

    def test_reclaims_a_delivery_stuck_sending_past_the_lease(self, db_session: Session):
        webhook = _make_webhook(db_session)
        stale = datetime.now(timezone.utc) - timedelta(seconds=WEBHOOK_CLAIM_LEASE_SECONDS + 60)
        delivery = _make_delivery(
            db_session, webhook, status=WebhookDeliveryStatus.SENDING, claimed_at=stale
        )

        claimed = webhook_service.claim_pending_deliveries(db_session, limit=10)

        assert [d.id for d in claimed] == [delivery.id]
        db_session.refresh(delivery)
        assert delivery.claimed_at.replace(tzinfo=timezone.utc) > stale

    def test_does_not_claim_terminal_states(self, db_session: Session):
        webhook = _make_webhook(db_session)
        _make_delivery(db_session, webhook, status=WebhookDeliveryStatus.SUCCESS)
        _make_delivery(db_session, webhook, status=WebhookDeliveryStatus.FAILED)
        _make_delivery(db_session, webhook, status=WebhookDeliveryStatus.MAX_RETRIES_EXCEEDED)

        claimed = webhook_service.claim_pending_deliveries(db_session, limit=10)

        assert claimed == []

    def test_respects_limit(self, db_session: Session):
        webhook = _make_webhook(db_session)
        for _ in range(5):
            _make_delivery(db_session, webhook)

        claimed = webhook_service.claim_pending_deliveries(db_session, limit=3)

        assert len(claimed) == 3


class TestScheduleRetryReleasesClaim:
    """TR-11's specific fix for the retry path: the old code left `status`
    untouched on a retryable failure (implicitly still PENDING, since
    nothing claimed it before). Now that claim_pending_deliveries() flips
    it to SENDING first, _schedule_retry() MUST explicitly revert to
    PENDING — otherwise every retry is silently stuck in SENDING until the
    5-minute lease expires, even though the retry backoff itself can be as
    short as 60s."""

    def test_reverts_to_pending_and_clears_claim_below_max_retries(self, db_session: Session):
        webhook = _make_webhook(db_session)
        delivery = _make_delivery(
            db_session,
            webhook,
            status=WebhookDeliveryStatus.SENDING,
            claimed_at=datetime.now(timezone.utc),
        )
        delivery.attempt_count = 1

        webhook_service._schedule_retry(db_session, delivery, webhook)

        assert delivery.status == WebhookDeliveryStatus.PENDING
        assert delivery.claimed_at is None
        assert delivery.next_retry_at is not None

    def test_max_retries_marks_exceeded_and_clears_claim(self, db_session: Session):
        from app.services.webhook_service import MAX_RETRIES

        webhook = _make_webhook(db_session)
        delivery = _make_delivery(
            db_session,
            webhook,
            status=WebhookDeliveryStatus.SENDING,
            claimed_at=datetime.now(timezone.utc),
        )
        delivery.attempt_count = MAX_RETRIES

        webhook_service._schedule_retry(db_session, delivery, webhook)

        assert delivery.status == WebhookDeliveryStatus.MAX_RETRIES_EXCEEDED
        assert delivery.claimed_at is None
