"""Tests for session management functionality."""

from __future__ import annotations

import time

from fastapi.testclient import TestClient

from app.db import models
from app.services import session_service


class TestListSessions:
    """Tests for listing active sessions."""

    def test_list_sessions_returns_active_sessions(
        self, client: TestClient, test_user: models.User
    ):
        """Test that list sessions returns all active sessions for the user."""
        # Create multiple sessions by logging in multiple times
        sessions = []
        for i in range(3):
            response = client.post(
                "/v1/auth/login",
                json={"email": test_user.email, "password": "test123456"},
                headers={"x-device-name": f"Device {i}"},
            )
            assert response.status_code == 200
            sessions.append(response.json())
            time.sleep(0.1)  # Small delay to ensure different timestamps

        # Get session list using one of the access tokens
        list_response = client.get(
            "/v1/auth/sessions",
            headers={"Authorization": f"Bearer {sessions[0]['access_token']}"},
        )

        assert list_response.status_code == 200
        session_list = list_response.json()
        assert len(session_list) == 3

        # Verify session structure
        for session_info in session_list:
            assert "id" in session_info
            assert "device_name" in session_info
            assert "user_agent" in session_info
            assert "ip_address" in session_info
            assert "last_activity" in session_info
            assert "created_at" in session_info
            assert "is_current" in session_info

        # Verify device names
        device_names = [s["device_name"] for s in session_list]
        assert "Device 0" in device_names
        assert "Device 1" in device_names
        assert "Device 2" in device_names

    def test_list_sessions_marks_current_session(self, client: TestClient, test_user: models.User):
        """Test that the current session is marked with is_current=True."""
        # Login to create a session
        login_response = client.post(
            "/v1/auth/login",
            json={"email": test_user.email, "password": "test123456"},
            headers={"x-device-name": "Current Device"},
        )
        assert login_response.status_code == 200
        access_token = login_response.json()["access_token"]

        # Create another session
        client.post(
            "/v1/auth/login",
            json={"email": test_user.email, "password": "test123456"},
            headers={"x-device-name": "Other Device"},
        )

        # Get session list using first access token
        list_response = client.get(
            "/v1/auth/sessions",
            headers={"Authorization": f"Bearer {access_token}"},
        )

        assert list_response.status_code == 200
        session_list = list_response.json()
        assert len(session_list) == 2

        # Find current session
        current_sessions = [s for s in session_list if s["is_current"]]
        assert len(current_sessions) == 1
        assert current_sessions[0]["device_name"] == "Current Device"

        # Verify other session is not current
        other_sessions = [s for s in session_list if not s["is_current"]]
        assert len(other_sessions) == 1
        assert other_sessions[0]["device_name"] == "Other Device"

    def test_list_sessions_requires_auth(self, client: TestClient):
        """Test that list sessions requires authentication."""
        response = client.get("/v1/auth/sessions")
        assert response.status_code == 401

    def test_list_sessions_empty_for_new_user(
        self, client: TestClient, test_user: models.User, db_session
    ):
        """Test that list sessions returns empty array if no sessions exist."""
        # Create a new user without logging in
        from app.core import security

        new_user = models.User(
            email="newuser@example.com",
            password_hash=security.get_password_hash("password123"),
            is_admin=False,
            is_active=True,
        )
        db_session.add(new_user)
        db_session.commit()
        db_session.refresh(new_user)

        # Login to get token
        login_response = client.post(
            "/v1/auth/login",
            json={"email": "newuser@example.com", "password": "password123"},
        )
        assert login_response.status_code == 200
        access_token = login_response.json()["access_token"]

        # List sessions should return only the current session
        list_response = client.get(
            "/v1/auth/sessions",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        assert list_response.status_code == 200
        session_list = list_response.json()
        assert len(session_list) == 1


class TestRevokeSession:
    """Tests for revoking a specific session."""

    def test_revoke_session_success(self, client: TestClient, test_user: models.User):
        """Test that a user can revoke their own session."""
        # Create two sessions
        login1 = client.post(
            "/v1/auth/login",
            json={"email": test_user.email, "password": "test123456"},
            headers={"x-device-name": "Device 1"},
        )
        login2 = client.post(
            "/v1/auth/login",
            json={"email": test_user.email, "password": "test123456"},
            headers={"x-device-name": "Device 2"},
        )
        assert login1.status_code == 200
        assert login2.status_code == 200

        access_token1 = login1.json()["access_token"]

        # Get session list
        list_response = client.get(
            "/v1/auth/sessions",
            headers={"Authorization": f"Bearer {access_token1}"},
        )
        sessions = list_response.json()
        assert len(sessions) == 2

        # Find the session to revoke (Device 2)
        session_to_revoke = next(s for s in sessions if s["device_name"] == "Device 2")
        session_id = session_to_revoke["id"]

        # Revoke the session
        revoke_response = client.delete(
            f"/v1/auth/sessions/{session_id}",
            headers={"Authorization": f"Bearer {access_token1}"},
        )
        assert revoke_response.status_code == 200
        assert revoke_response.json() == {"ok": True}

        # Verify session was removed
        list_response2 = client.get(
            "/v1/auth/sessions",
            headers={"Authorization": f"Bearer {access_token1}"},
        )
        remaining_sessions = list_response2.json()
        assert len(remaining_sessions) == 1
        assert remaining_sessions[0]["device_name"] == "Device 1"

    def test_revoke_session_makes_refresh_token_invalid(
        self, client: TestClient, test_user: models.User
    ):
        """Test that revoking a session invalidates its refresh token."""
        # Login to create session
        login_response = client.post(
            "/v1/auth/login",
            json={"email": test_user.email, "password": "test123456"},
        )
        assert login_response.status_code == 200
        tokens = login_response.json()
        access_token = tokens["access_token"]
        refresh_token = tokens["refresh_token"]

        # Get session ID
        list_response = client.get(
            "/v1/auth/sessions",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        sessions = list_response.json()
        session_id = sessions[0]["id"]

        # Revoke the session
        revoke_response = client.delete(
            f"/v1/auth/sessions/{session_id}",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        assert revoke_response.status_code == 200

        # Try to use the refresh token (should fail)
        refresh_response = client.post(
            "/v1/auth/refresh",
            json={"refresh_token": refresh_token},
        )
        assert refresh_response.status_code == 401

    def test_revoke_session_invalid_id_format(self, client: TestClient, test_user: models.User):
        """Test that invalid session ID format returns 400."""
        login_response = client.post(
            "/v1/auth/login",
            json={"email": test_user.email, "password": "test123456"},
        )
        access_token = login_response.json()["access_token"]

        response = client.delete(
            "/v1/auth/sessions/invalid-uuid",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        assert response.status_code == 400
        response_data = response.json()
        assert "error" in response_data
        assert "Invalid session ID format" in response_data["error"]["message"]

    def test_revoke_session_not_found(self, client: TestClient, test_user: models.User):
        """Test that revoking non-existent session returns 404."""
        login_response = client.post(
            "/v1/auth/login",
            json={"email": test_user.email, "password": "test123456"},
        )
        access_token = login_response.json()["access_token"]

        # Use a valid UUID that doesn't exist
        fake_session_id = "00000000-0000-0000-0000-000000000000"
        response = client.delete(
            f"/v1/auth/sessions/{fake_session_id}",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        assert response.status_code == 404
        response_data = response.json()
        assert "error" in response_data
        assert "Session not found" in response_data["error"]["message"]

    def test_revoke_session_requires_auth(self, client: TestClient):
        """Test that revoking a session requires authentication."""
        fake_session_id = "00000000-0000-0000-0000-000000000000"
        response = client.delete(f"/v1/auth/sessions/{fake_session_id}")
        assert response.status_code == 401

    def test_revoke_session_cannot_revoke_other_users_session(
        self, client: TestClient, test_user: models.User, db_session
    ):
        """Test that users cannot revoke other users' sessions."""
        # Create another user
        from app.core import security

        other_user = models.User(
            email="otheruser@example.com",
            password_hash=security.get_password_hash("password123"),
            is_admin=False,
            is_active=True,
        )
        db_session.add(other_user)
        db_session.commit()
        db_session.refresh(other_user)

        # Login as test_user
        login1 = client.post(
            "/v1/auth/login",
            json={"email": test_user.email, "password": "test123456"},
        )
        access_token1 = login1.json()["access_token"]

        # Login as other_user
        login2 = client.post(
            "/v1/auth/login",
            json={"email": "otheruser@example.com", "password": "password123"},
        )
        access_token2 = login2.json()["access_token"]

        # Get other user's session ID
        list_response = client.get(
            "/v1/auth/sessions",
            headers={"Authorization": f"Bearer {access_token2}"},
        )
        other_sessions = list_response.json()
        other_session_id = other_sessions[0]["id"]

        # Try to revoke other user's session (should fail)
        revoke_response = client.delete(
            f"/v1/auth/sessions/{other_session_id}",
            headers={"Authorization": f"Bearer {access_token1}"},
        )
        assert revoke_response.status_code == 403
        response_data = revoke_response.json()
        assert "error" in response_data
        assert "Cannot revoke another user" in response_data["error"]["message"]


class TestRevokeAllSessions:
    """Tests for revoking all sessions."""

    def test_revoke_all_sessions_success(self, client: TestClient, test_user: models.User):
        """Test that a user can revoke all their sessions."""
        # Create multiple sessions
        tokens = []
        for i in range(3):
            response = client.post(
                "/v1/auth/login",
                json={"email": test_user.email, "password": "test123456"},
                headers={"x-device-name": f"Device {i}"},
            )
            tokens.append(response.json())

        access_token = tokens[0]["access_token"]

        # Verify 3 sessions exist
        list_response = client.get(
            "/v1/auth/sessions",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        assert len(list_response.json()) == 3

        # Revoke all sessions
        revoke_response = client.delete(
            "/v1/auth/sessions",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        assert revoke_response.status_code == 200
        assert revoke_response.json()["revoked_count"] == 3

        # Verify all sessions are gone
        list_response2 = client.get(
            "/v1/auth/sessions",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        assert len(list_response2.json()) == 0

    def test_revoke_all_sessions_invalidates_refresh_tokens(
        self, client: TestClient, test_user: models.User
    ):
        """Test that revoking all sessions invalidates all refresh tokens."""
        # Create two sessions
        login1 = client.post(
            "/v1/auth/login",
            json={"email": test_user.email, "password": "test123456"},
        )
        login2 = client.post(
            "/v1/auth/login",
            json={"email": test_user.email, "password": "test123456"},
        )

        access_token1 = login1.json()["access_token"]
        refresh_token1 = login1.json()["refresh_token"]
        refresh_token2 = login2.json()["refresh_token"]

        # Revoke all sessions
        revoke_response = client.delete(
            "/v1/auth/sessions",
            headers={"Authorization": f"Bearer {access_token1}"},
        )
        assert revoke_response.status_code == 200

        # Try to use first refresh token (should fail)
        refresh_response1 = client.post(
            "/v1/auth/refresh",
            json={"refresh_token": refresh_token1},
        )
        assert refresh_response1.status_code == 401

        # Try to use second refresh token (should also fail)
        refresh_response2 = client.post(
            "/v1/auth/refresh",
            json={"refresh_token": refresh_token2},
        )
        assert refresh_response2.status_code == 401

    def test_revoke_all_sessions_requires_auth(self, client: TestClient):
        """Test that revoking all sessions requires authentication."""
        response = client.delete("/v1/auth/sessions")
        assert response.status_code == 401

    def test_revoke_all_sessions_returns_zero_if_no_sessions(
        self, client: TestClient, test_user: models.User, db_session
    ):
        """Test that revoking all sessions returns 0 if user has no sessions."""
        # Create a user and manually create a session to get token,
        # then manually delete the session
        login_response = client.post(
            "/v1/auth/login",
            json={"email": test_user.email, "password": "test123456"},
        )
        access_token = login_response.json()["access_token"]

        # Manually delete all sessions
        session_service.revoke_all_user_sessions(db_session, test_user.id)
        db_session.commit()

        # Try to revoke all (should return 0)
        # Note: The access token is still valid (JWT), but sessions are gone
        revoke_response = client.delete(
            "/v1/auth/sessions",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        assert revoke_response.status_code == 200
        assert revoke_response.json()["revoked_count"] == 0


class TestSessionAuditLogging:
    """Tests for session-related audit logging."""

    def test_session_revocation_logged(
        self, client: TestClient, test_user: models.User, db_session
    ):
        """Test that session revocation is logged to audit log."""
        # Login to create session
        login_response = client.post(
            "/v1/auth/login",
            json={"email": test_user.email, "password": "test123456"},
        )
        access_token = login_response.json()["access_token"]

        # Get session ID
        list_response = client.get(
            "/v1/auth/sessions",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        session_id = list_response.json()[0]["id"]

        # Revoke session
        client.delete(
            f"/v1/auth/sessions/{session_id}",
            headers={"Authorization": f"Bearer {access_token}"},
        )

        # Check audit log
        from sqlalchemy import select

        stmt = select(models.AuditLog).where(
            models.AuditLog.action == models.AuditAction.SESSION_REVOKED
        )
        audit_logs = list(db_session.execute(stmt).scalars().all())

        assert len(audit_logs) >= 1
        latest_log = audit_logs[-1]
        assert latest_log.actor_user_id == test_user.id
        assert latest_log.details is not None
        assert "session_id" in latest_log.details

    def test_revoke_all_sessions_logged(
        self, client: TestClient, test_user: models.User, db_session
    ):
        """Test that revoking all sessions is logged to audit log."""
        # Create sessions
        login_response = client.post(
            "/v1/auth/login",
            json={"email": test_user.email, "password": "test123456"},
        )
        access_token = login_response.json()["access_token"]

        # Revoke all sessions
        client.delete(
            "/v1/auth/sessions",
            headers={"Authorization": f"Bearer {access_token}"},
        )

        # Check audit log
        from sqlalchemy import select

        stmt = select(models.AuditLog).where(
            models.AuditLog.action == models.AuditAction.SESSION_REVOKED
        )
        audit_logs = list(db_session.execute(stmt).scalars().all())

        assert len(audit_logs) >= 1
        latest_log = audit_logs[-1]
        assert latest_log.actor_user_id == test_user.id
        assert latest_log.details is not None
        assert latest_log.details.get("all_sessions") is True
        assert "revoked_count" in latest_log.details


class TestSessionServiceFunctions:
    """Tests for session service utility functions."""

    def test_get_session_by_id(self, db_session, test_user: models.User):
        """Test getting session by ID."""
        # Create a session
        session, _ = session_service.create_session(
            db=db_session,
            user_id=test_user.id,
            device_name="Test Device",
        )
        db_session.commit()

        # Get session by ID
        retrieved = session_service.get_session_by_id(db_session, session.id)
        assert retrieved is not None
        assert retrieved.id == session.id
        assert retrieved.user_id == test_user.id
        assert retrieved.device_name == "Test Device"

    def test_get_session_by_id_not_found(self, db_session):
        """Test getting non-existent session returns None."""
        import uuid

        fake_id = uuid.uuid4()
        retrieved = session_service.get_session_by_id(db_session, fake_id)
        assert retrieved is None

    def test_revoke_all_user_sessions(self, db_session, test_user: models.User):
        """Test revoking all sessions for a user."""
        # Create multiple sessions
        for i in range(3):
            session_service.create_session(
                db=db_session,
                user_id=test_user.id,
                device_name=f"Device {i}",
            )
        db_session.commit()

        # Verify 3 sessions exist
        sessions = session_service.get_user_sessions(db_session, test_user.id)
        assert len(sessions) == 3

        # Revoke all
        count = session_service.revoke_all_user_sessions(db_session, test_user.id)
        db_session.commit()
        assert count == 3

        # Verify all gone
        sessions_after = session_service.get_user_sessions(db_session, test_user.id)
        assert len(sessions_after) == 0

    def test_revoke_all_except_session(self, db_session, test_user: models.User):
        """Test revoking all sessions except one."""
        # Create multiple sessions
        sessions = []
        for i in range(3):
            session, _ = session_service.create_session(
                db=db_session,
                user_id=test_user.id,
                device_name=f"Device {i}",
            )
            sessions.append(session)
        db_session.commit()

        # Keep the first session
        keep_session_id = sessions[0].id

        # Revoke all except the first
        count = session_service.revoke_all_user_sessions(
            db_session, test_user.id, except_session_id=keep_session_id
        )
        db_session.commit()
        assert count == 2

        # Verify only one remains
        remaining = session_service.get_user_sessions(db_session, test_user.id)
        assert len(remaining) == 1
        assert remaining[0].id == keep_session_id
