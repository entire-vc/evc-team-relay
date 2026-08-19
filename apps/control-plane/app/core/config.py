from __future__ import annotations

from functools import lru_cache

from pydantic import AnyUrl, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration loaded from environment variables."""

    project_name: str = "Relay Control Plane (Lite)"
    api_version: str = "0.1.0"
    database_url: str = Field(default="sqlite+pysqlite:///./control-plane.db")

    # Server identity (for multi-server plugin support)
    server_name: str = Field(default="Relay Server", description="Display name for this server")
    server_id: str | None = Field(
        default=None, description="Unique server ID. If not set, derived from relay_key_id"
    )

    # Debug mode for detailed error messages (set DEBUG_MODE=true in .env for development)
    debug_mode: bool = Field(default=False)

    # Logging configuration
    log_level: str = Field(default="INFO", description="Log level (DEBUG, INFO, WARNING, ERROR)")
    log_format: str = Field(default="json", description="Log format: 'json' or 'text'")

    jwt_secret: str = Field(default="dev-secret-change-me")
    jwt_algorithm: str = Field(default="HS256")
    access_token_expire_minutes: int = Field(default=60)
    refresh_token_expire_days: int = Field(default=30)

    relay_public_url: AnyUrl = Field(default="wss://relay.localhost")
    control_plane_public_url: str = Field(
        default="http://localhost:8000",
        description=(
            "Externally-reachable base URL of this control-plane API (e.g. "
            "https://cp.tr.entire.vc). Used to build the absolute base_url "
            "returned by POST /shares/{id}/file-token, since uvicorn runs "
            "without --proxy-headers and Request.base_url would otherwise "
            "reflect the internal container address, not the public one."
        ),
    )
    # TR-22: relay-token is a stateless CWT with no jti/revocation-list — remove_member
    # cannot invalidate an already-issued token, so this TTL is the entire exposure
    # window during which a removed member can still write to the CRDT doc. Keep this
    # short; relay-server enforces exp per-message (not just at connect), confirmed in
    # ghcr.io/entire-vc/evc-relay-server (crates/y-sweet-core/src/doc_connection.rs
    # DocConnection::send + crates/relay/src/server.rs handle_socket), so shrinking this
    # value directly shrinks the write-after-removal window.
    relay_token_ttl_minutes: int = Field(default=5)

    # TR-36: session.created fires (and notify_session_created queues a
    # "New login to your account" email) on every login, including a
    # reconnect-loop client hammering /auth/login — observed 10 emails to one
    # user in 2 minutes. This bounds how often that email fires per
    # (user, device) pair; the underlying UserSession row is still created
    # every time, only the notification is suppressed.
    security_new_session_email_suppression_hours: int = Field(default=24)

    # CORS settings
    cors_allowed_origins: str = Field(
        default="https://cp.tr.entire.vc",
        description=(
            "Comma-separated list of allowed CORS origins. "
            "Set to '*' only for local development (never in production)."
        ),
    )

    # Relay Ed25519 authentication
    relay_private_key: str | None = Field(
        default=None, description="Ed25519 private key in PEM format for signing relay tokens"
    )
    relay_key_id: str = Field(default="relay_cp_dev", description="Key ID for JWT kid header")

    bootstrap_admin_email: str | None = Field(default=None)
    bootstrap_admin_password: str | None = Field(default=None)

    # OAuth/OIDC settings (simple single provider via env vars)
    oauth_state_secret: str | None = Field(
        default=None,
        description=(
            "HMAC-SHA256 secret for signing OAuth state parameters. "
            "Required when oauth_enabled=True. "
            'Generate with: python -c "import secrets; print(secrets.token_hex(32))"'
        ),
    )
    oauth_enabled: bool = Field(default=False, description="Enable OAuth authentication")
    oauth_provider_name: str = Field(
        default="casdoor", description="OAuth provider name (e.g., 'casdoor', 'keycloak')"
    )
    oauth_issuer_url: str | None = Field(
        default=None, description="OAuth issuer URL (e.g., https://casdoor.example.com)"
    )
    oauth_client_id: str | None = Field(default=None, description="OAuth client ID")
    oauth_client_secret: str | None = Field(default=None, description="OAuth client secret")
    oauth_auto_register: bool = Field(
        default=True, description="Auto-create user account on first OAuth login"
    )
    oauth_scopes: str = Field(
        default="openid profile email",
        description="OAuth scopes to request (space-separated)",
    )
    oauth_admin_groups: str | None = Field(
        default=None,
        description="Comma-separated list of IAM groups that grant admin role",
    )
    oauth_default_role: str = Field(
        default="user",
        description="Default role for new OAuth users ('user' or 'admin')",
    )
    oauth_sync_user_info: bool = Field(
        default=True,
        description="Sync user name/avatar from IAM on each login",
    )

    # SMTP settings for email delivery
    smtp_host: str | None = Field(default=None, description="SMTP server hostname")
    smtp_port: int = Field(default=587, description="SMTP server port")
    smtp_user: str | None = Field(default=None, description="SMTP username")
    smtp_password: str | None = Field(default=None, description="SMTP password")
    smtp_use_tls: bool = Field(default=True, description="Use TLS for SMTP connection")
    email_from: str = Field(
        default="noreply@relay.local", description="From address for system emails"
    )
    email_reply_to: str | None = Field(
        default=None, description="Reply-to address for system emails"
    )
    email_enabled: bool = Field(
        default=False, description="Enable email sending (if False, logs to console)"
    )
    password_reset_expire_hours: int = Field(
        default=1, description="Password reset token expiration (hours)"
    )
    email_verification_expire_hours: int = Field(
        default=24, description="Email verification token expiration (hours)"
    )
    require_email_verification: bool = Field(
        default=False,
        description="Block relay token issuance for users with unverified email",
    )

    # Billing integration (enterprise edition)
    billing_enabled: bool = Field(default=False, description="Enable billing integration")
    billing_stub_mode: bool = Field(
        default=True, description="Use local stub instead of Billing Service"
    )
    billing_base_url: str = Field(default="https://billing.entire.vc/api/v1")
    billing_service_token: str = Field(default="")
    billing_webhook_secret: str = Field(default="")
    billing_grace_period_days: int = Field(default=7)
    billing_return_url: str = Field(default="", description="Return URL after checkout")

    # Web publishing settings
    web_publish_domain: str | None = Field(
        default=None,
        description="Domain for web publishing. If not set, web publishing is disabled.",
    )
    web_frame_ancestors: str | None = Field(
        default=None,
        description=(
            "Space-separated origins for CSP frame-ancestors on PRIVATE share responses. "
            "Example: 'https://mesh.entire.host https://mesh-dev.entire.host'"
        ),
    )

    @property
    def web_publish_enabled(self) -> bool:
        """Check if web publishing is enabled."""
        return bool(self.web_publish_domain)

    # Listmonk email list sync
    listmonk_url: str | None = Field(
        default=None, description="Listmonk base URL (e.g. https://lists.entire.host)"
    )
    listmonk_api_user: str = Field(default="api", description="Listmonk API username")
    listmonk_api_password: str | None = Field(default=None, description="Listmonk API password")
    listmonk_list_id: int = Field(default=8, description="Listmonk list ID for team-relay-users")
    listmonk_sync_interval: int = Field(
        default=300, description="Listmonk sync poll interval in seconds"
    )

    @property
    def listmonk_enabled(self) -> bool:
        return bool(self.listmonk_url and self.listmonk_api_password)

    # MinIO / S3 settings for asset storage
    minio_endpoint: str = Field(default="localhost:9000", description="MinIO endpoint")
    minio_access_key: str = Field(default="minioadmin", description="MinIO access key")
    minio_secret_key: str = Field(default="minioadmin", description="MinIO secret key")
    minio_secure: bool = Field(default=False, description="Use TLS for MinIO")
    minio_bucket: str = Field(default="relay-assets", description="MinIO bucket name")

    # Lifecycle email nudge engine
    lifecycle_enabled: bool = Field(
        default=False, description="Feature flag — activate after E2E smoke"
    )
    lifecycle_launch_date: str | None = Field(
        default=None,
        description="ISO datetime cutoff; only users registered on/after this date are nudged",
    )
    lifecycle_worker_interval: int = Field(default=3600, description="Poll interval seconds")
    lifecycle_from_name: str | None = Field(default=None, description="From-name (lead persona)")
    lifecycle_from_email: str | None = Field(default=None, description="From-address (lead email)")
    lifecycle_unsubscribe_secret: str = Field(
        default="change-me-in-prod",
        description="HMAC-SHA256 key for unsubscribe tokens",
    )

    # Agent key limits
    agent_key_max_per_share: int = Field(default=20, description="Max active agent keys per share")
    agent_key_creation_rate_per_hour: int = Field(
        default=10, description="Max agent key creations per user per hour"
    )
    agent_key_default_ttl_days: int = Field(
        default=90,
        description=(
            "Default expiry applied when a caller omits expires_at at key creation "
            "(TR-45) — matches the prod-DB-password rotation cadence used elsewhere."
        ),
    )
    agent_key_lenient_read_grace: bool = Field(
        default=True,
        description=(
            "Phase-1 migration switch for the unified read-scope policy (#b69d73fb, "
            "ADR-0001). While true, a write-only key is still admitted through a read "
            "gate — as it always was on /files, /assets and share metadata — and each "
            "such call is logged as a WARNING naming the key. Set false to enforce the "
            "literal policy on every read route. Withdraw only once the WARNING has "
            "been quiet for a full window and the keys it named carry 'read'."
        ),
    )
    invite_default_ttl_days: int = Field(
        default=30,
        description=(
            "Default expiry applied when a caller omits/nulls expires_in_days at "
            "invite creation (TR security-tail follow-up to TR-45) — the schema's "
            "own default of 7 only fires when the field is absent from the request "
            "body; a caller that sends an explicit null bypasses it and the invite "
            "was left with no expiry at all."
        ),
    )

    # Data retention (TR-48): email_queue/webhook_deliveries accumulate PII
    # (recipient addresses, full HTML bodies, webhook payloads) with no purge —
    # rows older than this are hard-deleted by the background retention worker.
    data_retention_days: int = Field(
        default=90,
        description="Age (days) past which email_queue/webhook_deliveries rows are purged",
    )

    # Argus CRM integration (S7: contact dedup + cross-product suppression-read)
    argus_enabled: bool = Field(default=False, description="Enable Argus CRM integration")
    argus_api_url: str = Field(default="http://argus-api:8000", description="Argus API base URL")
    argus_timeout_seconds: float = Field(default=5.0, description="Argus API request timeout")
    argus_service_key: str | None = Field(
        default=None, description="X-Argus-Service-Key for M2M auth"
    )

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return cached settings instance."""
    return Settings()
