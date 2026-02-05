"""Prometheus metrics middleware for HTTP request tracking."""

from __future__ import annotations

import time
from typing import Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from app.core.metrics import (
    HTTP_REQUEST_DURATION_SECONDS,
    HTTP_REQUEST_SIZE_BYTES,
    HTTP_REQUESTS_IN_PROGRESS,
    HTTP_REQUESTS_TOTAL,
    HTTP_RESPONSE_SIZE_BYTES,
)


def normalize_path(path: str) -> str:
    """Normalize path for metrics labels to avoid cardinality explosion.

    Replaces dynamic path segments (UUIDs, numeric IDs) with placeholders.
    """
    import re

    # Replace UUIDs
    path = re.sub(
        r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
        "{id}",
        path,
        flags=re.IGNORECASE,
    )
    # Replace numeric IDs
    path = re.sub(r"/\d+(?=/|$)", "/{id}", path)
    # Replace tokens (64 char hex strings)
    path = re.sub(r"/[0-9a-f]{64}(?=/|$)", "/{token}", path, flags=re.IGNORECASE)
    # Replace email verification tokens (shorter tokens)
    path = re.sub(r"/[0-9a-f]{32}(?=/|$)", "/{token}", path, flags=re.IGNORECASE)

    return path


class PrometheusMiddleware(BaseHTTPMiddleware):
    """Middleware to collect Prometheus metrics for HTTP requests."""

    # Paths to exclude from metrics
    EXCLUDED_PATHS = {"/metrics", "/health", "/health/live", "/health/ready"}

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        """Process request and record metrics."""
        path = request.url.path

        # Skip metrics for excluded paths
        if path in self.EXCLUDED_PATHS:
            return await call_next(request)

        method = request.method
        normalized_path = normalize_path(path)

        # Track request size
        content_length = request.headers.get("content-length")
        if content_length:
            HTTP_REQUEST_SIZE_BYTES.labels(method=method, endpoint=normalized_path).observe(
                int(content_length)
            )

        # Track in-progress requests
        HTTP_REQUESTS_IN_PROGRESS.labels(method=method, endpoint=normalized_path).inc()

        start_time = time.perf_counter()
        try:
            response = await call_next(request)
            status_code = response.status_code
        except Exception:
            status_code = 500
            raise
        finally:
            # Record duration
            duration = time.perf_counter() - start_time
            HTTP_REQUEST_DURATION_SECONDS.labels(method=method, endpoint=normalized_path).observe(
                duration
            )

            # Decrement in-progress counter
            HTTP_REQUESTS_IN_PROGRESS.labels(method=method, endpoint=normalized_path).dec()

        # Record request count
        HTTP_REQUESTS_TOTAL.labels(
            method=method, endpoint=normalized_path, status=str(status_code)
        ).inc()

        # Track response size
        response_size = response.headers.get("content-length")
        if response_size:
            HTTP_RESPONSE_SIZE_BYTES.labels(method=method, endpoint=normalized_path).observe(
                int(response_size)
            )

        return response
