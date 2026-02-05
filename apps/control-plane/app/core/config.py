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
    relay_token_ttl_minutes: int = Field(default=30)

    # Relay Ed25519 authentication
    relay_private_key: str | None = Field(
        default=None, description="Ed25519 private key in PEM format for signing relay tokens"
    )
    relay_key_id: str = Field(default="relay_cp_dev", description="Key ID for JWT kid header")

    bootstrap_admin_email: str | None = Field(default=None)
    bootstrap_admin_password: str | None = Field(default=None)

    # OAuth/OIDC settings (simple single provider via env vars)
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

    # Web publishing settings
    web_publish_domain: str | None = Field(
        default=None,
        description="Domain for web publishing. If not set, web publishing is disabled.",
    )

    @property
    def web_publish_enabled(self) -> bool:
        """Check if web publishing is enabled."""
        return bool(self.web_publish_domain)

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return cached settings instance."""
    return Settings()
