"""MinIO client shared by the request paths and the usage worker.

The minio-py default HTTP client waits 5 minutes to connect and 5 minutes per
read, and retries five times. On a stalled object store that holds a request
(and whatever it is holding — a worker thread, a pool connection, a row lock)
for many minutes. Every caller here gets bounded timeouts instead, and
transport failures surface as 503/504 rather than an unhandled 500.
"""

from __future__ import annotations

import threading
from contextlib import contextmanager
from typing import Iterator

import certifi
import urllib3
from fastapi import HTTPException, status
from minio import Minio
from minio.error import ServerError
from urllib3.exceptions import (
    ConnectTimeoutError,
    MaxRetryError,
    NewConnectionError,
    ReadTimeoutError,
)

from app.core.config import get_settings
from app.core.logging import get_logger
from app.core.metrics import OBJECT_STORAGE_FAILURES_TOTAL

logger = get_logger(__name__)

_lock = threading.Lock()
_clients: dict[tuple, Minio] = {}


def get_minio_client() -> Minio:
    """MinIO client with bounded timeouts, one per storage configuration."""
    settings = get_settings()
    key = (
        settings.minio_endpoint,
        settings.minio_access_key,
        settings.minio_secret_key,
        settings.minio_secure,
        settings.minio_connect_timeout_seconds,
        settings.minio_read_timeout_seconds,
    )
    with _lock:
        client = _clients.get(key)
        if client is None:
            http = urllib3.PoolManager(
                timeout=urllib3.Timeout(
                    connect=settings.minio_connect_timeout_seconds,
                    read=settings.minio_read_timeout_seconds,
                ),
                maxsize=10,
                cert_reqs="CERT_REQUIRED",
                ca_certs=certifi.where(),
                # One quick reconnect for a refused/reset connection; never
                # re-send after a read timeout — that would double the wait.
                retries=urllib3.Retry(
                    total=1, connect=1, read=0, status=0, other=0, backoff_factor=0.2
                ),
            )
            client = Minio(
                settings.minio_endpoint,
                access_key=settings.minio_access_key,
                secret_key=settings.minio_secret_key,
                secure=settings.minio_secure,
                http_client=http,
            )
            _clients[key] = client
        return client


def _is_timeout(exc: BaseException) -> bool:
    if isinstance(exc, MaxRetryError):
        exc = exc.reason or exc
    # NewConnectionError subclasses ConnectTimeoutError, but a refused or
    # unresolvable endpoint is an outage, not a timeout.
    if isinstance(exc, NewConnectionError):
        return False
    return isinstance(exc, (ReadTimeoutError, ConnectTimeoutError, TimeoutError))


@contextmanager
def storage_errors(operation: str) -> Iterator[None]:
    """Turn object-storage transport failures into a bounded 503/504.

    S3 errors (NoSuchKey, AccessDenied, ...) pass through for the caller to
    handle: they are answers from a working store, not an outage.
    """
    try:
        yield
    except (urllib3.exceptions.HTTPError, ServerError, OSError) as exc:
        timed_out = _is_timeout(exc)
        reason = "timeout" if timed_out else "unavailable"
        OBJECT_STORAGE_FAILURES_TOTAL.labels(operation=operation, reason=reason).inc()
        logger.warning(
            "object_storage_failure",
            extra={"operation": operation, "reason": reason, "error": str(exc)},
        )
        raise HTTPException(
            status_code=(
                status.HTTP_504_GATEWAY_TIMEOUT
                if timed_out
                else status.HTTP_503_SERVICE_UNAVAILABLE
            ),
            detail=f"Object storage {'timed out' if timed_out else 'is unavailable'}",
            headers=None if timed_out else {"Retry-After": "5"},
        ) from exc


def get_objects_size(prefix: str) -> int:
    """List objects under prefix and return total size in bytes.

    Args:
        prefix: Prefix to filter objects (e.g. "share_id/")

    Returns:
        Total size of all objects under prefix in bytes
    """
    settings = get_settings()
    client = get_minio_client()
    total = 0

    try:
        for obj in client.list_objects(settings.minio_bucket, prefix=prefix, recursive=True):
            total += obj.size or 0
    except Exception:
        logger.exception(f"Failed to list objects under prefix {prefix}")
        raise

    return total
