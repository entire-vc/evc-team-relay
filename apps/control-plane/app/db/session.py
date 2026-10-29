from __future__ import annotations

import time
from typing import Generator

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.exc import TimeoutError as PoolTimeoutError
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import QueuePool

from app.core.config import get_settings
from app.core.metrics import DB_POOL_TIMEOUTS_TOTAL, DB_POOL_WAIT_SECONDS

_engine: Engine | None = None
_probe_engine: Engine | None = None
_SessionLocal: sessionmaker | None = None

# Readiness gets its own single connection so a request storm that drains the
# main pool cannot also fail the container healthcheck after a 5 s pool wait.
PROBE_POOL_TIMEOUT_SECONDS = 1.0


class InstrumentedQueuePool(QueuePool):
    """QueuePool that records how long callers wait for a connection."""

    def _do_get(self):  # noqa: ANN202 - mirrors the SQLAlchemy signature
        started = time.perf_counter()
        try:
            return super()._do_get()
        except PoolTimeoutError:
            DB_POOL_TIMEOUTS_TOTAL.inc()
            raise
        finally:
            DB_POOL_WAIT_SECONDS.observe(time.perf_counter() - started)


def configure_engine(
    database_url: str | None = None,
    **engine_kwargs,
) -> Engine:
    """Initialize the global SQLAlchemy engine and session factory."""
    global _engine, _probe_engine, _SessionLocal

    settings = get_settings()
    url = database_url or settings.database_url

    connect_args = engine_kwargs.pop("connect_args", {})
    if url.startswith("sqlite"):
        connect_args.setdefault("check_same_thread", False)
    elif url.startswith("postgresql"):
        # Bound worker occupancy during exhaustion; retain the 5+10 connection
        # budget rather than increasing pressure on the database.
        engine_kwargs.setdefault("pool_timeout", settings.database_pool_timeout_seconds)
        engine_kwargs.setdefault("poolclass", InstrumentedQueuePool)

    if _probe_engine is not None:
        _probe_engine.dispose()
        _probe_engine = None
    _engine = create_engine(url, pool_pre_ping=True, connect_args=connect_args, **engine_kwargs)
    _SessionLocal = sessionmaker(bind=_engine, autocommit=False, autoflush=False)
    return _engine


def get_engine() -> Engine:
    if _engine is None:
        return configure_engine()
    return _engine


def get_probe_engine() -> Engine | None:
    """Engine for readiness checks, isolated from the request pool.

    PostgreSQL gets one dedicated connection with a short pool wait. Other
    backends (SQLite in tests) return None: callers use a regular session.
    """
    global _probe_engine
    engine = get_engine()
    if engine.dialect.name != "postgresql":
        return None
    if _probe_engine is None:
        _probe_engine = create_engine(
            engine.url,
            pool_pre_ping=True,
            pool_size=1,
            max_overflow=0,
            pool_timeout=PROBE_POOL_TIMEOUT_SECONDS,
            connect_args={"connect_timeout": 2},
        )
    return _probe_engine


def get_sessionmaker() -> sessionmaker:
    global _SessionLocal
    if _SessionLocal is None:
        configure_engine()
    assert _SessionLocal is not None
    return _SessionLocal


def get_db() -> Generator[Session, None, None]:
    session = get_sessionmaker()()
    try:
        yield session
    finally:
        session.close()


# Initialize engine on import for the main application process.
configure_engine()
