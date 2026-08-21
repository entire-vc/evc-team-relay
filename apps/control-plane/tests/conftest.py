from __future__ import annotations

import importlib
import os
import pkgutil
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from slowapi import Limiter
from sqlalchemy.pool import StaticPool

# Ensure the project package is importable when tests run without an editable install.
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import app.api.routers as routers_pkg
import app.main as main_module
from app.core.config import get_settings
from app.core.security import generate_ed25519_keypair
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
    # RELAY_PRIVATE_KEY is required at startup (fail-closed, no ephemeral fallback) —
    # seed a throwaway keypair so the app lifespan can boot in tests.
    private_pem, _public_b64 = generate_ed25519_keypair()
    os.environ["RELAY_PRIVATE_KEY"] = private_pem

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
    with engine.begin() as conn:
        Base.metadata.drop_all(conn)
        Base.metadata.create_all(conn)
    yield


@pytest.fixture
def db_connection(engine):
    """Provide a single DBAPI connection for the test.

    All sessions (test setup + HTTP handlers) share this connection so that
    changes made by either side are immediately visible to the other — no
    cross-thread StaticPool visibility race with SQLite in-memory.

    A connection-level transaction wraps the test; everything is rolled back
    on teardown so clean_database only needs to run once per test.
    """
    with engine.connect() as connection:
        yield connection
        connection.rollback()


@pytest.fixture
def db_session(db_connection):
    """Provide a database session bound to the test's shared connection."""
    from sqlalchemy.orm import Session

    with Session(bind=db_connection, autocommit=False, autoflush=True) as session:
        yield session


def _collect_rate_limiters() -> list[Limiter]:
    """Collect every module-level slowapi Limiter under app.api.routers + app.main.

    Introspected rather than hand-enumerated: a hand-maintained list silently
    falls behind as routers are added — that is exactly how `web.limiter` and
    `webhooks.limiter` went unreset for every test (Mesh #c3acaa8d). The
    `webhooks` one is the dangerous case: its endpoint is windowed at 1 HOUR,
    so a leaked counter can never recover inside a single (~6 min) test run.

    A new router that declares its own `limiter = Limiter(...)` is picked up
    here automatically, without anyone having to remember to edit this file.
    """
    seen: dict[int, Limiter] = {}
    prefix = f"{routers_pkg.__name__}."
    for module_info in pkgutil.iter_modules(routers_pkg.__path__, prefix=prefix):
        module = importlib.import_module(module_info.name)
        for value in vars(module).values():
            if isinstance(value, Limiter):
                seen[id(value)] = value
    for value in vars(main_module).values():
        if isinstance(value, Limiter):
            seen[id(value)] = value
    return list(seen.values())


@pytest.fixture
def client(engine, db_connection):
    # Reset rate limiters before each test to avoid cross-test pollution.
    from app.db.session import get_db

    for lim in _collect_rate_limiters():
        # `_storage` is slowapi's internal handle on the backing store. It is
        # present on every Limiter today (verified: MemoryStorage, has
        # .reset()), but it is a private attribute — a slowapi version bump
        # could rename/remove it. Silently skipping a missing attribute would
        # turn this into a no-op reset that nobody notices (the exact failure
        # mode this whole fixture exists to prevent), so fail loudly instead.
        if not hasattr(lim, "_storage"):
            raise RuntimeError(
                f"slowapi Limiter {lim!r} has no `_storage` attribute — slowapi's "
                "internal API has likely changed and this test-isolation reset is "
                "silently broken. Update `_collect_rate_limiters`'s reset logic for "
                "the new slowapi internals before trusting any rate-limit test."
            )
        if lim._storage:
            lim._storage.reset()

    app = build_app()

    # Every HTTP request handler gets a session on the same connection as the
    # test's db_session, so data set up by the test is immediately visible.
    from sqlalchemy.orm import Session as _Session

    def override_get_db():
        with _Session(bind=db_connection, autocommit=False, autoflush=True) as handler_session:
            yield handler_session

    app.dependency_overrides[get_db] = override_get_db

    with TestClient(app) as test_client:
        yield test_client


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
