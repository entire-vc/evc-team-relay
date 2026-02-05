from __future__ import annotations

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address
from sqlalchemy.exc import DataError, IntegrityError, OperationalError, SQLAlchemyError
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api.routers import (
    admin,
    admin_ui,
    auth,
    dashboard,
    health,
    invites,
    keys,
    metrics,
    oauth,
    server,
    shares,
    tokens,
    users,
    web,
    webhooks,
)
from app.api.routers.admin_ui import AdminAuthRedirect
from app.core import security
from app.core.config import get_settings
from app.core.logging import configure_logging, get_logger
from app.db.session import get_sessionmaker
from app.middleware import errors, logging
from app.middleware.metrics import PrometheusMiddleware
from app.services import auth_service

# Rate limiter configuration
limiter = Limiter(key_func=get_remote_address)


tags_metadata = [
    {"name": "meta", "description": "Health and metadata endpoints"},
    {"name": "auth", "description": "Authentication and session APIs"},
    {"name": "oauth", "description": "OAuth/OIDC authentication"},
    {"name": "users", "description": "User search and lookup"},
    {"name": "admin", "description": "Administrative endpoints"},
    {"name": "dashboard", "description": "Dashboard statistics and insights"},
    {"name": "shares", "description": "Share metadata management"},
    {"name": "invites", "description": "Share invite link management and redemption"},
    {"name": "tokens", "description": "Token issuance for relay access"},
    {"name": "keys", "description": "Public key distribution for JWT verification"},
    {"name": "webhooks", "description": "Webhook management for event notifications"},
    {"name": "web", "description": "Web publishing public API"},
]


def build_app() -> FastAPI:
    settings = get_settings()

    # Configure structured logging
    configure_logging(log_level=settings.log_level, log_format=settings.log_format)
    logger = get_logger(__name__)
    logger.info(
        "Starting Control Plane",
        extra={"version": settings.api_version, "log_format": settings.log_format},
    )

    app = FastAPI(
        title=settings.project_name,
        version=settings.api_version,
        openapi_tags=tags_metadata,
        description="""
## Relay Control Plane API

Self-hosted control plane for managing document collaboration and sharing via Relay.

### Features

* **Authentication** - JWT-based authentication with login/logout
* **User Management** - Admin endpoints for user CRUD operations
* **Share Management** - Create and manage document/folder shares with visibility controls
* **Access Control** - Role-based permissions (owner, editor, viewer)
* **Token Issuance** - Generate relay tokens for secure document access
* **Audit Logging** - Comprehensive activity tracking
* **Dashboard** - Statistics and insights for admins and users
* **Rate Limiting** - Protection against abuse (10 login attempts/min, 30 tokens/min)
* **Health Checks** - Kubernetes-compatible liveness/readiness probes

### Authentication

Most endpoints require authentication via JWT token in the `Authorization` header:

```
Authorization: Bearer <your-jwt-token>
```

Get a token by calling `POST /auth/login` with valid credentials.
        """,
        contact={
            "name": "Relay Control Plane",
            "url": "https://github.com/entire-vc/relay-onprem",
        },
        license_info={
            "name": "Private",
        },
    )

    # Add rate limiter state
    app.state.limiter = limiter

    # Add middlewares (order matters - first added = outermost)
    app.add_middleware(PrometheusMiddleware)  # Metrics collection
    app.add_middleware(logging.RequestLoggingMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Register exception handlers (order matters - most specific first)
    # Admin UI auth redirect (must be before generic handlers)
    async def admin_auth_redirect_handler(request, exc):
        return RedirectResponse("/admin-ui/login", status_code=302)

    app.add_exception_handler(AdminAuthRedirect, admin_auth_redirect_handler)
    app.add_exception_handler(StarletteHTTPException, errors.http_exception_handler)
    app.add_exception_handler(RequestValidationError, errors.validation_exception_handler)
    # Specific database errors before generic SQLAlchemyError
    app.add_exception_handler(IntegrityError, errors.integrity_error_handler)
    app.add_exception_handler(DataError, errors.data_error_handler)
    app.add_exception_handler(OperationalError, errors.operational_error_handler)
    app.add_exception_handler(SQLAlchemyError, errors.database_exception_handler)
    app.add_exception_handler(Exception, errors.general_exception_handler)
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

    # Mount static files for admin UI
    app.mount("/static", StaticFiles(directory="app/static"), name="static")

    # Prometheus metrics endpoint (no auth, no versioning)
    app.include_router(metrics.router)

    # Register v1 API routers
    app.include_router(health.router, prefix="/v1")
    app.include_router(server.router, prefix="/v1")
    app.include_router(auth.router, prefix="/v1")
    app.include_router(oauth.router, prefix="/v1")  # OAuth/OIDC authentication
    app.include_router(users.router, prefix="/v1")
    app.include_router(admin.router, prefix="/v1")
    app.include_router(admin_ui.router, prefix="/v1")  # Admin web UI
    app.include_router(dashboard.router, prefix="/v1")
    app.include_router(shares.router, prefix="/v1")
    app.include_router(invites.router, prefix="/v1")  # Share invite management (authenticated)
    app.include_router(invites.public_router, prefix="/v1")  # Public invite redemption
    app.include_router(tokens.router, prefix="/v1")
    app.include_router(keys.router, prefix="/v1")
    app.include_router(web.router)  # Web publishing public API (prefix included)
    app.include_router(webhooks.router)  # Webhooks API (prefix included)
    app.include_router(webhooks.admin_router)  # Admin webhooks API (prefix included)

    # Legacy routes (backward compatibility) - proxy to /v1 with deprecation warning
    app.include_router(health.router, deprecated=True)
    app.include_router(server.router, deprecated=True)
    app.include_router(auth.router, deprecated=True)
    app.include_router(oauth.router, deprecated=True)  # OAuth also available without /v1
    app.include_router(users.router, deprecated=True)
    app.include_router(admin.router, deprecated=True)
    app.include_router(admin_ui.router, deprecated=True)
    app.include_router(dashboard.router, deprecated=True)
    app.include_router(shares.router, deprecated=True)
    app.include_router(invites.router, deprecated=True)
    app.include_router(invites.public_router, deprecated=True)
    app.include_router(tokens.router, deprecated=True)
    app.include_router(keys.router, deprecated=True)

    @app.on_event("startup")
    def _bootstrap_admin() -> None:
        session_maker = get_sessionmaker()
        session = session_maker()
        try:
            auth_service.bootstrap_admin_if_needed(session)
        finally:
            session.close()

    @app.on_event("startup")
    def _validate_oauth_config() -> None:
        """Validate OAuth configuration on startup."""
        settings = get_settings()
        if settings.oauth_enabled:
            errors = []
            if not settings.oauth_issuer_url:
                errors.append("OAUTH_ISSUER_URL is required when OAuth is enabled")
            if not settings.oauth_client_id:
                errors.append("OAUTH_CLIENT_ID is required when OAuth is enabled")
            if not settings.oauth_client_secret:
                errors.append("OAUTH_CLIENT_SECRET is required when OAuth is enabled")
            if settings.oauth_default_role not in ("user", "admin"):
                errors.append(
                    f"OAUTH_DEFAULT_ROLE must be 'user' or 'admin', "
                    f"got '{settings.oauth_default_role}'"
                )
            if errors:
                error_msg = "OAuth configuration errors:\n  - " + "\n  - ".join(errors)
                logger.error(error_msg)
                raise ValueError(error_msg)
            logger.info(
                "OAuth enabled: provider=%s, issuer=%s, scopes=%s, auto_register=%s, "
                "admin_groups=%s",
                settings.oauth_provider_name,
                settings.oauth_issuer_url,
                settings.oauth_scopes,
                settings.oauth_auto_register,
                settings.oauth_admin_groups,
            )

    @app.on_event("startup")
    def _bootstrap_relay_keys() -> None:
        """Load or generate Ed25519 keypair for relay token signing."""
        settings = get_settings()
        private_key, public_key, key_id = security.load_or_generate_relay_keypair(settings)
        # Store in app state for access during requests
        app.state.relay_private_key = private_key
        app.state.relay_public_key = public_key
        app.state.relay_key_id = key_id

    return app


app = build_app()
