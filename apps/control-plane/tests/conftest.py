from __future__ import annotations

import importlib
import os
import pkgutil
import random
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from slowapi import Limiter
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from sqlalchemy.sql.compiler import TypeCompiler

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


@compiles(UUID, "sqlite")
def compile_sqlite_uuid(_type: UUID, _compiler: TypeCompiler, **_kwargs: object) -> str:
    # SQLite treats UUID as numeric affinity and can turn valid hex values into
    # floats. Keep UUID storage textual in tests; PostgreSQL still uses UUID.
    return "CHAR(36)"


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


def pytest_addoption(parser):
    parser.addoption(
        "--shuffle-seed", type=int, default=None, help="Reproducible test collection shuffle"
    )


def pytest_configure(config):
    config.addinivalue_line(
        "markers", "db_commit: requires real commits or independent database connections"
    )
    workers = config.getoption("numprocesses", default=None)
    if workers not in (None, 0, 1, 2):
        raise pytest.UsageError("DB isolation budget allows at most 2 explicit workers; no auto")


def pytest_collection_modifyitems(config, items):
    seed = config.getoption("shuffle_seed")
    if seed is not None:
        random.Random(seed).shuffle(items)


def pytest_report_header(config):
    seed = config.getoption("shuffle_seed")
    if seed is not None:
        return f"test collection shuffle seed: {seed}; DB worker budget: 2"


@pytest.fixture(scope="session")
def engine(test_env):
    engine = session_module.configure_engine(
        database_url="sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    with engine.begin() as connection:
        Base.metadata.create_all(connection)
    yield engine
    engine.dispose()


@pytest.fixture(autouse=True)
def clean_database(engine, request, monkeypatch):
    if request.node.get_closest_marker("db_commit"):
        # Opt out when the test observes actual commits/connection availability.
        # Reset both sides: a committed test must not pollute the next rollback
        # test, irrespective of collected/shuffled order.
        with engine.begin() as connection:
            Base.metadata.drop_all(connection)
            Base.metadata.create_all(connection)
        yield None
        with engine.begin() as connection:
            Base.metadata.drop_all(connection)
            Base.metadata.create_all(connection)
        return

    with engine.connect() as connection:
        transaction = connection.begin()
        # sqlite3 legacy mode does not BEGIN for SAVEPOINT. Without this
        # physical BEGIN, releasing the first savepoint commits outside the
        # outer SQLAlchemy transaction. Keep legacy commit behavior for the
        # db_commit opt-out above; only rollback tests need this envelope.
        connection.exec_driver_sql("BEGIN")
        # Startup and workers use get_sessionmaker rather than HTTP dependency
        # overrides. Their commits must join this same rollback boundary too.
        monkeypatch.setattr(
            session_module,
            "_SessionLocal",
            sessionmaker(
                bind=connection,
                autoflush=False,
                join_transaction_mode="create_savepoint",
            ),
        )
        try:
            yield connection
        finally:
            try:
                if (
                    not transaction.is_active
                    or not connection.connection.dbapi_connection.in_transaction
                ):
                    raise AssertionError(
                        "test ended the outer transaction; use db_commit semantics"
                    )
            finally:
                transaction.rollback()


@pytest.fixture
def db_connection(engine, clean_database):
    """Share setup, handlers and startup inside the test rollback boundary."""
    if clean_database is not None:
        yield clean_database
    else:
        with engine.connect() as connection:
            yield connection
            connection.rollback()


@pytest.fixture
def db_session(db_connection):
    """Session commits release a savepoint; teardown rolls back the outer test."""
    from sqlalchemy.orm import Session

    with Session(
        bind=db_connection,
        autocommit=False,
        autoflush=True,
        join_transaction_mode="create_savepoint",
    ) as session:
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
        with _Session(
            bind=db_connection,
            autocommit=False,
            autoflush=True,
            join_transaction_mode="create_savepoint",
        ) as handler_session:
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
