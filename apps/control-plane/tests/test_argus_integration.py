"""Tests for Argus CRM integration — argus_service + lifecycle_service suppression hook.

Unit tests use monkeypatching to avoid real Argus HTTP calls. The argus_service module
is tested for correct fire-and-forget behavior and fail-open semantics. The lifecycle
tests verify that suppressed contacts are skipped and unsuppressed ones proceed.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

from app.services import argus_service

# ---------------------------------------------------------------------------
# argus_service unit tests
# ---------------------------------------------------------------------------


class TestArgusServiceDisabled:
    """When ARGUS_ENABLED=false (default), all functions are no-ops."""

    def setup_method(self):
        os.environ["ARGUS_ENABLED"] = "false"
        from app.core.config import get_settings
        get_settings.cache_clear()

    def teardown_method(self):
        os.environ.pop("ARGUS_ENABLED", None)
        from app.core.config import get_settings
        get_settings.cache_clear()

    def test_register_product_user_noop(self):
        """register_product_user returns immediately without spawning a thread."""
        with patch("threading.Thread") as mock_thread:
            argus_service.register_product_user("user@example.com", "User")
            mock_thread.assert_not_called()

    def test_is_suppressed_returns_false(self):
        """is_suppressed returns False without touching Argus when disabled."""
        with patch("httpx.Client") as mock_client:
            result = argus_service.is_suppressed("user@example.com")
            assert result is False
            mock_client.assert_not_called()


class TestArgusServiceEnabled:
    """When ARGUS_ENABLED=true, functions hit the configured API."""

    def setup_method(self):
        os.environ["ARGUS_ENABLED"] = "true"
        os.environ["ARGUS_API_URL"] = "http://argus-test:8000"
        os.environ["ARGUS_SERVICE_KEY"] = "test-key"
        from app.core.config import get_settings
        get_settings.cache_clear()

    def teardown_method(self):
        for key in ("ARGUS_ENABLED", "ARGUS_API_URL", "ARGUS_SERVICE_KEY"):
            os.environ.pop(key, None)
        from app.core.config import get_settings
        get_settings.cache_clear()

    def test_is_suppressed_true(self):
        """is_suppressed returns True when Argus reports suppressed=true."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"suppressed": True, "reason": "opt_out", "person_id": "abc"}

        with patch("httpx.Client") as mock_client_cls:
            mock_client = MagicMock()
            mock_client_cls.return_value.__enter__.return_value = mock_client
            mock_client.post.return_value = mock_resp

            result = argus_service.is_suppressed("user@example.com")

        assert result is True
        mock_client.post.assert_called_once_with(
            "/api/outreach/suppression_check",
            json={"email": "user@example.com"},
        )

    def test_is_suppressed_false(self):
        """is_suppressed returns False when Argus reports suppressed=false."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"suppressed": False, "reason": None, "person_id": "abc"}

        with patch("httpx.Client") as mock_client_cls:
            mock_client = MagicMock()
            mock_client_cls.return_value.__enter__.return_value = mock_client
            mock_client.post.return_value = mock_resp

            result = argus_service.is_suppressed("user@example.com")

        assert result is False

    def test_is_suppressed_fail_open_on_timeout(self):
        """is_suppressed returns False on Argus timeout (fail-open per SO-3)."""
        import httpx

        with patch("httpx.Client") as mock_client_cls:
            mock_client = MagicMock()
            mock_client_cls.return_value.__enter__.return_value = mock_client
            mock_client.post.side_effect = httpx.TimeoutException("timeout")

            result = argus_service.is_suppressed("user@example.com")

        assert result is False

    def test_is_suppressed_fail_open_on_http_error(self):
        """is_suppressed returns False when Argus returns non-200 (fail-open)."""
        mock_resp = MagicMock()
        mock_resp.status_code = 503
        mock_resp.text = "Service Unavailable"

        with patch("httpx.Client") as mock_client_cls:
            mock_client = MagicMock()
            mock_client_cls.return_value.__enter__.return_value = mock_client
            mock_client.post.return_value = mock_resp

            result = argus_service.is_suppressed("user@example.com")

        assert result is False

    def test_register_product_user_spawns_daemon_thread(self):
        """register_product_user spawns a daemon thread and does not block."""
        with patch("threading.Thread") as mock_thread_cls:
            mock_thread = MagicMock()
            mock_thread_cls.return_value = mock_thread
            argus_service.register_product_user("new@example.com", "New User")
            mock_thread_cls.assert_called_once()
            assert mock_thread_cls.call_args.kwargs.get("daemon") is True
            mock_thread.start.assert_called_once()


# ---------------------------------------------------------------------------
# lifecycle_service suppression integration tests
# ---------------------------------------------------------------------------


def _make_user(email: str = "u@example.com"):
    """Create a minimal User-like object for lifecycle tests."""
    user = MagicMock()
    user.id = "11111111-1111-1111-1111-111111111111"
    user.email = email
    user.created_at = datetime(2020, 1, 1, tzinfo=timezone.utc)
    return user


class TestLifecycleArgusSuppressionT1:
    """T1 trigger (no_share_24h) skips suppressed users."""

    def setup_method(self):
        os.environ["ARGUS_ENABLED"] = "true"
        os.environ["ARGUS_API_URL"] = "http://argus-test:8000"
        from app.core.config import get_settings
        get_settings.cache_clear()

    def teardown_method(self):
        for key in ("ARGUS_ENABLED", "ARGUS_API_URL"):
            os.environ.pop(key, None)
        from app.core.config import get_settings
        get_settings.cache_clear()

    def test_suppressed_user_skipped(self):
        """T1: suppressed user → no email queued."""
        from app.services.lifecycle_service import process_t1_no_share_24h

        db = MagicMock()
        launch_cutoff = datetime(2019, 1, 1, tzinfo=timezone.utc)
        user = _make_user("suppressed@example.com")

        with (
            patch(
                "app.services.lifecycle_service._already_handled", return_value=False
            ),
            patch(
                "app.services.lifecycle_service._lifecycle_opt_in", return_value=True
            ),
            patch(
                "app.services.lifecycle_service._total_sent_count", return_value=0
            ),
            patch(
                "app.services.argus_service.is_suppressed", return_value=True
            ),
            patch("app.services.lifecycle_service.get_email_service") as mock_email,
        ):
            # Patch the DB query to return our user
            db.execute.return_value.scalars.return_value.all.return_value = [user]
            sent = process_t1_no_share_24h(db, launch_cutoff)

        assert sent == 0
        # email service's queue_email should NOT have been called
        mock_email.return_value.queue_email.assert_not_called()

    def test_unsuppressed_user_gets_email(self):
        """T1: unsuppressed user → email queued (argus returns False)."""
        from app.services.lifecycle_service import process_t1_no_share_24h

        db = MagicMock()
        launch_cutoff = datetime(2019, 1, 1, tzinfo=timezone.utc)
        user = _make_user("active@example.com")

        with (
            patch(
                "app.services.lifecycle_service._already_handled", return_value=False
            ),
            patch(
                "app.services.lifecycle_service._lifecycle_opt_in", return_value=True
            ),
            patch(
                "app.services.lifecycle_service._total_sent_count", return_value=0
            ),
            patch(
                "app.services.argus_service.is_suppressed", return_value=False
            ),
            patch(
                "app.services.lifecycle_service._render_template",
                return_value=("<html>", "text"),
            ),
            patch(
                "app.services.lifecycle_service._template_context", return_value={}
            ),
            patch(
                "app.services.lifecycle_service._mark_sent"
            ),
            patch("app.services.lifecycle_service.get_email_service") as mock_email,
        ):
            db.execute.return_value.scalars.return_value.all.return_value = [user]
            sent = process_t1_no_share_24h(db, launch_cutoff)

        assert sent == 1
        mock_email.return_value.queue_email.assert_called_once()
