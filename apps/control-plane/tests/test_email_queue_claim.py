"""Tests for TR-11 (#8a509876): row-claim in the email queue.

Root cause: two worker instances could both SELECT the same PENDING rows
and both send — no row-claim existed, and status was updated in a single
commit AFTER the SMTP attempt, so a SIGKILL mid-send left the row PENDING
forever poised to resend. claim_pending_emails() now does the equivalent of
SELECT ... FOR UPDATE SKIP LOCKED, flips claimed rows to SENDING before any
SMTP attempt, and reclaims rows stuck in SENDING past a lease timeout.

Note: `FOR UPDATE SKIP LOCKED` is a genuine Postgres row-locking feature —
this suite runs on SQLite (this repo's test DB), which silently ignores the
clause. These tests verify the surrounding state machine (what gets
claimed, when it's released, when a stale claim is reclaimed) — the actual
cross-connection non-overlap guarantee was verified separately against a
disposable Postgres container (see PR description) since SQLite has no
row-level locking to exercise.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.orm import Session

from app.db.models import EmailQueue, EmailStatus
from app.services.email_service import EMAIL_CLAIM_LEASE_SECONDS, EmailService


def _make_email(
    db: Session,
    status: EmailStatus = EmailStatus.PENDING,
    next_retry_at: datetime | None = None,
    claimed_at: datetime | None = None,
) -> EmailQueue:
    now = datetime.now(timezone.utc)
    email = EmailQueue(
        to_email="user@example.com",
        subject="Test",
        body_text="text",
        body_html="<p>html</p>",
        email_type="generic",
        status=status,
        next_retry_at=next_retry_at if next_retry_at is not None else now,
        claimed_at=claimed_at,
    )
    db.add(email)
    db.commit()
    db.refresh(email)
    return email


@pytest.fixture
def email_service() -> EmailService:
    # Uses the test session's configured settings (see conftest.py); tests
    # that care about the SMTP-vs-log branch override .email_enabled
    # directly after construction.
    return EmailService()


class TestClaimPendingEmails:
    def test_claims_a_due_pending_email(self, db_session: Session, email_service: EmailService):
        email = _make_email(db_session)

        claimed = email_service.claim_pending_emails(db_session, limit=10)

        assert [e.id for e in claimed] == [email.id]
        db_session.refresh(email)
        assert email.status == EmailStatus.SENDING
        assert email.claimed_at is not None

    def test_does_not_claim_a_not_yet_due_email(
        self, db_session: Session, email_service: EmailService
    ):
        future = datetime.now(timezone.utc) + timedelta(minutes=5)
        _make_email(db_session, next_retry_at=future)

        claimed = email_service.claim_pending_emails(db_session, limit=10)

        assert claimed == []

    def test_does_not_claim_a_row_already_sending_within_lease(
        self, db_session: Session, email_service: EmailService
    ):
        """The exact split-brain scenario: another worker claimed this row
        moments ago — it must not be claimed again while its lease is live."""
        recent = datetime.now(timezone.utc) - timedelta(seconds=5)
        _make_email(db_session, status=EmailStatus.SENDING, claimed_at=recent)

        claimed = email_service.claim_pending_emails(db_session, limit=10)

        assert claimed == []

    def test_reclaims_a_row_stuck_sending_past_the_lease(
        self, db_session: Session, email_service: EmailService
    ):
        """Crash-recovery path: a worker claimed this row and died before
        recording an outcome — it must become claimable again."""
        stale = datetime.now(timezone.utc) - timedelta(seconds=EMAIL_CLAIM_LEASE_SECONDS + 60)
        email = _make_email(db_session, status=EmailStatus.SENDING, claimed_at=stale)

        claimed = email_service.claim_pending_emails(db_session, limit=10)

        assert [e.id for e in claimed] == [email.id]
        db_session.refresh(email)
        # SQLite (this suite's test DB) drops tzinfo on read-back; compare
        # naive-vs-naive rather than assuming aware datetimes round-trip.
        assert email.claimed_at.replace(tzinfo=timezone.utc) > stale

    def test_does_not_claim_terminal_states(self, db_session: Session, email_service: EmailService):
        _make_email(db_session, status=EmailStatus.SENT)
        _make_email(db_session, status=EmailStatus.FAILED)

        claimed = email_service.claim_pending_emails(db_session, limit=10)

        assert claimed == []

    def test_respects_limit(self, db_session: Session, email_service: EmailService):
        for _ in range(5):
            _make_email(db_session)

        claimed = email_service.claim_pending_emails(db_session, limit=3)

        assert len(claimed) == 3


class TestProcessQueuedEmailReleasesClaim:
    """Once claimed (SENDING), process_queued_email must always leave the
    row in a state a future claim_pending_emails() call can act on
    correctly — never stuck in SENDING itself."""

    @pytest.mark.asyncio
    async def test_success_clears_claim_and_marks_sent(
        self, db_session: Session, email_service: EmailService
    ):
        email = _make_email(
            db_session, status=EmailStatus.SENDING, claimed_at=datetime.now(timezone.utc)
        )
        email_service.email_enabled = True
        email_service._send_smtp = lambda msg: True  # type: ignore[method-assign]

        result = await email_service.process_queued_email(db_session, email)

        assert result is True
        assert email.status == EmailStatus.SENT
        assert email.claimed_at is None

    @pytest.mark.asyncio
    async def test_disabled_transport_clears_claim_without_marking_sent(
        self, db_session: Session, email_service: EmailService
    ):
        """TR-04 (#ac65cfe5): a disabled transport used to take this exact
        branch and fabricate SENT — the row released its claim but the DB
        recorded a delivery that never happened. Disabled now releases the
        claim back to PENDING instead, so the row is retried for real once
        EMAIL_ENABLED flips back on."""
        email = _make_email(
            db_session, status=EmailStatus.SENDING, claimed_at=datetime.now(timezone.utc)
        )
        email_service.email_enabled = False

        result = await email_service.process_queued_email(db_session, email)

        assert result is False
        assert email.status == EmailStatus.PENDING
        assert email.sent_at is None
        assert email.claimed_at is None

    @pytest.mark.asyncio
    async def test_retry_reverts_to_pending_and_clears_claim(
        self, db_session: Session, email_service: EmailService
    ):
        """TR-11's specific fix: the old code left status untouched on a
        retryable failure (implicitly still PENDING, since nothing claimed
        it before). Now that claim_pending_emails() flips it to SENDING
        first, process_queued_email MUST explicitly revert to PENDING on
        retry — otherwise the row is silently stuck in SENDING forever
        (well past the lease, so it would eventually get reclaimed, but a
        60s email retry interval is far shorter than the 300s lease — that
        gap alone would delay every retry by minutes for no reason)."""
        email = _make_email(
            db_session, status=EmailStatus.SENDING, claimed_at=datetime.now(timezone.utc)
        )
        email_service.email_enabled = True
        email_service._send_smtp = lambda msg: False  # type: ignore[method-assign]

        result = await email_service.process_queued_email(db_session, email)

        assert result is False
        assert email.status == EmailStatus.PENDING
        assert email.claimed_at is None
        assert email.next_retry_at is not None

    @pytest.mark.asyncio
    async def test_max_retries_clears_claim_and_marks_failed(
        self, db_session: Session, email_service: EmailService
    ):
        from app.services.email_service import MAX_EMAIL_RETRIES

        email = _make_email(
            db_session, status=EmailStatus.SENDING, claimed_at=datetime.now(timezone.utc)
        )
        email.attempt_count = MAX_EMAIL_RETRIES - 1
        email_service.email_enabled = True
        email_service._send_smtp = lambda msg: False  # type: ignore[method-assign]

        result = await email_service.process_queued_email(db_session, email)

        assert result is False
        assert email.status == EmailStatus.FAILED
        assert email.claimed_at is None
