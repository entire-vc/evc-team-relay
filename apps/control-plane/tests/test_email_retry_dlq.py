"""Tests for TR-35 (#adc5086f): email-worker retry/DLQ.

EMAIL_RETRY_INTERVALS used to be [60, 300, 900] — 3 attempts over ~21min,
then FAILED forever with no way to ever retry. A routine 30min SMTP outage
silently lost transactional email (invites, security alerts). Fixed by
matching the webhook worker's schedule (RETRY_INTERVALS in
webhook_service.py: 1m/5m/15m/1h/6h/24h, 6 attempts) and adding an admin
requeue endpoint for the terminal FAILED state.
"""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.db.models import EmailQueue, EmailStatus
from app.services import email_service
from app.services.webhook_service import MAX_RETRIES as WEBHOOK_MAX_RETRIES
from app.services.webhook_service import RETRY_INTERVALS as WEBHOOK_RETRY_INTERVALS


def auth_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def login(client: TestClient, email: str, password: str) -> str:
    response = client.post("/auth/login", json={"email": email, "password": password})
    assert response.status_code == 200, response.text
    return response.json()["access_token"]


def make_email(
    db: Session,
    status: EmailStatus = EmailStatus.FAILED,
    attempt_count: int = 3,
    error_message: str | None = "SMTP delivery failed, scheduled for retry",
) -> EmailQueue:
    email = EmailQueue(
        to_email="someone@example.com",
        subject="Test",
        body_text="body",
        body_html="<p>body</p>",
        email_type="invite_notification",
        status=status,
        attempt_count=attempt_count,
        error_message=error_message,
    )
    db.add(email)
    db.commit()
    db.refresh(email)
    return email


class TestRetrySchedule:
    def test_matches_webhook_worker_schedule(self):
        """The whole point of TR-35: email and webhook workers must not
        silently diverge again — same intervals, same attempt budget."""
        assert email_service.EMAIL_RETRY_INTERVALS == WEBHOOK_RETRY_INTERVALS
        assert email_service.MAX_EMAIL_RETRIES == WEBHOOK_MAX_RETRIES

    def test_covers_the_reported_30min_outage(self):
        """Task's own repro: SMTP down 30min must NOT exhaust retries."""
        elapsed = 0
        attempts_within_30min = 0
        for interval in email_service.EMAIL_RETRY_INTERVALS:
            elapsed += interval
            if elapsed <= 30 * 60:
                attempts_within_30min += 1
        assert (
            attempts_within_30min < email_service.MAX_EMAIL_RETRIES
        ), "a 30min outage must not exhaust all retry attempts"

    def test_total_retry_window_is_at_least_24h(self):
        assert sum(email_service.EMAIL_RETRY_INTERVALS) >= 24 * 3600


class TestProcessQueuedEmailStillMarksFailedAtBudgetEnd:
    @pytest.mark.asyncio
    async def test_status_is_failed_after_max_retries_exceeded(self, db_session: Session):
        """Regression guard: exhausting the (now longer) budget still lands
        on the terminal FAILED status the requeue endpoint targets."""
        svc = email_service.EmailService()
        svc.email_enabled = True
        svc.smtp_host = ""  # _send_smtp() returns False immediately, no real SMTP needed
        email = make_email(
            db_session,
            status=EmailStatus.PENDING,
            attempt_count=email_service.MAX_EMAIL_RETRIES - 1,
        )

        result = await svc.process_queued_email(db_session, email)

        assert result is False
        assert email.status == EmailStatus.FAILED
        assert email.attempt_count == email_service.MAX_EMAIL_RETRIES


class TestRequeueEmailServiceGuard:
    def test_refuses_to_requeue_a_pending_email(self, db_session: Session):
        """Backstop below the router's own check — a future call site that
        forgets to check status first must fail loud, not silently reset a
        still-retrying email's budget and let the worker re-send it."""
        svc = email_service.EmailService()
        email = make_email(db_session, status=EmailStatus.PENDING, attempt_count=1)

        with pytest.raises(ValueError, match="only FAILED emails may be requeued"):
            svc.requeue_email(db_session, email)

    def test_refuses_to_requeue_a_sent_email(self, db_session: Session):
        svc = email_service.EmailService()
        email = make_email(db_session, status=EmailStatus.SENT, attempt_count=1)

        with pytest.raises(ValueError, match="only FAILED emails may be requeued"):
            svc.requeue_email(db_session, email)


class TestAdminRequeueEmail:
    def test_requeue_resets_failed_email_to_pending(self, db_session: Session, client: TestClient):
        admin_token = login(client, "bootstrap@example.com", "super-secret")
        email = make_email(db_session, status=EmailStatus.FAILED, attempt_count=6)

        response = client.post(
            f"/admin/emails/{email.id}/requeue",
            headers=auth_headers(admin_token),
        )

        assert response.status_code == 200, response.text
        data = response.json()
        assert data["status"] == EmailStatus.PENDING.value
        assert data["attempt_count"] == 0
        assert data["error_message"] is None

    def test_requeue_pending_email_is_rejected(self, db_session: Session, client: TestClient):
        """Only FAILED (exhausted) emails may be requeued — a still-retrying
        PENDING email has its own schedule already, resetting it would
        silently discard its position in that schedule."""
        admin_token = login(client, "bootstrap@example.com", "super-secret")
        email = make_email(db_session, status=EmailStatus.PENDING, attempt_count=1)

        response = client.post(
            f"/admin/emails/{email.id}/requeue",
            headers=auth_headers(admin_token),
        )

        assert response.status_code == 409

    def test_requeue_sent_email_is_rejected(self, db_session: Session, client: TestClient):
        admin_token = login(client, "bootstrap@example.com", "super-secret")
        email = make_email(db_session, status=EmailStatus.SENT, attempt_count=1)

        response = client.post(
            f"/admin/emails/{email.id}/requeue",
            headers=auth_headers(admin_token),
        )

        assert response.status_code == 409

    def test_requeue_unknown_email_returns_404(self, client: TestClient):
        admin_token = login(client, "bootstrap@example.com", "super-secret")

        response = client.post(
            f"/admin/emails/{uuid.uuid4()}/requeue",
            headers=auth_headers(admin_token),
        )

        assert response.status_code == 404

    def test_requeue_requires_admin(self, db_session: Session, client: TestClient):
        client.post(
            "/admin/users",
            json={
                "email": "not-admin@example.com",
                "password": "password123",
                "is_admin": False,
                "is_active": True,
            },
            headers=auth_headers(login(client, "bootstrap@example.com", "super-secret")),
        )
        non_admin_token = login(client, "not-admin@example.com", "password123")
        email = make_email(db_session, status=EmailStatus.FAILED)

        response = client.post(
            f"/admin/emails/{email.id}/requeue",
            headers=auth_headers(non_admin_token),
        )

        assert response.status_code == 403

    def test_list_emails_filters_by_status(self, db_session: Session, client: TestClient):
        admin_token = login(client, "bootstrap@example.com", "super-secret")
        make_email(db_session, status=EmailStatus.FAILED)
        make_email(db_session, status=EmailStatus.SENT)

        response = client.get(
            "/admin/emails",
            params={"status": EmailStatus.FAILED.value},
            headers=auth_headers(admin_token),
        )

        assert response.status_code == 200, response.text
        results = response.json()
        assert len(results) == 1
        assert results[0]["status"] == EmailStatus.FAILED.value
