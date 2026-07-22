"""Tests for TR-48 (#68f7cd73): email_queue/webhook_deliveries retention cleanup.

Neither table had a purge path — email_queue accumulates full HTML bodies and
recipient addresses (PII), webhook_deliveries accumulates arbitrary event
payloads, both with rows going back to February and no retention policy. Fixed
with a background worker (mirroring the existing run_metrics_collector()
pattern) that hard-deletes rows older than settings.data_retention_days.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import models
from app.services import webhook_service
from app.workers import retention_worker


def make_email(
    db: Session, created_at: datetime, to_email: str = "someone@example.com"
) -> models.EmailQueue:
    email = models.EmailQueue(
        to_email=to_email,
        subject="Test",
        body_text="body",
        body_html="<p>body</p>",
        email_type="invite_notification",
        status=models.EmailStatus.SENT,
        created_at=created_at,
    )
    db.add(email)
    db.commit()
    db.refresh(email)
    return email


def make_webhook(db: Session) -> models.Webhook:
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


def make_delivery(
    db: Session, webhook: models.Webhook, created_at: datetime
) -> models.WebhookDelivery:
    delivery = models.WebhookDelivery(
        webhook_id=webhook.id,
        event_id=uuid.uuid4(),
        event_type="share.created",
        payload={"foo": "bar"},
        status=models.WebhookDeliveryStatus.SUCCESS,
        created_at=created_at,
    )
    db.add(delivery)
    db.commit()
    db.refresh(delivery)
    return delivery


def test_settings_has_data_retention_days() -> None:
    """Settings must expose data_retention_days so the retention worker works."""
    from app.core.config import get_settings

    settings = get_settings()
    assert hasattr(settings, "data_retention_days")
    assert isinstance(settings.data_retention_days, int)
    assert settings.data_retention_days > 0


class TestCleanupOldRecords:
    def test_deletes_email_queue_rows_older_than_retention(self, db_session: Session):
        now = datetime.now(timezone.utc)
        old_email_id = make_email(db_session, created_at=now - timedelta(days=100)).id
        recent_email_id = make_email(db_session, created_at=now - timedelta(days=1)).id

        counts = retention_worker.cleanup_old_records(db_session, retention_days=90)
        db_session.commit()

        assert counts["email_queue_deleted"] == 1
        remaining_ids = set(db_session.execute(select(models.EmailQueue.id)).scalars().all())
        assert old_email_id not in remaining_ids
        assert recent_email_id in remaining_ids

    def test_deletes_webhook_deliveries_older_than_retention(self, db_session: Session):
        now = datetime.now(timezone.utc)
        webhook = make_webhook(db_session)
        old_delivery_id = make_delivery(
            db_session, webhook, created_at=now - timedelta(days=100)
        ).id
        recent_delivery_id = make_delivery(
            db_session, webhook, created_at=now - timedelta(days=1)
        ).id

        counts = retention_worker.cleanup_old_records(db_session, retention_days=90)
        db_session.commit()

        assert counts["webhook_deliveries_deleted"] == 1
        remaining_ids = set(db_session.execute(select(models.WebhookDelivery.id)).scalars().all())
        assert old_delivery_id not in remaining_ids
        assert recent_delivery_id in remaining_ids

    def test_returns_zero_counts_when_nothing_is_old_enough(self, db_session: Session):
        now = datetime.now(timezone.utc)
        make_email(db_session, created_at=now - timedelta(days=1))
        webhook = make_webhook(db_session)
        make_delivery(db_session, webhook, created_at=now - timedelta(days=1))

        counts = retention_worker.cleanup_old_records(db_session, retention_days=90)

        assert counts == {"email_queue_deleted": 0, "webhook_deliveries_deleted": 0}

    def test_does_not_touch_the_parent_webhook_row(self, db_session: Session):
        """Deleting old deliveries must not cascade up and remove the webhook
        registration itself — only the delivery history rows are retention-bound."""
        now = datetime.now(timezone.utc)
        webhook = make_webhook(db_session)
        make_delivery(db_session, webhook, created_at=now - timedelta(days=100))

        retention_worker.cleanup_old_records(db_session, retention_days=90)
        db_session.commit()

        assert (
            db_session.execute(
                select(models.Webhook).where(models.Webhook.id == webhook.id)
            ).scalar_one_or_none()
            is not None
        )

    def test_boundary_row_just_inside_the_window_is_kept(self, db_session: Session):
        now = datetime.now(timezone.utc)
        # 89 days old with a 90-day retention window: must survive.
        kept = make_email(db_session, created_at=now - timedelta(days=89))

        counts = retention_worker.cleanup_old_records(db_session, retention_days=90)
        db_session.commit()

        assert counts["email_queue_deleted"] == 0
        assert (
            db_session.execute(
                select(models.EmailQueue).where(models.EmailQueue.id == kept.id)
            ).scalar_one_or_none()
            is not None
        )
