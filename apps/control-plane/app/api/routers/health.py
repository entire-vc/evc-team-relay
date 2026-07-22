from __future__ import annotations

from datetime import datetime

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


@router.get("/version")
def version() -> dict[str, str]:
    settings = get_settings()
    return {"version": settings.api_version}
