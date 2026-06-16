"""Background metrics collector — runs every 60 s as an in-process asyncio task.

Queries the database and MinIO to update Prometheus gauges without blocking
the request path.  Started via app startup hook in app/main.py.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

from minio import Minio
from minio.error import S3Error
from sqlalchemy import case, func, select, text

from app.core.config import get_settings
from app.core.logging import get_logger
from app.core.metrics import (
    AGENT_KEYS_TOTAL,
    DB_CONNECTIONS_ACTIVE,
    DB_CONNECTIONS_IDLE,
    DB_CONNECTIONS_TOTAL,
    DB_HEALTH_STATUS,
    METRICS_COLLECTION_ERRORS_TOTAL,
    MINIO_BUCKET_SIZE_BYTES,
    POSTGRES_SIZE_BYTES,
    SESSIONS_ACTIVE_TOTAL,
    SHARE_FILES_TOTAL,
    SHARE_MEMBERS_TOTAL,
    SHARE_SIZE_BYTES,
    SHARES_TOTAL,
    USERS_ACTIVE_30D,
    USERS_TOTAL,
)
from app.db import models
from app.db.session import get_engine, get_sessionmaker

logger = get_logger(__name__)

COLLECT_INTERVAL_SECONDS = 60


def _init_gauge_labels() -> None:
    """Initialize gauge label combinations to zero so they appear in /metrics output
    before the first collection cycle completes."""
    for status_val in ("active", "inactive"):
        for role_val in ("admin", "user"):
            for twofa_val in ("true", "false"):
                USERS_TOTAL.labels(status=status_val, role=role_val, twofa_enabled=twofa_val).set(0)
    USERS_ACTIVE_30D.set(0)
    for kind_val in ("doc", "folder"):
        for vis_val in ("private", "public", "protected"):
            SHARES_TOTAL.labels(kind=kind_val, visibility=vis_val).set(0)
    for role_val in ("viewer", "editor"):
        SHARE_MEMBERS_TOTAL.labels(role=role_val).set(0)
    # SHARE_FILES_TOTAL and SHARE_SIZE_BYTES use dynamic share_id labels — no pre-init
    for s in ("active", "revoked", "expired"):
        AGENT_KEYS_TOTAL.labels(status=s).set(0)
    SESSIONS_ACTIVE_TOTAL.set(0)
    DB_HEALTH_STATUS.set(0)
    DB_CONNECTIONS_ACTIVE.set(0)
    DB_CONNECTIONS_IDLE.set(0)
    DB_CONNECTIONS_TOTAL.set(0)


_init_gauge_labels()


def _collect_db_metrics() -> None:
    """Run all DB aggregate queries and update gauges. Uses a fresh session."""
    db = get_sessionmaker()()
    try:
        _do_collect_db(db)
        DB_HEALTH_STATUS.set(1)
    except Exception as exc:
        DB_HEALTH_STATUS.set(0)
        METRICS_COLLECTION_ERRORS_TOTAL.labels(collector="db").inc()
        logger.warning("metrics_worker: db collection error", extra={"error": str(exc)})
    finally:
        db.close()


def _do_collect_db(db) -> None:  # noqa: ANN001
    now = datetime.now(timezone.utc)
    thirty_days_ago = now - timedelta(days=30)

    # ── db_connections (SQLAlchemy pool stats) ────────────────────────────────
    # Read in its own try/except so a pool introspection failure neither aborts
    # the rest of DB collection nor flips db_health_status to 0.
    try:
        pool = get_engine().pool
        active = pool.checkedout()
        idle = pool.checkedin()
        DB_CONNECTIONS_ACTIVE.set(active)
        DB_CONNECTIONS_IDLE.set(idle)
        DB_CONNECTIONS_TOTAL.set(active + idle)
    except Exception as exc:
        METRICS_COLLECTION_ERRORS_TOTAL.labels(collector="pool").inc()
        logger.warning("metrics_worker: pool stat error", extra={"error": str(exc)})

    # ── users_total (status × role × twofa_enabled) ──────────────────────────
    user_rows = db.execute(
        select(
            models.User.is_active,
            models.User.is_admin,
            models.User.totp_enabled,
            func.count(models.User.id),
        ).group_by(models.User.is_active, models.User.is_admin, models.User.totp_enabled)
    ).all()
    # Reset all label combinations before setting to handle edge cases
    for status_val in ("active", "inactive"):
        for role_val in ("admin", "user"):
            for twofa_val in ("true", "false"):
                USERS_TOTAL.labels(status=status_val, role=role_val, twofa_enabled=twofa_val).set(0)
    for is_active, is_admin, totp_enabled, cnt in user_rows:
        USERS_TOTAL.labels(
            status="active" if is_active else "inactive",
            role="admin" if is_admin else "user",
            twofa_enabled="true" if totp_enabled else "false",
        ).set(cnt)

    # ── users_active_30d ──────────────────────────────────────────────────────
    active_30d = (
        db.execute(
            select(func.count(func.distinct(models.UserSession.user_id))).where(
                models.UserSession.last_activity >= thirty_days_ago
            )
        ).scalar()
        or 0
    )
    USERS_ACTIVE_30D.set(active_30d)

    # ── shares_total (kind × visibility) ─────────────────────────────────────
    share_rows = db.execute(
        select(
            models.Share.kind,
            models.Share.visibility,
            func.count(models.Share.id),
        ).group_by(models.Share.kind, models.Share.visibility)
    ).all()
    for kind_val in ["doc", "folder"]:
        for vis_val in ["private", "public", "protected"]:
            SHARES_TOTAL.labels(kind=kind_val, visibility=vis_val).set(0)
    for kind, visibility, cnt in share_rows:
        SHARES_TOTAL.labels(kind=kind.value, visibility=visibility.value).set(cnt)

    # ── share_members_total (role) ────────────────────────────────────────────
    member_rows = db.execute(
        select(models.ShareMember.role, func.count(models.ShareMember.id)).group_by(
            models.ShareMember.role
        )
    ).all()
    for role_val in ["viewer", "editor"]:
        SHARE_MEMBERS_TOTAL.labels(role=role_val).set(0)
    for role, cnt in member_rows:
        SHARE_MEMBERS_TOTAL.labels(role=role.value).set(cnt)

    # ── share_files_total{share_id}: per-share folder item counts, top-20 ─────
    # Uses jsonb_array_length; falls back gracefully for SQLite (no jsonb support).
    # Clears stale share_id labels each cycle before setting new values.
    SHARE_FILES_TOTAL.clear()
    try:
        share_file_rows = db.execute(
            select(
                models.Share.id,
                func.jsonb_array_length(models.Share.web_folder_items).label("cnt"),
            )
            .where(
                models.Share.kind == models.ShareKind.FOLDER,
                models.Share.web_folder_items.is_not(None),
            )
            .order_by(func.jsonb_array_length(models.Share.web_folder_items).desc())
            .limit(20)
        ).all()
        for share_id, cnt in share_file_rows:
            SHARE_FILES_TOTAL.labels(share_id=str(share_id)).set(cnt or 0)
    except Exception:
        pass  # SQLite / no jsonb support — metric stays cleared

    # ── share_size_bytes{share_id}: per-share doc content size, top-20 ────────
    # Clears stale share_id labels each cycle before setting new values.
    SHARE_SIZE_BYTES.clear()
    share_size_rows = db.execute(
        select(
            models.Share.id,
            func.length(models.Share.web_content).label("sz"),
        )
        .where(
            models.Share.kind == models.ShareKind.DOC,
            models.Share.web_content.is_not(None),
        )
        .order_by(func.length(models.Share.web_content).desc())
        .limit(20)
    ).all()
    for share_id, sz in share_size_rows:
        SHARE_SIZE_BYTES.labels(share_id=str(share_id)).set(sz or 0)

    # ── agent_keys_total (status: active / revoked / expired) ────────────────
    status_expr = case(
        (models.ShareAgentKey.revoked_at.is_not(None), "revoked"),
        (
            (models.ShareAgentKey.expires_at.is_not(None))
            & (models.ShareAgentKey.expires_at < now),
            "expired",
        ),
        else_="active",
    ).label("status")
    key_rows = db.execute(
        select(status_expr, func.count(models.ShareAgentKey.id)).group_by(status_expr)
    ).all()
    for s in ["active", "revoked", "expired"]:
        AGENT_KEYS_TOTAL.labels(status=s).set(0)
    for status, cnt in key_rows:
        AGENT_KEYS_TOTAL.labels(status=status).set(cnt)

    # ── sessions_active_total ─────────────────────────────────────────────────
    sessions_cnt = (
        db.execute(
            select(func.count(models.UserSession.id)).where(models.UserSession.expires_at > now)
        ).scalar()
        or 0
    )
    SESSIONS_ACTIVE_TOTAL.set(sessions_cnt)

    # ── postgres_size_bytes ───────────────────────────────────────────────────
    try:
        pg_size = db.execute(text("SELECT pg_database_size(current_database())")).scalar() or 0
        POSTGRES_SIZE_BYTES.set(pg_size)
    except Exception:
        pass  # SQLite or unavailable — skip without error


def _collect_minio_metrics() -> None:
    """Query MinIO for bucket size and update the gauge."""
    settings = get_settings()
    try:
        client = Minio(
            settings.minio_endpoint,
            access_key=settings.minio_access_key,
            secret_key=settings.minio_secret_key,
            secure=settings.minio_secure,
        )
        total_bytes = 0
        for obj in client.list_objects(settings.minio_bucket, recursive=True):
            total_bytes += obj.size or 0
        MINIO_BUCKET_SIZE_BYTES.labels(bucket=settings.minio_bucket).set(total_bytes)
    except S3Error as exc:
        METRICS_COLLECTION_ERRORS_TOTAL.labels(collector="minio").inc()
        logger.warning("metrics_worker: minio s3 error", extra={"error": str(exc)})
    except Exception as exc:
        METRICS_COLLECTION_ERRORS_TOTAL.labels(collector="minio").inc()
        logger.warning("metrics_worker: minio collection error", extra={"error": str(exc)})


async def run_metrics_collector() -> None:
    """Async loop started at app startup.  Collects metrics every 60 s.

    Sleeps before the first collection so that startup DB operations (bootstrap
    admin, test fixtures) complete before we open a competing session on the
    shared SQLAlchemy pool connection.  Gauge labels are pre-initialised via
    _init_gauge_labels() so they appear in /metrics immediately.
    """
    logger.info(
        "metrics_worker: background collector started", extra={"interval": COLLECT_INTERVAL_SECONDS}
    )
    while True:
        await asyncio.sleep(COLLECT_INTERVAL_SECONDS)
        try:
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, _collect_db_metrics)
            await asyncio.wait_for(
                loop.run_in_executor(None, _collect_minio_metrics),
                timeout=30.0,
            )
        except asyncio.TimeoutError:
            METRICS_COLLECTION_ERRORS_TOTAL.labels(collector="minio").inc()
            logger.warning("metrics_worker: minio timed out")
        except Exception as exc:
            logger.error("metrics_worker: unexpected error", extra={"error": str(exc)})
