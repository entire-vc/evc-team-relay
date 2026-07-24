"""MinIO storage client for calculating per-user storage usage."""

from __future__ import annotations

from minio import Minio

from app.core.config import get_settings
from app.core.logging import get_logger

logger = get_logger(__name__)

# Singleton client
_client: Minio | None = None


def _get_client() -> Minio:
    """Get or create singleton MinIO client."""
    global _client
    if _client is None:
        settings = get_settings()
        _client = Minio(
            settings.minio_endpoint,
            access_key=settings.minio_access_key,
            secret_key=settings.minio_secret_key,
            secure=settings.minio_secure,
        )
    return _client


def get_objects_size(prefix: str) -> int:
    """List objects under prefix and return total size in bytes.

    Args:
        prefix: Prefix to filter objects (e.g. "share_id/")

    Returns:
        Total size of all objects under prefix in bytes
    """
    settings = get_settings()
    client = _get_client()
    total = 0

    try:
        for obj in client.list_objects(settings.minio_bucket, prefix=prefix, recursive=True):
            total += obj.size or 0
    except Exception:
        logger.exception(f"Failed to list objects under prefix {prefix}")
        raise

    return total
