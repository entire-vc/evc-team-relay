"""Request logging middleware with structured JSON output."""

from __future__ import annotations

import time
import uuid
from typing import Callable

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware

from app.core.logging import clear_request_context, get_logger, set_request_context

logger = get_logger(__name__)


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    """
    Middleware for structured request logging.

    Logs all HTTP requests with:
    - Request ID (for tracing)
    - Method and path
    - Status code
    - Duration
    - Client IP
    - User agent
    - User ID (if authenticated)
    """

    # Paths to skip detailed logging (reduce noise)
    QUIET_PATHS = {"/health", "/health/live", "/health/ready", "/metrics"}

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        # Generate unique request ID
        request_id = str(uuid.uuid4())
        request.state.request_id = request_id

        # Set request context for logging
        set_request_context(request_id=request_id)

        # Record start time
        start_time = time.time()

        # Extract client info
        client_host = request.client.host if request.client else "unknown"
        user_agent = request.headers.get("user-agent", "unknown")

        try:
            # Process request
            response = await call_next(request)

            # Get user_id if available (set by auth middleware/dependencies)
            user_id = getattr(request.state, "user_id", None)
            if user_id:
                set_request_context(user_id=str(user_id))

            # Calculate duration
            duration_ms = (time.time() - start_time) * 1000

            # Build log context
            log_context = {
                "request_id": request_id,
                "method": request.method,
                "path": request.url.path,
                "status_code": response.status_code,
                "duration_ms": round(duration_ms, 2),
                "client_ip": client_host,
            }

            # Add user_agent only for non-quiet paths to reduce log size
            if request.url.path not in self.QUIET_PATHS:
                log_context["user_agent"] = user_agent
                if user_id:
                    log_context["user_id"] = str(user_id)

            # Log based on status code
            if response.status_code >= 500:
                logger.error("Request failed", extra=log_context)
            elif response.status_code >= 400:
                logger.warning("Request error", extra=log_context)
            elif request.url.path not in self.QUIET_PATHS:
                logger.info("Request completed", extra=log_context)

            # Add request ID to response headers for tracing
            response.headers["X-Request-ID"] = request_id

            # Add deprecation warning for legacy routes (without /v1 prefix)
            path = request.url.path
            if not path.startswith("/v1/") and not path.startswith("/static"):
                # Check if this is an API route (not static files or docs)
                if (
                    path.startswith("/auth")
                    or path.startswith("/shares")
                    or path.startswith("/admin")
                    or path.startswith("/tokens")
                    or path.startswith("/keys")
                    or path.startswith("/users")
                    or path.startswith("/dashboard")
                    or path.startswith("/invites")
                    or path.startswith("/health")
                    or path.startswith("/server")
                ):
                    response.headers["X-API-Deprecation"] = (
                        "This route is deprecated. Use /v1{} instead.".format(path)
                    )
                    response.headers["X-API-Version"] = "legacy"
                else:
                    response.headers["X-API-Version"] = "1"
            else:
                response.headers["X-API-Version"] = "1"

            return response

        except Exception as exc:
            # Log the exception
            duration_ms = (time.time() - start_time) * 1000
            logger.exception(
                "Request raised exception",
                extra={
                    "request_id": request_id,
                    "method": request.method,
                    "path": request.url.path,
                    "duration_ms": round(duration_ms, 2),
                    "client_ip": client_host,
                    "error_type": type(exc).__name__,
                    "error_message": str(exc),
                },
            )
            raise

        finally:
            # Clear request context
            clear_request_context()
