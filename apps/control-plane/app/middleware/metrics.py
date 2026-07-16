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

# Label value for any request that never matched a route (scanner/bot probes on
# arbitrary paths). Without this, every probed path becomes its own permanent
# Prometheus time series — see task #4b0eac5c.
UNMATCHED_ENDPOINT = "__unmatched__"


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

        # Track in-progress requests. The matched route isn't known until routing
        # runs inside call_next, so this gauge is labeled by method only.
        HTTP_REQUESTS_IN_PROGRESS.labels(method=method).inc()

        start_time = time.perf_counter()
        try:
            response = await call_next(request)
            status_code = response.status_code
        except Exception:
            status_code = 500
            raise
        finally:
            # Routing has resolved by the time call_next returns — label by the
            # matched route template, or a single bucket for anything unmatched.
            route = request.scope.get("route")
            endpoint = getattr(route, "path", None) or UNMATCHED_ENDPOINT

            duration = time.perf_counter() - start_time
            HTTP_REQUEST_DURATION_SECONDS.labels(method=method, endpoint=endpoint).observe(duration)

            HTTP_REQUESTS_IN_PROGRESS.labels(method=method).dec()

        # Record request count
        HTTP_REQUESTS_TOTAL.labels(method=method, endpoint=endpoint, status=str(status_code)).inc()

        # Track request size
        content_length = request.headers.get("content-length")
        if content_length:
            HTTP_REQUEST_SIZE_BYTES.labels(method=method, endpoint=endpoint).observe(
                int(content_length)
            )

        # Track response size
        response_size = response.headers.get("content-length")
        if response_size:
            HTTP_RESPONSE_SIZE_BYTES.labels(method=method, endpoint=endpoint).observe(
                int(response_size)
            )

        return response
