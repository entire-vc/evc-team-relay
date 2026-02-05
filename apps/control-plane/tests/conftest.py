from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool

# Ensure the project package is importable when tests run without an editable install.
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.core.config import get_settings
from app.db import session as session_module
from app.db.models import Base
from app.main import build_app


@pytest.fixture(scope="session", autouse=True)
def test_env():
    """
    Session-scoped env setup. Can't use pytest's monkeypatch here because monkeypatch
    is function-scoped by default (ScopeMismatch). We manage os.environ manually.
    """
    old_env = os.environ.copy()

    os.environ["DATABASE_URL"] = "sqlite+pysqlite:///:memory:"
    os.environ["JWT_SECRET"] = "test-secret"
    os.environ["BOOTSTRAP_ADMIN_EMAIL"] = "bootstrap@example.com"
    os.environ["BOOTSTRAP_ADMIN_PASSWORD"] = "super-secret"
    os.environ["RELAY_PUBLIC_URL"] = "wss://relay.test"

    get_settings.cache_clear()
    yield
    os.environ.clear()
    os.environ.update(old_env)
    get_settings.cache_clear()


@pytest.fixture(scope="session")
def engine(test_env):
    engine = session_module.configure_engine(
        database_url="sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    return engine


@pytest.fixture(autouse=True)
def clean_database(engine):
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    yield


@pytest.fixture
def client(engine):
    # Reset rate limiters before each test to avoid cross-test pollution
    from app.api.routers import auth, invites, shares, tokens
    from app.main import limiter as main_limiter

    # Clear all limiter storages
    for lim in [main_limiter, auth.limiter, shares.limiter, tokens.limiter, invites.limiter]:
        if hasattr(lim, "_storage") and lim._storage:
            lim._storage.reset()

    app = build_app()
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def db_session(engine):
    """Provide a database session for tests."""
    from sqlalchemy.orm import Session

    with Session(engine) as session:
        yield session


@pytest.fixture
def test_user(db_session):
    """Create a test user for authentication tests."""
    from app.core import security
    from app.db import models

    user = models.User(
        email="testuser@example.com",
        password_hash=security.get_password_hash("test123456"),
        is_admin=False,
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    return user
