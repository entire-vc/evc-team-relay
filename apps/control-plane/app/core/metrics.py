"""Prometheus metrics definitions for Control Plane."""

from __future__ import annotations

from prometheus_client import Counter, Gauge, Histogram, Info

# =============================================================================
# Application Info
# =============================================================================

APP_INFO = Info("control_plane", "Control Plane application information")

# =============================================================================
# HTTP Metrics
# =============================================================================

HTTP_REQUESTS_TOTAL = Counter(
    "http_requests_total",
    "Total number of HTTP requests",
    ["method", "endpoint", "status"],
)

HTTP_REQUEST_DURATION_SECONDS = Histogram(
    "http_request_duration_seconds",
    "HTTP request duration in seconds",
    ["method", "endpoint"],
    buckets=(0.005, 0.01, 0.025, 0.05, 0.075, 0.1, 0.25, 0.5, 0.75, 1.0, 2.5, 5.0, 7.5, 10.0),
)

HTTP_REQUESTS_IN_PROGRESS = Gauge(
    "http_requests_in_progress",
    "Number of HTTP requests currently being processed",
    ["method", "endpoint"],
)

HTTP_REQUEST_SIZE_BYTES = Histogram(
    "http_request_size_bytes",
    "HTTP request size in bytes",
    ["method", "endpoint"],
    buckets=(100, 500, 1000, 5000, 10000, 50000, 100000, 500000, 1000000),
)

HTTP_RESPONSE_SIZE_BYTES = Histogram(
    "http_response_size_bytes",
    "HTTP response size in bytes",
    ["method", "endpoint"],
    buckets=(100, 500, 1000, 5000, 10000, 50000, 100000, 500000, 1000000),
)

# =============================================================================
# Database Metrics
# =============================================================================

DB_CONNECTIONS_ACTIVE = Gauge(
    "db_connections_active",
    "Number of active database connections",
)

DB_CONNECTIONS_IDLE = Gauge(
    "db_connections_idle",
    "Number of idle database connections",
)

DB_CONNECTIONS_TOTAL = Gauge(
    "db_connections_total",
    "Total number of database connections in pool",
)

DB_QUERY_DURATION_SECONDS = Histogram(
    "db_query_duration_seconds",
    "Database query duration in seconds",
    ["operation"],
    buckets=(0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0),
)

DB_HEALTH_STATUS = Gauge(
    "db_health_status",
    "Database health status (1 = healthy, 0 = unhealthy)",
)

# =============================================================================
# Business Metrics
# =============================================================================

USERS_TOTAL = Gauge(
    "users_total",
    "Total number of users",
    ["status"],  # active, inactive
)

USERS_ACTIVE_30D = Gauge(
    "users_active_30d",
    "Number of users active in last 30 days",
)

SHARES_TOTAL = Gauge(
    "shares_total",
    "Total number of shares",
    ["kind", "visibility"],  # kind: doc/folder, visibility: private/public/protected
)

SHARE_MEMBERS_TOTAL = Gauge(
    "share_members_total",
    "Total number of share memberships",
    ["role"],  # viewer, editor
)

RELAY_TOKENS_ISSUED_TOTAL = Counter(
    "relay_tokens_issued_total",
    "Total number of relay tokens issued",
    ["mode"],  # read, write
)

LOGIN_ATTEMPTS_TOTAL = Counter(
    "login_attempts_total",
    "Total number of login attempts",
    ["status", "method"],  # status: success/failure, method: password/oauth/2fa
)

OAUTH_LOGINS_TOTAL = Counter(
    "oauth_logins_total",
    "Total number of OAuth logins",
    ["provider", "status"],  # provider: casdoor/etc, status: success/failure
)

INVITES_TOTAL = Counter(
    "invites_total",
    "Total number of invite operations",
    ["action"],  # created, redeemed, revoked
)

AUDIT_EVENTS_TOTAL = Counter(
    "audit_events_total",
    "Total number of audit log events",
    ["action"],
)

# =============================================================================
# 2FA Metrics
# =============================================================================

TOTP_OPERATIONS_TOTAL = Counter(
    "totp_operations_total",
    "Total number of 2FA/TOTP operations",
    ["operation", "status"],  # operation: enable/disable/verify, status: success/failure
)

# =============================================================================
# Session Metrics
# =============================================================================

SESSIONS_ACTIVE = Gauge(
    "sessions_active",
    "Number of active user sessions",
)

SESSION_OPERATIONS_TOTAL = Counter(
    "session_operations_total",
    "Total number of session operations",
    ["operation"],  # created, revoked, refreshed, expired
)

# =============================================================================
# Backup Metrics (updated by backup service)
# =============================================================================

BACKUP_LAST_SUCCESS_TIMESTAMP = Gauge(
    "backup_last_success_timestamp",
    "Unix timestamp of last successful backup",
)

BACKUP_LAST_DURATION_SECONDS = Gauge(
    "backup_last_duration_seconds",
    "Duration of last backup in seconds",
)

BACKUP_SIZE_BYTES = Gauge(
    "backup_size_bytes",
    "Size of last backup in bytes",
)

BACKUP_SUCCESS_TOTAL = Counter(
    "backup_success_total",
    "Total number of successful backups",
)

BACKUP_FAILURE_TOTAL = Counter(
    "backup_failure_total",
    "Total number of failed backups",
)


def init_app_info(version: str = "0.1.0") -> None:
    """Initialize application info metric."""
    APP_INFO.info({"version": version, "service": "control-plane"})
