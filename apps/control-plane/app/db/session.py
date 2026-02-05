from __future__ import annotations

from typing import Generator

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings

_engine: Engine | None = None
_SessionLocal: sessionmaker | None = None


def configure_engine(
    database_url: str | None = None,
    **engine_kwargs,
) -> Engine:
    """Initialize the global SQLAlchemy engine and session factory."""
    global _engine, _SessionLocal

    settings = get_settings()
    url = database_url or settings.database_url

    connect_args = engine_kwargs.pop("connect_args", {})
    if url.startswith("sqlite"):
        connect_args.setdefault("check_same_thread", False)

    _engine = create_engine(url, pool_pre_ping=True, connect_args=connect_args, **engine_kwargs)
    _SessionLocal = sessionmaker(bind=_engine, autocommit=False, autoflush=False)
    return _engine


def get_engine() -> Engine:
    if _engine is None:
        return configure_engine()
    return _engine


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
