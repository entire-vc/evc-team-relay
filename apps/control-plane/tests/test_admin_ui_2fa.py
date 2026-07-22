"""Tests for TR-06 (#fceefc4f): /admin-ui/login bypassed 2FA, and TR-19
(#4ed5a3b9): /admin-ui/login had no rate limit — unbounded password
brute-force on the sole public entry point to the full-access admin panel.

authenticate_user() only checks password; login_submit used to issue the
admin_token cookie immediately after, never looking at user.totp_enabled —
a full 2FA bypass for the admin panel (delete user/share, toggle-admin).
The JSON /auth/login endpoint already gated on totp_enabled correctly; this
is the parallel fix for the server-rendered admin-ui login form.
"""

from __future__ import annotations

import pyotp
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core import security
from app.db import models


def auth_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def login_json(client: TestClient, email: str, password: str) -> str:
    response = client.post("/auth/login", json={"email": email, "password": password})
    assert response.status_code == 200, response.text
    return response.json()["access_token"]


def make_admin_user(db: Session, email: str, password: str = "test123456") -> models.User:
    user = models.User(
        email=email,
        password_hash=security.get_password_hash(password),
        is_admin=True,
        is_active=True,
        email_verified=True,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def enable_2fa_via_real_flow(client: TestClient, user_token: str) -> str:
    """Enable 2FA through the actual app endpoints (not direct DB writes),
    matching the convention in test_totp.py — returns the raw TOTP secret."""
    response = client.post("/auth/2fa/enable", headers=auth_headers(user_token))
    assert response.status_code == 200, response.text
    secret = response.json()["secret"]

    totp = pyotp.TOTP(secret)
    verify_resp = client.post(
        "/auth/2fa/verify",
        json={"code": totp.now()},
        headers=auth_headers(user_token),
    )
    assert verify_resp.status_code == 200, verify_resp.text
    return secret


ADMIN_EMAIL = "totp-admin@example.com"
ADMIN_PASSWORD = "test123456"


class TestAdminUiLoginWithout2FA:
    def test_login_without_2fa_still_issues_admin_token_directly(
        self, db_session: Session, client: TestClient
    ):
        """Regression guard: the non-2FA path must be completely unchanged."""
        make_admin_user(db_session, "plain-admin@example.com")

        response = client.post(
            "/admin-ui/login",
            data={"email": "plain-admin@example.com", "password": ADMIN_PASSWORD},
            follow_redirects=False,
        )

        assert response.status_code == 302
        assert response.headers["location"] == "/admin-ui/dashboard"
        assert "admin_token" in response.cookies
        assert "admin_2fa_pending" not in response.cookies


class TestAdminUiLoginRateLimit:
    def test_login_is_rate_limited(self, db_session: Session, client: TestClient):
        """TR-19: /admin-ui/login had no rate limiting at all — unbounded
        password brute-force against the sole public admin-panel entry
        point. Wrong-password attempts must still trip the limiter (the
        limit guards the endpoint, not just successful logins)."""
        make_admin_user(db_session, "rl-admin@example.com")

        statuses = []
        for _ in range(15):
            resp = client.post(
                "/admin-ui/login",
                data={"email": "rl-admin@example.com", "password": "wrong-password"},
                follow_redirects=False,
            )
            statuses.append(resp.status_code)

        assert 429 in statuses, f"expected a 429 among {statuses}"


class TestAdminUiLoginWith2FA:
    def _make_2fa_admin(self, db_session: Session, client: TestClient) -> tuple[models.User, str]:
        user = make_admin_user(db_session, ADMIN_EMAIL, ADMIN_PASSWORD)
        user_token = login_json(client, ADMIN_EMAIL, ADMIN_PASSWORD)
        secret = enable_2fa_via_real_flow(client, user_token)
        db_session.refresh(user)
        assert user.totp_enabled is True
        return user, secret

    def test_REGRESSION_password_alone_does_not_issue_admin_token(
        self, db_session: Session, client: TestClient
    ):
        """The exact bug: password-only POST to /admin-ui/login for a TOTP
        account must NOT grant the admin cookie."""
        self._make_2fa_admin(db_session, client)

        response = client.post(
            "/admin-ui/login",
            data={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD},
            follow_redirects=False,
        )

        assert response.status_code == 302
        assert response.headers["location"] == "/admin-ui/login/2fa"
        assert "admin_token" not in response.cookies
        assert "admin_2fa_pending" in response.cookies

    def test_REGRESSION_dashboard_is_not_reachable_after_password_step_alone(
        self, db_session: Session, client: TestClient
    ):
        """Belt-and-suspenders: even if a caller ignored the redirect and
        tried the dashboard directly with only the pending cookie, it must
        still be rejected (proves get_current_admin_from_cookie never
        accepts the pending token under any cookie name)."""
        self._make_2fa_admin(db_session, client)

        step1 = client.post(
            "/admin-ui/login",
            data={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD},
            follow_redirects=False,
        )
        pending_value = step1.cookies["admin_2fa_pending"]

        # Attacker who captured the pending cookie tries substituting it as
        # the real session cookie directly.
        response = client.get(
            "/admin-ui/dashboard",
            cookies={"admin_token": pending_value},
            follow_redirects=False,
        )
        assert response.status_code == 302
        assert response.headers["location"] == "/admin-ui/login"

    def test_2fa_page_without_pending_cookie_redirects_to_login(self, client: TestClient):
        response = client.get("/admin-ui/login/2fa", follow_redirects=False)
        assert response.status_code == 302
        assert response.headers["location"].startswith("/admin-ui/login")

    def test_full_login_flow_succeeds_with_valid_totp_code(
        self, db_session: Session, client: TestClient
    ):
        _, secret = self._make_2fa_admin(db_session, client)

        step1 = client.post(
            "/admin-ui/login",
            data={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD},
            follow_redirects=False,
        )
        pending_value = step1.cookies["admin_2fa_pending"]

        page = client.get("/admin-ui/login/2fa", cookies={"admin_2fa_pending": pending_value})
        assert page.status_code == 200

        totp = pyotp.TOTP(secret)
        step2 = client.post(
            "/admin-ui/login/2fa",
            data={"totp_code": totp.now()},
            cookies={"admin_2fa_pending": pending_value},
            follow_redirects=False,
        )

        assert step2.status_code == 302
        assert step2.headers["location"] == "/admin-ui/dashboard"
        assert "admin_token" in step2.cookies

        dashboard = client.get(
            "/admin-ui/dashboard", cookies={"admin_token": step2.cookies["admin_token"]}
        )
        assert dashboard.status_code == 200

    def test_invalid_totp_code_is_rejected(self, db_session: Session, client: TestClient):
        self._make_2fa_admin(db_session, client)

        step1 = client.post(
            "/admin-ui/login",
            data={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD},
            follow_redirects=False,
        )
        pending_value = step1.cookies["admin_2fa_pending"]

        step2 = client.post(
            "/admin-ui/login/2fa",
            data={"totp_code": "000000"},
            cookies={"admin_2fa_pending": pending_value},
            follow_redirects=False,
        )

        assert step2.status_code == 200  # re-renders the form with an error
        assert "admin_token" not in step2.cookies
        assert "Invalid code" in step2.text

    def test_backup_code_completes_login(self, db_session: Session, client: TestClient):
        user = make_admin_user(db_session, ADMIN_EMAIL, ADMIN_PASSWORD)
        user_token = login_json(client, ADMIN_EMAIL, ADMIN_PASSWORD)
        enable_resp = client.post("/auth/2fa/enable", headers=auth_headers(user_token))
        secret = enable_resp.json()["secret"]
        backup_codes = enable_resp.json()["backup_codes"]
        totp = pyotp.TOTP(secret)
        client.post("/auth/2fa/verify", json={"code": totp.now()}, headers=auth_headers(user_token))
        db_session.refresh(user)

        step1 = client.post(
            "/admin-ui/login",
            data={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD},
            follow_redirects=False,
        )
        pending_value = step1.cookies["admin_2fa_pending"]

        step2 = client.post(
            "/admin-ui/login/2fa",
            data={"totp_code": backup_codes[0]},
            cookies={"admin_2fa_pending": pending_value},
            follow_redirects=False,
        )

        assert step2.status_code == 302
        assert "admin_token" in step2.cookies

    def test_REGRESSION_pending_token_is_single_use(self, db_session: Session, client: TestClient):
        """A completed pending token must not be replayable for a second
        session — otherwise a captured (but already-used) cookie value would
        still be able to mint fresh admin sessions indefinitely."""
        _, secret = self._make_2fa_admin(db_session, client)

        step1 = client.post(
            "/admin-ui/login",
            data={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD},
            follow_redirects=False,
        )
        pending_value = step1.cookies["admin_2fa_pending"]
        totp = pyotp.TOTP(secret)

        first = client.post(
            "/admin-ui/login/2fa",
            data={"totp_code": totp.now()},
            cookies={"admin_2fa_pending": pending_value},
            follow_redirects=False,
        )
        assert first.status_code == 302
        assert "admin_token" in first.cookies

        replay = client.post(
            "/admin-ui/login/2fa",
            data={"totp_code": totp.now()},
            cookies={"admin_2fa_pending": pending_value},
            follow_redirects=False,
        )
        assert replay.status_code == 302
        assert replay.headers["location"].startswith("/admin-ui/login")
        assert "admin_token" not in replay.cookies

    def test_expired_pending_token_is_rejected(self, db_session: Session, client: TestClient):
        self._make_2fa_admin(db_session, client)

        step1 = client.post(
            "/admin-ui/login",
            data={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD},
            follow_redirects=False,
        )
        pending_value = step1.cookies["admin_2fa_pending"]

        # Force-expire the underlying record.
        from app.services.auth_service import _hash_admin_2fa_pending_token

        token_hash = _hash_admin_2fa_pending_token(pending_value)
        record = db_session.execute(
            models.AdminLoginPendingToken.__table__.select().where(
                models.AdminLoginPendingToken.token_hash == token_hash
            )
        ).first()
        assert record is not None
        db_session.execute(
            models.AdminLoginPendingToken.__table__.update()
            .where(models.AdminLoginPendingToken.token_hash == token_hash)
            .values(expires_at=security.utcnow())
        )
        db_session.commit()

        page = client.get(
            "/admin-ui/login/2fa",
            cookies={"admin_2fa_pending": pending_value},
            follow_redirects=False,
        )
        assert page.status_code == 302
        assert page.headers["location"].startswith("/admin-ui/login")

    def test_garbage_pending_cookie_is_rejected(self, client: TestClient):
        response = client.get(
            "/admin-ui/login/2fa",
            cookies={"admin_2fa_pending": "not-a-real-token"},
            follow_redirects=False,
        )
        assert response.status_code == 302
        assert response.headers["location"].startswith("/admin-ui/login")

    def test_2fa_login_is_rate_limited(self, db_session: Session, client: TestClient):
        """Brute-force guard on the 6-digit code — the new endpoint had no
        rate limiting until this fix (admin_ui.py had no limiter at all)."""
        self._make_2fa_admin(db_session, client)

        step1 = client.post(
            "/admin-ui/login",
            data={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD},
            follow_redirects=False,
        )
        pending_value = step1.cookies["admin_2fa_pending"]

        statuses = []
        for _ in range(15):
            resp = client.post(
                "/admin-ui/login/2fa",
                data={"totp_code": "000000"},
                cookies={"admin_2fa_pending": pending_value},
            )
            statuses.append(resp.status_code)

        assert 429 in statuses, f"expected a 429 among {statuses}"
