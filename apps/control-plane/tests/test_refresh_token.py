"""Tests for refresh token functionality."""

from __future__ import annotations

import time
from datetime import timedelta

from fastapi.testclient import TestClient

from app.core.security import utcnow
from app.db import models
from app.services import session_service


class TestRefreshTokenFlow:
    """Tests for complete refresh token flow."""

    def test_login_returns_refresh_token(self, client: TestClient, test_user: models.User):
        """Test that login returns both access_token and refresh_token."""
        response = client.post(
            "/v1/auth/login",
            json={"email": test_user.email, "password": "test123456"},
        )

        assert response.status_code == 200
        data = response.json()
        assert "access_token" in data
        assert "refresh_token" in data
        assert "expires_in" in data
        assert data["token_type"] == "bearer"
        assert len(data["refresh_token"]) == 64  # 256 bits = 64 hex chars

    def test_refresh_endpoint_returns_new_tokens(self, client: TestClient, test_user: models.User):
        """Test that refresh endpoint returns new access and refresh tokens."""
        # Login to get initial tokens
        login_response = client.post(
            "/v1/auth/login",
            json={"email": test_user.email, "password": "test123456"},
        )
        assert login_response.status_code == 200
        initial_tokens = login_response.json()

        # Wait a bit to ensure timestamp difference in JWT tokens
        time.sleep(1.1)

        # Use refresh token to get new tokens
        refresh_response = client.post(
            "/v1/auth/refresh",
            json={"refresh_token": initial_tokens["refresh_token"]},
        )

        assert refresh_response.status_code == 200
        new_tokens = refresh_response.json()
        assert "access_token" in new_tokens
        assert "refresh_token" in new_tokens
        assert new_tokens["access_token"] != initial_tokens["access_token"]
        assert new_tokens["refresh_token"] != initial_tokens["refresh_token"]

    def test_refresh_token_rotation_old_token_invalid(
        self, client: TestClient, test_user: models.User
    ):
        """Test that old refresh token becomes invalid after rotation."""
        # Login to get initial tokens
        login_response = client.post(
            "/v1/auth/login",
            json={"email": test_user.email, "password": "test123456"},
        )
        initial_tokens = login_response.json()

        # Use refresh token
        refresh_response = client.post(
            "/v1/auth/refresh",
            json={"refresh_token": initial_tokens["refresh_token"]},
        )
        assert refresh_response.status_code == 200

        # Try to use old refresh token again (should fail)
        reuse_response = client.post(
            "/v1/auth/refresh",
            json={"refresh_token": initial_tokens["refresh_token"]},
        )
        assert reuse_response.status_code == 401
        response_data = reuse_response.json()
        # Error middleware wraps response in {"error": {...}}
        assert "error" in response_data
        assert "Invalid or expired" in response_data["error"]["message"]

    def test_invalid_refresh_token_rejected(self, client: TestClient):
        """Test that invalid refresh tokens are rejected."""
        response = client.post(
            "/v1/auth/refresh",
            json={"refresh_token": "0" * 64},  # Valid format but non-existent token
        )

        assert response.status_code == 401
        response_data = response.json()
        # Error middleware wraps response in {"error": {...}}
        assert "error" in response_data
        assert "Invalid or expired" in response_data["error"]["message"]

    def test_malformed_refresh_token_rejected(self, client: TestClient):
        """Test that malformed refresh tokens are rejected."""
        # Token too short
        response = client.post(
            "/v1/auth/refresh",
            json={"refresh_token": "abc123"},
        )

        assert response.status_code == 422  # Validation error

    def test_multiple_sessions_per_user(self, client: TestClient, test_user: models.User):
        """Test that a user can have multiple active sessions."""
        # Login from "device 1"
        response1 = client.post(
            "/v1/auth/login",
            json={"email": test_user.email, "password": "test123456"},
            headers={"X-Device-Name": "Device 1"},
        )
        assert response1.status_code == 200
        tokens1 = response1.json()

        # Login from "device 2"
        response2 = client.post(
            "/v1/auth/login",
            json={"email": test_user.email, "password": "test123456"},
            headers={"X-Device-Name": "Device 2"},
        )
        assert response2.status_code == 200
        tokens2 = response2.json()

        # Both tokens should be different
        assert tokens1["refresh_token"] != tokens2["refresh_token"]

        # Both should be valid
        refresh1 = client.post(
            "/v1/auth/refresh",
            json={"refresh_token": tokens1["refresh_token"]},
        )
        assert refresh1.status_code == 200

        refresh2 = client.post(
            "/v1/auth/refresh",
            json={"refresh_token": tokens2["refresh_token"]},
        )
        assert refresh2.status_code == 200


class TestLegacyRouteProxy:
    """Tests for legacy route proxying and deprecation warnings."""

    def test_legacy_login_route_works(self, client: TestClient, test_user: models.User):
        """Test that legacy /auth/login route still works."""
        response = client.post(
            "/auth/login",  # No /v1 prefix
            json={"email": test_user.email, "password": "test123456"},
        )

        assert response.status_code == 200
        assert "access_token" in response.json()

    def test_legacy_route_has_deprecation_header(self, client: TestClient, test_user: models.User):
        """Test that legacy routes include deprecation warning header."""
        response = client.post(
            "/auth/login",
            json={"email": test_user.email, "password": "test123456"},
        )

        assert response.status_code == 200
        assert "X-API-Deprecation" in response.headers
        assert "Use /v1/auth/login instead" in response.headers["X-API-Deprecation"]
        assert response.headers["X-API-Version"] == "legacy"

    def test_v1_route_no_deprecation_header(self, client: TestClient, test_user: models.User):
        """Test that /v1 routes do not have deprecation warning."""
        response = client.post(
            "/v1/auth/login",
            json={"email": test_user.email, "password": "test123456"},
        )

        assert response.status_code == 200
        assert "X-API-Deprecation" not in response.headers
        assert response.headers["X-API-Version"] == "1"


class TestSessionService:
    """Unit tests for session service functions."""

    def test_create_session(self, engine, test_user: models.User):
        """Test creating a new session."""
        from sqlalchemy.orm import Session

        with Session(engine) as db:
            session, refresh_token = session_service.create_session(
                db=db,
                user_id=test_user.id,
                device_name="Test Device",
                user_agent="Test Agent",
                ip_address="127.0.0.1",
                expires_days=30,
            )

            assert session.user_id == test_user.id
            assert session.device_name == "Test Device"
            assert session.user_agent == "Test Agent"
            assert session.ip_address == "127.0.0.1"
            assert len(refresh_token) == 64

    def test_validate_refresh_token(self, engine, test_user: models.User):
        """Test validating a refresh token."""
        from sqlalchemy.orm import Session

        with Session(engine) as db:
            _, refresh_token = session_service.create_session(
                db=db,
                user_id=test_user.id,
            )
            db.commit()

            # Valid token
            session = session_service.validate_refresh_token(db, refresh_token)
            assert session is not None
            assert session.user_id == test_user.id

            # Invalid token
            invalid_session = session_service.validate_refresh_token(db, "0" * 64)
            assert invalid_session is None

    def test_rotate_refresh_token(self, engine, test_user: models.User):
        """Test rotating a refresh token."""
        from sqlalchemy.orm import Session

        with Session(engine) as db:
            session, old_token = session_service.create_session(
                db=db,
                user_id=test_user.id,
            )
            db.commit()

            # Rotate token
            rotated_session, new_token = session_service.rotate_refresh_token(db, session.id)
            db.commit()

            assert new_token != old_token
            assert len(new_token) == 64

            # Old token should be invalid
            old_session = session_service.validate_refresh_token(db, old_token)
            assert old_session is None

            # New token should be valid
            new_session = session_service.validate_refresh_token(db, new_token)
            assert new_session is not None

    def test_revoke_session(self, engine, test_user: models.User):
        """Test revoking a session."""
        from sqlalchemy.orm import Session

        with Session(engine) as db:
            session, _ = session_service.create_session(
                db=db,
                user_id=test_user.id,
            )
            db.commit()

            # Revoke session
            result = session_service.revoke_session(db, session.id)
            db.commit()
            assert result is True

            # Session should not exist
            sessions = session_service.get_user_sessions(db, test_user.id)
            assert len(sessions) == 0

            # Revoking again should return False
            result = session_service.revoke_session(db, session.id)
            assert result is False

    def test_get_user_sessions(self, engine, test_user: models.User):
        """Test getting all user sessions."""
        from sqlalchemy.orm import Session

        with Session(engine) as db:
            # Create multiple sessions
            session_service.create_session(
                db=db,
                user_id=test_user.id,
                device_name="Device 1",
            )
            session_service.create_session(
                db=db,
                user_id=test_user.id,
                device_name="Device 2",
            )
            db.commit()

            sessions = session_service.get_user_sessions(db, test_user.id)
            assert len(sessions) == 2
            device_names = {s.device_name for s in sessions}
            assert device_names == {"Device 1", "Device 2"}

    def test_cleanup_expired_sessions(self, engine, test_user: models.User):
        """Test cleanup of expired sessions."""
        from sqlalchemy.orm import Session

        with Session(engine) as db:
            # Create active session
            session_service.create_session(
                db=db,
                user_id=test_user.id,
                expires_days=30,
            )

            # Create expired session
            expired_session, _ = session_service.create_session(
                db=db,
                user_id=test_user.id,
                expires_days=1,
            )
            expired_session.expires_at = utcnow() - timedelta(hours=1)
            db.commit()

            # Cleanup expired sessions
            count = session_service.cleanup_expired_sessions(db)
            db.commit()
            assert count == 1

            # Only active session should remain
            sessions = session_service.get_user_sessions(db, test_user.id)
            assert len(sessions) == 1
