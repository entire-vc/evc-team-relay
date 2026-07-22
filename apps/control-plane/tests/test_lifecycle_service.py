"""Tests for lifecycle_service — TR-18 atomic queue+mark-sent.

TR-18: queue_email() and marking a lifecycle trigger "sent" used to be two
independent commits (lifecycle_service.py:211-214). A crash between them
left the email queued but the trigger unclaimed, so the next detection
cycle's _already_handled() found nothing and fired the same trigger again —
a duplicate nudge. _queue_and_mark_sent() now commits both writes together
in one transaction, with the lifecycle_state unique constraint as a second
independent guard against a genuine race.

No SMTP call happens on this path (queue_email() only inserts a DB row —
actual sending is a separate later step in process_queued_email()), so
these tests use the real EmailService + a real SQLite session rather than
mocking the DB layer (per repo convention: mock only true external
boundaries).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.db.models import EmailQueue, LifecycleState, User
from app.services.email_service import get_email_service
from app.services.lifecycle_service import (
    _queue_and_mark_sent,
    process_t1_no_share_24h,
)


def _make_user(db_session, email="nudge-target@example.com", created_at=None):
    user = User(
        email=email,
        password_hash="x",
        is_admin=False,
        is_active=True,
        created_at=created_at or (datetime.now(timezone.utc) - timedelta(hours=48)),
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    return user


class TestQueueAndMarkSent:
    def test_first_call_queues_email_and_claims_trigger(self, db_session):
        user = _make_user(db_session)
        email_svc = get_email_service()

        result = _queue_and_mark_sent(
            db_session,
            email_svc,
            user.id,
            "trig-1",
            user.email,
            "subject",
            "text",
            "<html/>",
            "lifecycle_no_share_24h",
        )

        assert result is True
        assert db_session.query(EmailQueue).count() == 1
        state = (
            db_session.query(LifecycleState).filter_by(user_id=user.id, trigger_key="trig-1").one()
        )
        assert state.state == "sent"

    def test_second_call_for_same_trigger_is_rejected_and_does_not_double_queue(self, db_session):
        """The actual regression: a second claim attempt for the same
        (user, trigger) — the observable shape of "the process crashed and
        the cycle re-ran" — must not leave a second EmailQueue row behind.
        Pre-fix, queue_email()'s own commit had already landed by the time
        _mark_sent() ran, so there was nothing to roll back."""
        user = _make_user(db_session)
        email_svc = get_email_service()

        first = _queue_and_mark_sent(
            db_session,
            email_svc,
            user.id,
            "trig-1",
            user.email,
            "s1",
            "t1",
            "<h1/>",
            "lifecycle_no_share_24h",
        )
        second = _queue_and_mark_sent(
            db_session,
            email_svc,
            user.id,
            "trig-1",
            user.email,
            "s2",
            "t2",
            "<h2/>",
            "lifecycle_no_share_24h",
        )

        assert first is True
        assert second is False
        assert db_session.query(EmailQueue).count() == 1
        assert (
            db_session.query(LifecycleState)
            .filter_by(user_id=user.id, trigger_key="trig-1")
            .count()
            == 1
        )

    def test_session_remains_usable_after_a_rejected_claim(self, db_session):
        """A rejected claim must roll back cleanly, not poison the session
        for whoever processes the next user in the same batch loop."""
        user = _make_user(db_session)
        other_user = _make_user(db_session, email="other@example.com")
        email_svc = get_email_service()

        _queue_and_mark_sent(
            db_session,
            email_svc,
            user.id,
            "trig-1",
            user.email,
            "s",
            "t",
            "<h/>",
            "lifecycle_no_share_24h",
        )
        _queue_and_mark_sent(
            db_session,
            email_svc,
            user.id,
            "trig-1",
            user.email,
            "s",
            "t",
            "<h/>",
            "lifecycle_no_share_24h",
        )  # rejected — would leave the session unusable if rollback were missing

        third = _queue_and_mark_sent(
            db_session,
            email_svc,
            other_user.id,
            "trig-1",
            other_user.email,
            "s",
            "t",
            "<h/>",
            "lifecycle_no_share_24h",
        )

        assert third is True
        assert db_session.query(EmailQueue).count() == 2


class TestProcessT1DuplicatePrevention:
    def test_rerunning_the_cycle_does_not_double_send_the_same_due_user(
        self, db_session, monkeypatch
    ):
        """The task's own verify method: re-running the detection cycle over
        a user who's still due (the observable effect of a crash-and-restart
        landing between the old code's two commits) must send once, not
        twice."""
        monkeypatch.setenv("ARGUS_ENABLED", "false")
        from app.core.config import get_settings

        get_settings.cache_clear()

        user = _make_user(db_session, created_at=datetime.now(timezone.utc) - timedelta(hours=48))
        launch_cutoff = datetime.now(timezone.utc) - timedelta(days=365)

        sent1 = process_t1_no_share_24h(db_session, launch_cutoff)
        sent2 = process_t1_no_share_24h(db_session, launch_cutoff)

        assert sent1 == 1
        assert sent2 == 0
        assert db_session.query(EmailQueue).count() == 1
        assert db_session.query(EmailQueue).filter_by(to_email=user.email).count() == 1

        get_settings.cache_clear()
