"""Structured logging configuration for Control Plane.

This module provides JSON-formatted structured logging with:
- Configurable log levels (DEBUG, INFO, WARNING, ERROR, CRITICAL)
- JSON or text format output
- Request context (request_id, user_id) via context variables
- Standard fields: timestamp, level, logger, message
"""

from __future__ import annotations

import logging
import sys
from contextvars import ContextVar
from datetime import datetime, timezone
from typing import Any

# Support both python-json-logger v2.x and v4.x import paths
try:
    from pythonjsonlogger.json import JsonFormatter as BaseJsonFormatter
except ImportError:
    from pythonjsonlogger import jsonlogger

    BaseJsonFormatter = jsonlogger.JsonFormatter

# Context variables for request tracing
request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)
user_id_var: ContextVar[str | None] = ContextVar("user_id", default=None)

# Flag to track if logging has been configured
_logging_configured = False


class StructuredJsonFormatter(BaseJsonFormatter):
    """Custom JSON formatter with standard fields and request context."""

    def add_fields(
        self,
        log_record: dict[str, Any],
        record: logging.LogRecord,
        message_dict: dict[str, Any],
    ) -> None:
        """Add custom fields to log record."""
        super().add_fields(log_record, record, message_dict)

        # Standard fields
        log_record["timestamp"] = datetime.now(timezone.utc).isoformat()
        log_record["level"] = record.levelname
        log_record["logger"] = record.name

        # Request context from context variables
        request_id = request_id_var.get()
        if request_id:
            log_record["request_id"] = request_id

        user_id = user_id_var.get()
        if user_id:
            log_record["user_id"] = user_id

        # Exception info
        if record.exc_info:
            log_record["error_type"] = record.exc_info[0].__name__ if record.exc_info[0] else None


class StructuredTextFormatter(logging.Formatter):
    """Text formatter with request context for development."""

    def format(self, record: logging.LogRecord) -> str:
        """Format log record as readable text with context."""
        # Add context to the record
        request_id = request_id_var.get()
        user_id = user_id_var.get()

        context_parts = []
        if request_id:
            context_parts.append(f"req={request_id[:8]}")
        if user_id:
            context_parts.append(f"user={user_id}")

        context_str = " ".join(context_parts)
        if context_str:
            context_str = f"[{context_str}] "

        # Format the base message
        timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        base_msg = (
            f"{timestamp} {record.levelname:8} {record.name}: {context_str}{record.getMessage()}"
        )

        # Add exception info if present
        if record.exc_info:
            base_msg += "\n" + self.formatException(record.exc_info)

        return base_msg


def configure_logging(log_level: str = "INFO", log_format: str = "json") -> None:
    """Configure structured logging for the application.

    Args:
        log_level: Logging level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
        log_format: Output format - 'json' for JSON lines, 'text' for readable text
    """
    global _logging_configured

    if _logging_configured:
        return

    # Parse log level
    numeric_level = getattr(logging, log_level.upper(), logging.INFO)

    # Create handler
    handler = logging.StreamHandler(sys.stdout)
    handler.setLevel(numeric_level)

    # Select formatter based on format setting
    if log_format.lower() == "json":
        formatter = StructuredJsonFormatter(fmt="%(timestamp)s %(level)s %(name)s %(message)s")
    else:
        formatter = StructuredTextFormatter()

    handler.setFormatter(formatter)

    # Configure root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(numeric_level)

    # Remove existing handlers and add our configured handler
    root_logger.handlers.clear()
    root_logger.addHandler(handler)

    # Suppress noisy third-party loggers
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    logging.getLogger("uvicorn.error").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)

    _logging_configured = True


def get_logger(name: str) -> logging.Logger:
    """Get a logger instance with the given name.

    Args:
        name: Logger name, typically __name__ of the module

    Returns:
        Configured logger instance
    """
    return logging.getLogger(name)


def set_request_context(request_id: str | None = None, user_id: str | None = None) -> None:
    """Set request context for logging.

    Args:
        request_id: Unique request ID for tracing
        user_id: Authenticated user ID
    """
    if request_id is not None:
        request_id_var.set(request_id)
    if user_id is not None:
        user_id_var.set(user_id)


def clear_request_context() -> None:
    """Clear request context after request completes."""
    request_id_var.set(None)
    user_id_var.set(None)
