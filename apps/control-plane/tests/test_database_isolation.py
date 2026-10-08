"""Regression contracts for the shared test database transaction boundary."""

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db import models
from app.db import session as session_module


def test_test_commit_cannot_end_outer_transaction(db_connection, db_session):
    user = models.User(email="isolation@example.com", password_hash="unused")
    db_session.add(user)
    db_session.commit()
    assert db_connection.in_transaction(), "test commit escaped the rollback boundary"
    with Session(bind=db_connection, join_transaction_mode="create_savepoint") as observer:
        assert observer.execute(select(models.User).where(models.User.id == user.id)).scalar_one()


def test_session_rollback_preserves_previously_committed_setup(db_session):
    user = models.User(email="savepoint@example.com", password_hash="unused")
    db_session.add(user)
    db_session.commit()
    user_id = user.id
    db_session.add(models.User(email=user.email, password_hash="duplicate"))
    try:
        db_session.commit()
    except IntegrityError:
        db_session.rollback()
    else:
        raise AssertionError("unique email constraint stopped rejecting duplicates")
    assert db_session.get(models.User, user_id).email == "savepoint@example.com"


def test_startup_and_handler_commits_share_rollback_boundary(client, db_connection, test_user):
    with session_module.get_sessionmaker()() as startup_session:
        assert startup_session.get_bind() is db_connection
    response = client.post(
        "/v1/auth/login", json={"email": test_user.email, "password": "test123456"}
    )
    assert response.status_code == 200
    assert response.json()["refresh_token"]
    assert db_connection.in_transaction(), "HTTP commit escaped the rollback boundary"


@pytest.mark.db_commit
def test_fixture_teardown_removes_commits_without_rebuilding(engine):
    """Exercise the actual fixture twice without assuming collected test order."""
    import conftest
    from sqlalchemy import event

    statements = []

    def track_ddl(_conn, _cursor, statement, _parameters, _context, _executemany):
        if statement.lstrip().upper().startswith(("DROP ", "CREATE ")):
            statements.append(statement)

    # The enclosing test uses real-commit mode so its shared engine has no
    # outer transaction while we explicitly exercise two ordinary fixtures.
    request = type(
        "Request", (), {"node": type("Node", (), {"get_closest_marker": lambda *a: None})()}
    )()
    event.listen(engine, "before_cursor_execute", track_ddl)
    try:
        for write in (True, False):
            with pytest.MonkeyPatch.context() as patch:
                fixture = conftest.clean_database.__wrapped__(engine, request, patch)
                connection = next(fixture)
                try:
                    with session_module.get_sessionmaker()() as db:
                        assert (
                            db.query(models.User).filter_by(email="leak@example.com").count() == 0
                        )
                        if write:
                            db.add(models.User(email="leak@example.com", password_hash="unused"))
                            db.commit()
                            assert (
                                db.query(models.User).filter_by(email="leak@example.com").count()
                                == 1
                            )
                    assert connection.in_transaction()
                finally:
                    with pytest.raises(StopIteration):
                        next(fixture)
        assert statements == [], "ordinary teardown rebuilt the schema instead of rolling back"
    finally:
        event.remove(engine, "before_cursor_execute", track_ddl)


@pytest.mark.db_commit
def test_fixture_rejects_a_commit_that_ends_the_physical_transaction(engine):
    import conftest

    request = type(
        "Request", (), {"node": type("Node", (), {"get_closest_marker": lambda *a: None})()}
    )()
    with pytest.MonkeyPatch.context() as patch:
        fixture = conftest.clean_database.__wrapped__(engine, request, patch)
        connection = next(fixture)
        raw_connection = connection.connection.dbapi_connection
        assert raw_connection.in_transaction
        raw_connection.commit()
        assert connection.in_transaction(), "SQLAlchemy must still hold the stale boundary"
        assert not raw_connection.in_transaction
        with pytest.raises(AssertionError, match="outer transaction"):
            next(fixture)
