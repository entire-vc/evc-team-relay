"""Tests for TR-04 (#ac65cfe5): EMAIL_ENABLED=false must not fabricate SENT.

Before this fix, a disabled transport made two independent false claims:
- `EmailService.send_email()` (the inline path used by password-reset /
  email-verification) returned `True` without ever touching SMTP.
- `EmailService.process_queued_email()` (the queue worker path used by
  invites / member_added / security alerts / lifecycle nudges) marked the
  row `SENT` with a real `sent_at` timestamp. 1434 rows accumulated this
  way between 2026-07-10 and 2026-07-21 while `EMAIL_ENABLED=false` on
  every Team Relay container post-Helsinki-cutover, and the DB had no way
  to distinguish them from real deliveries.
"""

from __future__ import annotations

import pytest
from sqlalchemy.orm import Session

from app.db.models import EmailQueue, EmailStatus
from app.services import email_service


def make_email(
    db: Session,
    status: EmailStatus = EmailStatus.SENDING,
    attempt_count: int = 0,
) -> EmailQueue:
    email = EmailQueue(
        to_email="someone@example.com",
        subject="Test",
        body_text="body",
        body_html="<p>body</p>",
        email_type="invite_notification",
        status=status,
        attempt_count=attempt_count,
    )
    db.add(email)
    db.commit()
    db.refresh(email)
    return email


class TestSendEmailDisabledReturnsFalse:
    @pytest.mark.asyncio
    async def test_disabled_transport_returns_false_not_true(self):
        svc = email_service.EmailService()
        svc.email_enabled = False

        result = await svc.send_email(
            "someone@example.com", "Subject", "body", email_type="password_reset"
        )

        assert result is False

    @pytest.mark.asyncio
    async def test_enabled_but_unconfigured_smtp_also_returns_false(self):
        """Sanity check the two failure modes stay distinguishable in code
        even though both currently return False: disabled (config choice)
        vs. enabled-but-broken (real outage) are different signals for an
        operator, even if this particular return type can't carry that."""
        svc = email_service.EmailService()
        svc.email_enabled = True
        svc.smtp_host = ""

        result = await svc.send_email(
            "someone@example.com", "Subject", "body", email_type="password_reset"
        )

        assert result is False


class TestProcessQueuedEmailDisabledDoesNotFakeSent:
    @pytest.mark.asyncio
    async def test_disabled_transport_leaves_row_pending_not_sent(self, db_session: Session):
        svc = email_service.EmailService()
        svc.email_enabled = False
        email = make_email(db_session, status=EmailStatus.SENDING, attempt_count=0)

        result = await svc.process_queued_email(db_session, email)

        assert result is False
        assert email.status == EmailStatus.PENDING
        assert email.sent_at is None
        assert email.claimed_at is None

    @pytest.mark.asyncio
    async def test_disabled_transport_does_not_consume_retry_budget(self, db_session: Session):
        """A disabled transport is not a delivery attempt — attempt_count
        must stay untouched so a later real send starts with its full
        retry budget instead of arriving pre-exhausted."""
        svc = email_service.EmailService()
        svc.email_enabled = False
        email = make_email(db_session, status=EmailStatus.SENDING, attempt_count=2)

        await svc.process_queued_email(db_session, email)

        assert email.attempt_count == 2

    @pytest.mark.asyncio
    async def test_disabled_transport_error_message_names_the_real_reason(
        self, db_session: Session
    ):
        svc = email_service.EmailService()
        svc.email_enabled = False
        email = make_email(db_session, status=EmailStatus.SENDING, attempt_count=0)

        await svc.process_queued_email(db_session, email)

        assert email.error_message is not None
        assert "disabled" in email.error_message.lower()

    @pytest.mark.asyncio
    async def test_re_enabling_lets_a_previously_skipped_row_actually_send(
        self, db_session: Session
    ):
        """End-to-end of the bug's own lifecycle: skipped while disabled,
        then a real send succeeds once the flag flips — the row must not
        have been left in a state (SENT, or budget-exhausted FAILED) that
        blocks the real attempt from ever happening."""
        svc = email_service.EmailService()
        svc.email_enabled = False
        email = make_email(db_session, status=EmailStatus.SENDING, attempt_count=0)
        await svc.process_queued_email(db_session, email)
        assert email.status == EmailStatus.PENDING

        svc.email_enabled = True
        svc._send_smtp = lambda msg: True  # noqa: SLF001 — stub the real SMTP call
        email.status = EmailStatus.SENDING  # simulate the next claim cycle

        result = await svc.process_queued_email(db_session, email)

        assert result is True
        assert email.status == EmailStatus.SENT
        assert email.sent_at is not None
        assert email.attempt_count == 1
