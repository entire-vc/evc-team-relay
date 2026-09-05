from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.logging import get_logger
from app.db.session import get_db

logger = get_logger(__name__)

router = APIRouter(tags=["meta"])


class HealthStatus(BaseModel):
    status: str
    timestamp: datetime
    version: str


class DetailedHealthStatus(HealthStatus):
    database: str
    relay_keys: str


@router.get("/health")
def health() -> dict[str, bool]:
    """Basic health check endpoint (backwards compatible)."""
    return {"ok": True}


@router.get("/health/live", response_model=HealthStatus)
def liveness_probe() -> HealthStatus:
    """
    Kubernetes liveness probe.
    Returns 200 if the application is running.
    """
    settings = get_settings()
    return HealthStatus(
        status="healthy",
        timestamp=datetime.utcnow(),
        version=settings.api_version,
    )


@router.get("/health/ready", response_model=DetailedHealthStatus)
def readiness_probe(db: Session = Depends(get_db)) -> DetailedHealthStatus | JSONResponse:
    """
    Kubernetes readiness probe.
    Returns 200 if the application is ready to serve traffic, 503 otherwise.
    Checks database connectivity and relay keys.
    """
    settings = get_settings()

    # Check database connectivity
    try:
        db.execute(text("SELECT 1"))
        db_status = "healthy"
    except Exception as e:
        logger.warning("Database health check failed", extra={"error": str(e)})
        db_status = "unhealthy"

    # Check relay keys (basic validation)
    relay_keys_status = (
        "healthy" if settings.relay_private_key or settings.relay_key_id else "not_configured"
    )

    # Return unhealthy status if any check fails
    overall_status = "healthy" if db_status == "healthy" else "unhealthy"

    body = DetailedHealthStatus(
        status=overall_status,
        timestamp=datetime.utcnow(),
        version=settings.api_version,
        database=db_status,
        relay_keys=relay_keys_status,
    )

    # TR-17: the body already carried status="unhealthy" on DB-down, but the
    # HTTP status code stayed 200 — Docker's healthcheck (and any LB/monitor
    # that reads the status code, not the JSON body) never saw a failure, so
    # a real Postgres outage went undetected. Return the same body but with
    # a real 503 when not ready — a Response instance returned directly
    # bypasses response_model's forced-200 serialization for this case.
    if overall_status != "healthy":
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content=body.model_dump(mode="json"),
        )

    return body


# The deploy writes the commit it shipped into app/BUILD_SHA; see
# `read_build_sha()` for why it lives inside the package directory and what
# "unknown" means.
BUILD_SHA_PATH = Path(__file__).resolve().parents[2] / "BUILD_SHA"


def read_build_sha() -> str:
    """
    The commit this build was made from, or "unknown".

    ## Why a file, and why inside `app/`

    `api_version` is a constant in `config.py` ("0.1.0"). It has never moved and
    is not meant to: it describes the API contract, not the deployment. So
    nothing this service exposed could distinguish a build from an hour ago from
    one from a month ago, and a stale container answers `/health` with
    `{"ok": true}` exactly like a current one. That is the gap this closes.

    The deploy pipeline writes the cloned commit into `app/BUILD_SHA` before the
    source is rsynced to the host. It has to be INSIDE `app/` because the
    Dockerfile copies selectively -- `COPY app /app/app`, not `COPY . .`. A file
    at the control-plane root would be rsynced to the server, look correct in
    every listing, and never enter the image; the endpoint would then report
    "unknown" forever while everything appeared wired up.

    ## "unknown" is a failure, not a default

    Absent file, unreadable file, empty file -- all return "unknown", and any
    freshness check reading this endpoint MUST treat "unknown" as a failure
    rather than skipping the comparison. A checker that cannot tell "I could not
    read the version" from "the version matches" is the exact shape of gate this
    endpoint exists to make unnecessary.
    """
    try:
        sha = BUILD_SHA_PATH.read_text(encoding="utf-8").strip()
    except OSError:
        return "unknown"
    # A truncated or half-written file must not read as a real commit.
    if not re.fullmatch(r"[0-9a-f]{40}", sha):
        return "unknown"
    return sha


@router.get("/version")
def version() -> dict[str, str]:
    settings = get_settings()
    return {"version": settings.api_version, "build_sha": read_build_sha()}
