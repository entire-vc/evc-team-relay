"""Tests for structured logging."""

from __future__ import annotations

import json
import logging
from io import StringIO

from app.core.logging import (
    StructuredJsonFormatter,
    StructuredTextFormatter,
    clear_request_context,
    get_logger,
    request_id_var,
    set_request_context,
    user_id_var,
)


class TestStructuredJsonFormatter:
    """Test JSON log formatter."""

    def test_json_formatter_basic_fields(self) -> None:
        """Test that JSON formatter includes standard fields."""
        formatter = StructuredJsonFormatter()
        stream = StringIO()
        handler = logging.StreamHandler(stream)
        handler.setFormatter(formatter)

        logger = logging.getLogger("test_json_basic")
        logger.handlers.clear()
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)

        logger.info("Test message")
        handler.flush()

        output = stream.getvalue()
        log_entry = json.loads(output.strip())

        assert "timestamp" in log_entry
        assert log_entry["level"] == "INFO"
        assert log_entry["logger"] == "test_json_basic"
        assert log_entry["message"] == "Test message"

    def test_json_formatter_with_extra(self) -> None:
        """Test that JSON formatter includes extra fields."""
        formatter = StructuredJsonFormatter()
        stream = StringIO()
        handler = logging.StreamHandler(stream)
        handler.setFormatter(formatter)

        logger = logging.getLogger("test_json_extra")
        logger.handlers.clear()
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)

        logger.info("Test message", extra={"user_id": "123", "action": "login"})
        handler.flush()

        output = stream.getvalue()
        log_entry = json.loads(output.strip())

        assert log_entry["user_id"] == "123"
        assert log_entry["action"] == "login"

    def test_json_formatter_with_request_context(self) -> None:
        """Test that JSON formatter includes request context."""
        formatter = StructuredJsonFormatter()
        stream = StringIO()
        handler = logging.StreamHandler(stream)
        handler.setFormatter(formatter)

        logger = logging.getLogger("test_json_context")
        logger.handlers.clear()
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)

        # Set request context
        set_request_context(request_id="req-123", user_id="user-456")
        try:
            logger.info("Test message")
            handler.flush()

            output = stream.getvalue()
            log_entry = json.loads(output.strip())

            assert log_entry["request_id"] == "req-123"
            assert log_entry["user_id"] == "user-456"
        finally:
            clear_request_context()


class TestStructuredTextFormatter:
    """Test text log formatter."""

    def test_text_formatter_basic(self) -> None:
        """Test that text formatter produces readable output."""
        formatter = StructuredTextFormatter()
        stream = StringIO()
        handler = logging.StreamHandler(stream)
        handler.setFormatter(formatter)

        logger = logging.getLogger("test_text_basic")
        logger.handlers.clear()
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)

        logger.info("Test message")
        handler.flush()

        output = stream.getvalue()
        assert "INFO" in output
        assert "test_text_basic" in output
        assert "Test message" in output

    def test_text_formatter_with_context(self) -> None:
        """Test that text formatter includes request context."""
        formatter = StructuredTextFormatter()
        stream = StringIO()
        handler = logging.StreamHandler(stream)
        handler.setFormatter(formatter)

        logger = logging.getLogger("test_text_context")
        logger.handlers.clear()
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)

        set_request_context(request_id="req-abc123", user_id="user-xyz")
        try:
            logger.info("Test message")
            handler.flush()

            output = stream.getvalue()
            # Text format shows truncated request_id
            assert "req=req-abc1" in output
            assert "user=user-xyz" in output
        finally:
            clear_request_context()


class TestRequestContext:
    """Test request context management."""

    def test_set_and_clear_context(self) -> None:
        """Test setting and clearing request context."""
        # Clear any existing context
        clear_request_context()

        # Initially empty
        assert request_id_var.get() is None
        assert user_id_var.get() is None

        # Set context
        set_request_context(request_id="req-1", user_id="user-1")
        assert request_id_var.get() == "req-1"
        assert user_id_var.get() == "user-1"

        # Clear context
        clear_request_context()
        assert request_id_var.get() is None
        assert user_id_var.get() is None

    def test_partial_context_update(self) -> None:
        """Test that partial context updates don't clear other values."""
        clear_request_context()

        set_request_context(request_id="req-1")
        assert request_id_var.get() == "req-1"
        assert user_id_var.get() is None

        set_request_context(user_id="user-1")
        assert request_id_var.get() == "req-1"
        assert user_id_var.get() == "user-1"

        clear_request_context()


class TestGetLogger:
    """Test logger factory."""

    def test_get_logger_returns_logger(self) -> None:
        """Test that get_logger returns a logger instance."""
        logger = get_logger("test_module")
        assert isinstance(logger, logging.Logger)
        assert logger.name == "test_module"

    def test_get_logger_same_name_returns_same_logger(self) -> None:
        """Test that get_logger returns the same logger for same name."""
        logger1 = get_logger("test_same")
        logger2 = get_logger("test_same")
        assert logger1 is logger2


class TestRequestLogging:
    """Test request logging through FastAPI TestClient."""

    def test_request_includes_request_id(self, client) -> None:
        """Test that responses include X-Request-ID header."""
        response = client.get("/health")
        assert "X-Request-ID" in response.headers
        # Request ID should be a UUID format
        request_id = response.headers["X-Request-ID"]
        assert len(request_id) == 36  # UUID format

    def test_request_id_is_unique(self, client) -> None:
        """Test that each request gets a unique request ID."""
        ids = set()
        for _ in range(5):
            response = client.get("/health")
            ids.add(response.headers["X-Request-ID"])

        assert len(ids) == 5  # All IDs should be unique

    def test_404_logs_at_info_not_warning(self, client, caplog) -> None:
        """TR-63: a 404 (e.g. a scanner probing a garbage slug like /.env, /.idea
        under /v1/web/shares/{slug}) must not log at WARNING — it's routine, not
        an actionable signal, and was drowning real WARNINGs in scanner noise."""
        import logging as logging_module

        with caplog.at_level(logging_module.INFO, logger="app.middleware.logging"):
            response = client.get("/v1/web/shares/.env")

        assert response.status_code == 404
        request_records = [
            r for r in caplog.records if r.message in ("Request error", "Request not found")
        ]
        assert len(request_records) == 1
        assert request_records[0].levelname == "INFO"
        assert request_records[0].message == "Request not found"

    def test_real_4xx_still_logs_at_warning(self, client, caplog) -> None:
        """TR-63 regression guard: only 404 is downgraded — a genuine client-error
        4xx (e.g. an invalid refresh token, 401) must still warn, so this fix
        doesn't silently swallow actionable signals along with the scanner noise."""
        import logging as logging_module

        with caplog.at_level(logging_module.INFO, logger="app.middleware.logging"):
            response = client.post("/v1/auth/refresh", json={"refresh_token": "a" * 64})

        assert response.status_code == 401
        request_records = [
            r for r in caplog.records if r.message in ("Request error", "Request not found")
        ]
        assert len(request_records) == 1
        assert request_records[0].levelname == "WARNING"
        assert request_records[0].message == "Request error"
