"""Tests for TR-36 (#ea334842): security_new_session email spam.

notify_session_created() used to queue a "New login to your account" email
on every session, unconditionally — a reconnect-loop client hitting
/auth/login repeatedly sent 10 emails to one user in 2 minutes (observed
prod repro, 2026-07-07 13:10-13:12). The underlying UserSession row is
still created every time (unaffected by this fix); only the notification
email is suppressed within a per-(user, device) window.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core import security
from app.db.models import EmailQueue, User
from app.services import session_service
from app.services.email_service import EMAIL_TYPE_SECURITY_NEW_SESSION
from app.services.notification_service import NotificationService


def make_user(db: Session, email: str = "spam-target@example.com") -> User:
    user = User(
        id=uuid.uuid4(),
        email=email,
        password_hash=security.get_password_hash("test123456"),
        is_admin=False,
        is_active=True,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def count_session_emails(db: Session, to_email: str) -> int:
    stmt = select(EmailQueue).where(
        EmailQueue.to_email == to_email,
        EmailQueue.email_type == EMAIL_TYPE_SECURITY_NEW_SESSION,
    )
    return len(db.execute(stmt).scalars().all())


@pytest.mark.asyncio
class TestSecuritySessionEmailSuppression:
    async def test_second_session_same_device_is_suppressed(self, db_session: Session):
        user = make_user(db_session)
        service = NotificationService()

        session_service.create_session(db_session, user.id, device_name="MacBook Pro")
        await service.notify_session_created(db_session, user, device_name="MacBook Pro")

        session_service.create_session(db_session, user.id, device_name="MacBook Pro")
        await service.notify_session_created(db_session, user, device_name="MacBook Pro")

        assert count_session_emails(db_session, user.email) == 1

    async def test_10_sessions_in_a_row_send_at_most_1_email(self, db_session: Session):
        """The task's own repro, almost literally: a reconnect-loop hammering
        /auth/login from the same device must not spam the inbox."""
        user = make_user(db_session)
        service = NotificationService()

        for _ in range(10):
            session_service.create_session(db_session, user.id, device_name="Loop Device")
            await service.notify_session_created(db_session, user, device_name="Loop Device")

        assert count_session_emails(db_session, user.email) == 1

    async def test_different_device_still_gets_its_own_email(self, db_session: Session):
        """Suppression is per-device, not per-user — a genuinely new device
        must still notify even if another device notified recently."""
        user = make_user(db_session)
        service = NotificationService()

        session_service.create_session(db_session, user.id, device_name="MacBook Pro")
        await service.notify_session_created(db_session, user, device_name="MacBook Pro")

        session_service.create_session(db_session, user.id, device_name="iPhone")
        await service.notify_session_created(db_session, user, device_name="iPhone")

        assert count_session_emails(db_session, user.email) == 2

    async def test_oauth_fixed_device_name_does_not_collapse_different_devices(
        self, db_session: Session
    ):
        """OAuth logins (oauth.py) always pass the fixed literal
        f"OAuth ({provider})" as device_name for EVERY session regardless of
        the real device — device_name alone would wrongly suppress a
        genuinely different device's login. ip_address/user_agent must
        distinguish them."""
        user = make_user(db_session)
        service = NotificationService()

        session_service.create_session(
            db_session,
            user.id,
            device_name="OAuth (github)",
            ip_address="203.0.113.10",
            user_agent="Mozilla/5.0 (Macintosh)",
        )
        await service.notify_session_created(
            db_session,
            user,
            device_name="OAuth (github)",
            ip_address="203.0.113.10",
            user_agent="Mozilla/5.0 (Macintosh)",
        )

        # Different real machine, same OAuth provider => same device_name
        # string, but a different IP/user-agent.
        session_service.create_session(
            db_session,
            user.id,
            device_name="OAuth (github)",
            ip_address="198.51.100.20",
            user_agent="Mozilla/5.0 (iPhone)",
        )
        await service.notify_session_created(
            db_session,
            user,
            device_name="OAuth (github)",
            ip_address="198.51.100.20",
            user_agent="Mozilla/5.0 (iPhone)",
        )

        assert count_session_emails(db_session, user.email) == 2

    async def test_none_device_name_suppresses_against_itself_not_crash(self, db_session: Session):
        """device_name is optional (Authorization header may not send one) —
        the suppression query must handle NULL without raising, and treat
        repeated no-device logins as the same 'device' bucket."""
        user = make_user(db_session)
        service = NotificationService()

        session_service.create_session(db_session, user.id, device_name=None)
        await service.notify_session_created(db_session, user, device_name=None)

        session_service.create_session(db_session, user.id, device_name=None)
        await service.notify_session_created(db_session, user, device_name=None)

        assert count_session_emails(db_session, user.email) == 1

    async def test_suppression_window_expiring_allows_a_new_email(
        self, db_session: Session, monkeypatch
    ):
        from app.core.config import get_settings

        monkeypatch.setenv("SECURITY_NEW_SESSION_EMAIL_SUPPRESSION_HOURS", "0")
        get_settings.cache_clear()
        try:
            user = make_user(db_session)
            service = NotificationService()

            session_service.create_session(db_session, user.id, device_name="MacBook Pro")
            await service.notify_session_created(db_session, user, device_name="MacBook Pro")

            session_service.create_session(db_session, user.id, device_name="MacBook Pro")
            await service.notify_session_created(db_session, user, device_name="MacBook Pro")

            assert count_session_emails(db_session, user.email) == 2
        finally:
            monkeypatch.delenv("SECURITY_NEW_SESSION_EMAIL_SUPPRESSION_HOURS", raising=False)
            get_settings.cache_clear()
