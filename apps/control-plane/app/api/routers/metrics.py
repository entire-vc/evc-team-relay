"""Prometheus metrics endpoint.

DB-level gauges are refreshed every 60 s by the background metrics collector
(app/workers/metrics_worker.py).  This endpoint simply serves the cached
Prometheus registry — no DB queries on the hot path.

NOTE: This route must NOT be added to the public Caddy reverse-proxy config.
Expose only on the internal Docker network (e.g. Prometheus scrape target via
the container's internal hostname/port 8000, not through Caddy).
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import PlainTextResponse
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from slowapi import Limiter
from slowapi.util import get_remote_address

from app.core.metrics import init_app_info

# Conservative rate limit: Prometheus default scrape interval is 15–60 s,
# so 10/min is generous for scrapers but blocks any scanning.
_limiter = Limiter(key_func=get_remote_address)

router = APIRouter(tags=["metrics"])


@router.get(
    "/metrics",
    response_class=PlainTextResponse,
    summary="Prometheus metrics",
    description=(
        "Returns all metrics in Prometheus text exposition format. "
        "Accessible from internal networks only — do not expose via public reverse proxy."
    ),
    include_in_schema=False,
)
@_limiter.limit("10/minute")
def get_metrics(request: Request) -> PlainTextResponse:
    """Serve cached Prometheus metrics registry."""
    init_app_info()
    return PlainTextResponse(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)
