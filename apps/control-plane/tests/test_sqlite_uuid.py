"""UUID storage must preserve every hex value in the SQLite test database."""

import uuid

import pytest
from sqlalchemy import select, text
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Session

from app.db.models import User


@pytest.mark.parametrize(
    "user_id",
    [
        pytest.param(uuid.UUID("12345678-1234-4123-8123-123456789012"), id="digits-only"),
        pytest.param(uuid.UUID("1e234567-1234-4123-8123-123456789012"), id="numeric-exponent"),
        pytest.param(uuid.UUID("a2345678-1234-4123-8123-123456789012"), id="letters-control"),
    ],
)
def test_uuid_round_trip_in_sqlite(db_session: Session, user_id: uuid.UUID) -> None:
    email = "uuid-storage@example.com"
    db_session.add(User(id=user_id, email=email, password_hash="unused"))
    db_session.flush()
    db_session.expire_all()

    # Force an ORM read: SQLite's numeric affinity used to convert some UUID
    # hex strings to floats, which the UUID result processor cannot decode.
    loaded = db_session.scalars(select(User).where(User.email == email)).one()
    assert loaded.id == user_id
    stored_id, storage_type = db_session.execute(
        text("SELECT id, typeof(id) FROM users WHERE email = :email"), {"email": email}
    ).one()
    assert stored_id == user_id.hex
    assert storage_type == "text"


def test_postgresql_uuid_type_stays_native() -> None:
    assert User.__table__.c.id.type.compile(dialect=postgresql.dialect()) == "UUID"
