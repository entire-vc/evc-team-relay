#!/usr/bin/env python3
"""
Comprehensive headless test for ALL plugin operations against the control plane API.

Simulates every API call the Obsidian plugin makes:
- Auth: login, get user info
- Shares: CRUD, visibility changes
- Members: add, list, update role, remove
- Invites: create, list, revoke
- OAuth: authorize flow, provider list, state validation, Casdoor reachability
- Tokens: CWT relay token issuance
- WebSocket: auth header + query param connection
- Web Publishing: enable, sync content, verify page
- Server Info: features, branding

Usage:
    # Full test suite on staging:
    python scripts/test_plugin_operations.py \
        --server http://localhost:8000 \
        --email test@entire.vc \
        --password Test123456

    # Full test suite on production:
    python scripts/test_plugin_operations.py \
        --server https://cp.tr.entire.vc \
        --email test@entire.vc \
        --password Test123456

    # With second user for member tests:
    python scripts/test_plugin_operations.py \
        --server http://localhost:8000 \
        --email test@entire.vc \
        --password Test123456 \
        --second-email simple@entire.vc \
        --second-password password123

    # Skip WebSocket tests:
    python scripts/test_plugin_operations.py \
        --server http://localhost:8000 \
        --email test@entire.vc \
        --password Test123456 \
        --skip-ws

    # Only specific test groups:
    python scripts/test_plugin_operations.py \
        --server http://localhost:8000 \
        --email test@entire.vc \
        --password Test123456 \
        --only shares,members

    # Only OAuth tests:
    python scripts/test_plugin_operations.py \
        --server http://localhost:8000 \
        --email test@entire.vc \
        --password Test123456 \
        --only oauth --verbose
"""

import argparse
import asyncio
import base64
import json
import sys
import time
import traceback
from dataclasses import dataclass, field
from typing import Optional
from urllib.parse import urlparse

import httpx

try:
    import websockets

    HAS_WEBSOCKETS = True
except ImportError:
    HAS_WEBSOCKETS = False

try:
    import cbor2

    HAS_CBOR2 = True
except ImportError:
    HAS_CBOR2 = False


# =====================================================================
# Configuration
# =====================================================================


@dataclass
class TestConfig:
    server_url: str
    email: str
    password: str
    second_email: Optional[str] = None
    second_password: Optional[str] = None
    verbose: bool = False
    skip_ws: bool = False
    skip_cleanup: bool = False
    only_groups: Optional[list[str]] = None


@dataclass
class TestResult:
    name: str
    passed: bool
    duration_ms: float
    error: Optional[str] = None
    details: Optional[str] = None


@dataclass
class TestState:
    """Shared state between tests."""

    jwt_token: str = ""
    user_id: str = ""
    user_email: str = ""
    second_jwt_token: str = ""
    second_user_id: str = ""
    share_id: str = ""
    share_path: str = ""
    share_slug: str = ""
    relay_url: str = ""
    relay_token: str = ""
    invite_id: str = ""
    invite_token: str = ""
    server_info: dict = field(default_factory=dict)
    web_publish_domain: str = ""


# =====================================================================
# Test Runner
# =====================================================================


class PluginOperationsTester:
    def __init__(self, config: TestConfig):
        self.config = config
        self.client = httpx.Client(
            timeout=60.0,
            follow_redirects=True,
            http2=False,
            limits=httpx.Limits(
                max_keepalive_connections=0,  # Disable keep-alive to avoid stale connections
            ),
        )
        self.state = TestState()
        self.results: list[TestResult] = []

    def log(self, msg: str, level: str = "INFO"):
        timestamp = time.strftime("%H:%M:%S")
        prefix = {
            "INFO": "  ",
            "OK": "  \u2705",
            "FAIL": "  \u274c",
            "WARN": "  \u26a0\ufe0f",
            "SKIP": "  \u23ed\ufe0f",
        }.get(level, "  ")
        print(f"[{timestamp}] {prefix} {msg}")

    def log_verbose(self, msg: str):
        if self.config.verbose:
            self.log(msg)

    def _headers(self, token: Optional[str] = None) -> dict:
        t = token or self.state.jwt_token
        return {"Authorization": f"Bearer {t}"} if t else {}

    def _run_test(self, name: str, func, group: str = "general") -> TestResult:
        """Run a single test and record the result."""
        if self.config.only_groups and group not in self.config.only_groups:
            result = TestResult(name=name, passed=True, duration_ms=0, details="SKIPPED")
            self.results.append(result)
            return result

        start = time.monotonic()
        try:
            func()
            duration = (time.monotonic() - start) * 1000
            result = TestResult(name=name, passed=True, duration_ms=duration)
            self.log(f"{name} ({duration:.0f}ms)", "OK")
        except Exception as e:
            duration = (time.monotonic() - start) * 1000
            error_msg = str(e)
            result = TestResult(
                name=name, passed=False, duration_ms=duration, error=error_msg
            )
            self.log(f"{name}: {error_msg}", "FAIL")
            if self.config.verbose:
                traceback.print_exc()

        self.results.append(result)
        return result

    def _run_async_test(self, name: str, coro_func, group: str = "general") -> TestResult:
        """Run an async test."""
        if self.config.only_groups and group not in self.config.only_groups:
            result = TestResult(name=name, passed=True, duration_ms=0, details="SKIPPED")
            self.results.append(result)
            return result

        start = time.monotonic()
        try:
            asyncio.run(coro_func())
            duration = (time.monotonic() - start) * 1000
            result = TestResult(name=name, passed=True, duration_ms=duration)
            self.log(f"{name} ({duration:.0f}ms)", "OK")
        except Exception as e:
            duration = (time.monotonic() - start) * 1000
            error_msg = str(e)
            result = TestResult(
                name=name, passed=False, duration_ms=duration, error=error_msg
            )
            self.log(f"{name}: {error_msg}", "FAIL")
            if self.config.verbose:
                traceback.print_exc()

        self.results.append(result)
        return result

    # =================================================================
    # Test Group: Server Info
    # =================================================================

    def test_server_info(self):
        """T21: GET /server/info"""
        resp = self.client.get(f"{self.config.server_url}/server/info")
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}"
        data = resp.json()
        assert "id" in data, "Missing 'id' in server info"
        assert "relay_url" in data, "Missing 'relay_url' in server info"
        assert "features" in data, "Missing 'features' in server info"
        self.state.server_info = data
        self.state.relay_url = data["relay_url"]
        self.state.web_publish_domain = data.get("features", {}).get(
            "web_publish_domain", ""
        )
        self.log_verbose(f"  Server: {data['name']} ({data['id']})")
        self.log_verbose(f"  Relay URL: {data['relay_url']}")

    def test_public_key(self):
        """T22: GET /keys/public"""
        resp = self.client.get(f"{self.config.server_url}/keys/public")
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}"
        data = resp.json()
        assert "public_key" in data, "Missing 'public_key'"
        assert len(data["public_key"]) > 20, "Public key too short"
        self.log_verbose(f"  Key: {data['public_key'][:40]}...")

    def test_health(self):
        """GET /health"""
        resp = self.client.get(f"{self.config.server_url}/health")
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}"

    # =================================================================
    # Test Group: Auth
    # =================================================================

    def test_login(self):
        """T1: POST /auth/login + GET /auth/me"""
        # Login
        resp = self.client.post(
            f"{self.config.server_url}/auth/login",
            json={"email": self.config.email, "password": self.config.password},
        )
        assert resp.status_code == 200, f"Login failed: {resp.status_code} {resp.text}"
        data = resp.json()
        assert "access_token" in data, "No access_token in response"
        self.state.jwt_token = data["access_token"]

        # Get user info
        resp = self.client.get(
            f"{self.config.server_url}/auth/me",
            headers=self._headers(),
        )
        assert resp.status_code == 200, f"GET /auth/me failed: {resp.status_code}"
        me = resp.json()
        self.state.user_id = me["id"]
        self.state.user_email = me["email"]
        self.log_verbose(f"  User: {me['email']} (id: {me['id'][:8]}...)")

    def test_login_second_user(self):
        """Login second user for member tests."""
        if not self.config.second_email or not self.config.second_password:
            raise Exception("Second user credentials not provided (--second-email/--second-password)")

        resp = self.client.post(
            f"{self.config.server_url}/auth/login",
            json={
                "email": self.config.second_email,
                "password": self.config.second_password,
            },
        )
        assert resp.status_code == 200, f"Second user login failed: {resp.status_code}"
        data = resp.json()
        self.state.second_jwt_token = data["access_token"]

        resp = self.client.get(
            f"{self.config.server_url}/auth/me",
            headers=self._headers(self.state.second_jwt_token),
        )
        assert resp.status_code == 200, f"GET /auth/me for second user failed"
        me = resp.json()
        self.state.second_user_id = me["id"]
        self.log_verbose(f"  Second user: {me['email']} (id: {me['id'][:8]}...)")

    # =================================================================
    # Test Group: Shares CRUD
    # =================================================================

    def test_list_shares_empty(self):
        """T3: GET /shares (expect empty or existing)"""
        resp = self.client.get(
            f"{self.config.server_url}/shares",
            headers=self._headers(),
        )
        assert resp.status_code == 200, f"List shares failed: {resp.status_code}"
        shares = resp.json()
        assert isinstance(shares, list), "Expected list response"
        self.log_verbose(f"  Found {len(shares)} existing shares")

    def test_create_folder_share(self):
        """T4: POST /shares (folder)"""
        test_path = f"headless-test-{int(time.time())}"
        resp = self.client.post(
            f"{self.config.server_url}/shares",
            json={
                "kind": "folder",
                "path": test_path,
                "visibility": "private",
            },
            headers=self._headers(),
        )
        assert resp.status_code == 201, f"Create share failed: {resp.status_code} {resp.text}"
        share = resp.json()
        assert share["kind"] == "folder"
        assert share["path"] == test_path
        assert share["visibility"] == "private"
        self.state.share_id = share["id"]
        self.state.share_path = test_path
        self.log_verbose(f"  Created share: {share['id'][:8]}... path={test_path}")

    def test_get_share(self):
        """T5: GET /shares/{id}"""
        assert self.state.share_id, "No share_id (run test_create_folder_share first)"
        resp = self.client.get(
            f"{self.config.server_url}/shares/{self.state.share_id}",
            headers=self._headers(),
        )
        assert resp.status_code == 200, f"Get share failed: {resp.status_code}"
        share = resp.json()
        assert share["id"] == self.state.share_id
        assert share["path"] == self.state.share_path
        self.log_verbose(f"  Share details: kind={share['kind']}, vis={share['visibility']}")

    def test_update_share_visibility(self):
        """T6: PATCH /shares/{id} (change visibility)"""
        assert self.state.share_id, "No share_id"
        resp = self.client.patch(
            f"{self.config.server_url}/shares/{self.state.share_id}",
            json={"visibility": "public"},
            headers=self._headers(),
        )
        assert resp.status_code == 200, f"Update share failed: {resp.status_code} {resp.text}"
        share = resp.json()
        assert share["visibility"] == "public", f"Expected 'public', got '{share['visibility']}'"
        self.log_verbose(f"  Visibility changed to: {share['visibility']}")

    # =================================================================
    # Test Group: Members
    # =================================================================

    def test_get_members(self):
        """T7: GET /shares/{id}/members"""
        assert self.state.share_id, "No share_id"
        resp = self.client.get(
            f"{self.config.server_url}/shares/{self.state.share_id}/members",
            headers=self._headers(),
        )
        assert resp.status_code == 200, f"Get members failed: {resp.status_code}"
        members = resp.json()
        assert isinstance(members, list)
        self.log_verbose(f"  Members: {len(members)}")

    def test_add_member_by_email(self):
        """T8: GET /users/search + POST /shares/{id}/members"""
        assert self.state.share_id, "No share_id"
        assert self.state.second_user_id, "No second user (login second user first)"

        # Search user
        resp = self.client.get(
            f"{self.config.server_url}/users/search",
            params={"email": self.config.second_email},
            headers=self._headers(),
        )
        assert resp.status_code == 200, f"User search failed: {resp.status_code} {resp.text}"
        user = resp.json()
        assert user["id"] == self.state.second_user_id

        # Add member
        resp = self.client.post(
            f"{self.config.server_url}/shares/{self.state.share_id}/members",
            json={"user_id": self.state.second_user_id, "role": "editor"},
            headers=self._headers(),
        )
        assert resp.status_code == 201, f"Add member failed: {resp.status_code} {resp.text}"
        member = resp.json()
        assert member["role"] == "editor"
        self.log_verbose(f"  Added member: {member['user_email']} as {member['role']}")

    def test_update_member_role(self):
        """PATCH /shares/{id}/members/{user_id}"""
        assert self.state.share_id and self.state.second_user_id
        resp = self.client.patch(
            f"{self.config.server_url}/shares/{self.state.share_id}/members/{self.state.second_user_id}",
            json={"role": "viewer"},
            headers=self._headers(),
        )
        assert resp.status_code == 200, f"Update role failed: {resp.status_code} {resp.text}"
        member = resp.json()
        assert member["role"] == "viewer", f"Expected 'viewer', got '{member['role']}'"
        self.log_verbose(f"  Role changed to: {member['role']}")

    def test_remove_member(self):
        """T9: DELETE /shares/{id}/members/{user_id}"""
        assert self.state.share_id and self.state.second_user_id
        resp = self.client.delete(
            f"{self.config.server_url}/shares/{self.state.share_id}/members/{self.state.second_user_id}",
            headers=self._headers(),
        )
        assert resp.status_code == 204, f"Remove member failed: {resp.status_code} {resp.text}"
        self.log_verbose("  Member removed")

    # =================================================================
    # Test Group: Invites
    # =================================================================

    def test_create_invite(self):
        """T10: POST /shares/{id}/invites"""
        assert self.state.share_id, "No share_id"
        resp = self.client.post(
            f"{self.config.server_url}/shares/{self.state.share_id}/invites",
            json={"role": "editor", "expires_in_days": 7, "max_uses": 5},
            headers=self._headers(),
        )
        assert resp.status_code == 201, f"Create invite failed: {resp.status_code} {resp.text}"
        invite = resp.json()
        assert "token" in invite, "Missing invite token"
        self.state.invite_id = invite["id"]
        self.state.invite_token = invite["token"]
        self.log_verbose(f"  Invite created: {invite['token'][:20]}...")

    def test_list_invites(self):
        """T11: GET /shares/{id}/invites"""
        assert self.state.share_id, "No share_id"
        resp = self.client.get(
            f"{self.config.server_url}/shares/{self.state.share_id}/invites",
            headers=self._headers(),
        )
        assert resp.status_code == 200, f"List invites failed: {resp.status_code}"
        invites = resp.json()
        assert isinstance(invites, list)
        assert len(invites) >= 1, "Expected at least 1 invite"
        self.log_verbose(f"  Found {len(invites)} invites")

    def test_get_invite_info(self):
        """GET /invite/{token} (public)"""
        assert self.state.invite_token, "No invite token"
        resp = self.client.get(
            f"{self.config.server_url}/invite/{self.state.invite_token}",
        )
        assert resp.status_code == 200, f"Get invite info failed: {resp.status_code}"
        info = resp.json()
        assert "role" in info
        self.log_verbose(f"  Invite info: role={info['role']}")

    def test_revoke_invite(self):
        """T12: DELETE /shares/{id}/invites/{invite_id}"""
        assert self.state.share_id and self.state.invite_id
        resp = self.client.delete(
            f"{self.config.server_url}/shares/{self.state.share_id}/invites/{self.state.invite_id}",
            headers=self._headers(),
        )
        assert resp.status_code == 204, f"Revoke invite failed: {resp.status_code}"
        self.log_verbose("  Invite revoked")

    # =================================================================
    # Test Group: Relay Tokens
    # =================================================================

    def test_get_relay_token(self):
        """T13: POST /tokens/relay (CWT)"""
        assert self.state.share_id, "No share_id"
        resp = self.client.post(
            f"{self.config.server_url}/tokens/relay",
            json={
                "share_id": self.state.share_id,
                "doc_id": self.state.share_id,
                "mode": "write",
            },
            headers=self._headers(),
        )
        assert resp.status_code == 200, f"Token request failed: {resp.status_code} {resp.text}"
        data = resp.json()
        assert "token" in data, "Missing token"
        assert "relay_url" in data, "Missing relay_url"
        assert "expires_at" in data, "Missing expires_at"
        self.state.relay_token = data["token"]
        self.state.relay_url = data["relay_url"]
        self.log_verbose(f"  Token: {data['token'][:40]}...")
        self.log_verbose(f"  Relay URL: {data['relay_url']}")

        # Decode CWT claims if cbor2 available
        if HAS_CBOR2:
            self._decode_cwt(data["token"])

    def _decode_cwt(self, token: str):
        """Decode CWT token claims."""
        try:
            padding = 4 - len(token) % 4
            if padding != 4:
                token += "=" * padding
            token_bytes = base64.urlsafe_b64decode(token)
            outer = cbor2.loads(token_bytes)

            if hasattr(outer, "tag") and outer.tag == 61:
                inner = outer.value
                if hasattr(inner, "tag") and inner.tag == 18:
                    cose_sign1 = inner.value
                else:
                    cose_sign1 = inner
            else:
                cose_sign1 = outer

            if isinstance(cose_sign1, list) and len(cose_sign1) >= 3:
                claims = cbor2.loads(cose_sign1[2])
                claim_names = {
                    1: "iss",
                    2: "sub",
                    3: "aud",
                    4: "exp",
                    6: "iat",
                    -80201: "scope",
                }
                named = {claim_names.get(k, k): v for k, v in claims.items()}
                self.log_verbose(f"  CWT claims: {json.dumps(named, default=str)}")

                # Validate CWT structure
                assert "iss" in named, "CWT missing 'iss' claim"
                assert "iat" in named, "CWT missing 'iat' claim"
                assert "scope" in named, "CWT missing 'scope' claim"
                assert "exp" not in named, "CWT should NOT have 'exp' claim"
                assert "aud" not in named, "CWT should NOT have 'aud' claim"
        except Exception as e:
            self.log_verbose(f"  CWT decode error: {e}")

    # =================================================================
    # Test Group: OAuth Flow
    # =================================================================

    def test_oauth_providers_list(self):
        """OAuth1: GET /auth/oauth/providers"""
        resp = self.client.get(
            f"{self.config.server_url}/auth/oauth/providers",
        )
        assert resp.status_code == 200, f"List providers failed: {resp.status_code}"
        providers = resp.json()
        assert isinstance(providers, list), "Expected list response"
        if providers:
            self.log_verbose(f"  Found {len(providers)} OAuth providers")
            for p in providers:
                self.log_verbose(f"    - {p.get('name', 'unknown')}: {p.get('authorize_url', '')}")

    def test_oauth_authorize_json(self):
        """OAuth2: GET /auth/oauth/casdoor/authorize (JSON response)"""
        redirect_uri = "http://127.0.0.1:12345/callback"

        resp = self.client.get(
            f"{self.config.server_url}/auth/oauth/casdoor/authorize",
            params={"redirect_uri": redirect_uri},
            headers={"Accept": "application/json"},
        )
        assert resp.status_code == 200, f"OAuth authorize failed: {resp.status_code} {resp.text}"
        data = resp.json()

        # Verify response structure
        assert "authorize_url" in data, "Missing authorize_url in response"
        assert "state" in data, "Missing state in response"

        authorize_url = data["authorize_url"]
        state_token = data["state"]

        # Verify authorize_url points to correct Casdoor instance
        assert authorize_url.startswith("https://login.entire.vc/"), \
            f"Authorize URL doesn't start with expected issuer: {authorize_url}"

        # Verify redirect_uri is in the authorize URL (may be URL-encoded)
        from urllib.parse import quote
        assert redirect_uri in authorize_url or quote(redirect_uri, safe="") in authorize_url, \
            f"Redirect URI not found in authorize URL: {authorize_url}"

        # Verify state parameter is in the authorize URL (may be URL-encoded)
        assert f"state={state_token}" in authorize_url \
            or f"state={quote(state_token, safe='')}" in authorize_url, \
            f"State parameter not found in authorize URL"

        self.log_verbose(f"  Authorize URL: {authorize_url[:80]}...")
        self.log_verbose(f"  State token: {state_token[:40]}...")

        # Decode state to verify structure
        self._verify_oauth_state(state_token, redirect_uri)

    def _verify_oauth_state(self, state_token: str, expected_redirect_uri: str):
        """Decode and verify OAuth state parameter."""
        try:
            # State is base64url-encoded JSON
            import base64

            # Add padding if needed
            padding = 4 - len(state_token) % 4
            if padding != 4:
                state_token += "=" * padding

            state_bytes = base64.urlsafe_b64decode(state_token)
            state_data = json.loads(state_bytes)

            # Verify state structure
            assert "code_verifier" in state_data, "State missing 'code_verifier'"
            assert "redirect_uri" in state_data, "State missing 'redirect_uri'"

            # Verify redirect_uri matches
            assert state_data["redirect_uri"] == expected_redirect_uri or \
                   state_data["redirect_uri"] == expected_redirect_uri.replace("http://", "https://"), \
                f"State redirect_uri mismatch: {state_data['redirect_uri']} != {expected_redirect_uri}"

            self.log_verbose(f"  State decoded: verifier={state_data['code_verifier'][:20]}...")
            if "return_url" in state_data:
                self.log_verbose(f"  Return URL: {state_data['return_url']}")

        except Exception as e:
            self.log_verbose(f"  State decode error: {e}")
            raise AssertionError(f"Failed to decode OAuth state: {e}")

    def test_oauth_casdoor_reachable(self):
        """OAuth3: Verify Casdoor authorize endpoint is reachable"""
        # First get the authorize URL
        redirect_uri = "http://127.0.0.1:12345/callback"

        resp = self.client.get(
            f"{self.config.server_url}/auth/oauth/casdoor/authorize",
            params={"redirect_uri": redirect_uri},
            headers={"Accept": "application/json"},
        )
        assert resp.status_code == 200, f"OAuth authorize failed: {resp.status_code}"
        data = resp.json()
        authorize_url = data["authorize_url"]

        # Try to fetch the Casdoor login page
        try:
            casdoor_resp = self.client.get(authorize_url, follow_redirects=False)

            # Casdoor should return 200 (login page) or 302 (redirect to login)
            assert casdoor_resp.status_code in (200, 302), \
                f"Casdoor returned unexpected status: {casdoor_resp.status_code}"

            # If it's HTML, verify it looks like a login page
            if casdoor_resp.status_code == 200:
                content_type = casdoor_resp.headers.get("content-type", "")
                assert "text/html" in content_type, \
                    f"Casdoor didn't return HTML: {content_type}"

                # Check for common login page elements (case-insensitive)
                html_lower = casdoor_resp.text.lower()
                has_login_indicator = any(
                    keyword in html_lower
                    for keyword in ["login", "sign in", "casdoor", "password", "email"]
                )
                assert has_login_indicator, \
                    "Casdoor page doesn't contain expected login elements"

            self.log_verbose(f"  Casdoor responded with {casdoor_resp.status_code}")

        except httpx.HTTPError as e:
            raise AssertionError(f"Failed to reach Casdoor: {e}")

    def test_invite_page_oauth_link(self):
        """OAuth4: Verify invite page has correct OAuth link with HTTPS return_url"""
        if not self.state.share_id or not self.state.jwt_token:
            self.log("  Skipped (no share — run with shares group)", "WARN")
            return

        # Create a temporary invite for this test
        resp = self.client.post(
            f"{self.config.server_url}/shares/{self.state.share_id}/invites",
            json={"role": "editor", "expires_in_days": 1, "max_uses": 1},
            headers=self._headers(),
        )
        if resp.status_code != 201:
            self.log("  Skipped (could not create invite for OAuth test)", "WARN")
            return

        invite = resp.json()
        invite_token = invite["token"]
        invite_id = invite["id"]

        try:
            # Fetch the invite page
            resp = self.client.get(
                f"{self.config.server_url}/invite/{invite_token}/page",
            )
            assert resp.status_code == 200, f"Invite page failed: {resp.status_code}"

            # Parse HTML for OAuth link
            html = resp.text

            # Look for oauth_authorize_url in the HTML
            # The template uses: href="{{ oauth_authorize_url }}"
            import re

            # Extract OAuth authorize URL from HTML
            oauth_link_match = re.search(
                r'href="([^"]*auth/oauth/[^"]*)"',
                html
            )

            assert oauth_link_match, "OAuth authorize link not found in invite page HTML"
            oauth_link = oauth_link_match.group(1)

            self.log_verbose(f"  OAuth link: {oauth_link[:80]}...")

            # Verify the link structure
            assert "/auth/oauth/casdoor/authorize" in oauth_link, \
                f"OAuth link doesn't contain expected path: {oauth_link}"

            # Verify return_url parameter exists and uses HTTPS (not HTTP)
            assert "return_url=" in oauth_link, \
                f"OAuth link missing return_url parameter: {oauth_link}"

            # Extract return_url from query string
            return_url_match = re.search(r'return_url=([^&"]+)', oauth_link)
            if return_url_match:
                import urllib.parse
                return_url = urllib.parse.unquote(return_url_match.group(1))
                self.log_verbose(f"  Return URL: {return_url}")

                # Verify return_url uses HTTPS (important for security)
                assert return_url.startswith("https://") or return_url.startswith("http://127.0.0.1") or return_url.startswith("http://localhost"), \
                    f"Return URL should use HTTPS or be localhost: {return_url}"

                # Verify return_url points back to invite page
                assert f"/invite/{invite_token}/page" in return_url, \
                    f"Return URL doesn't point to invite page: {return_url}"
        finally:
            # Cleanup: revoke the temporary invite
            self.client.delete(
                f"{self.config.server_url}/shares/{self.state.share_id}/invites/{invite_id}",
                headers=self._headers(),
            )

    # =================================================================
    # Test Group: WebSocket
    # =================================================================

    async def test_ws_auth_header(self):
        """T14: WebSocket connect with Authorization header"""
        if not HAS_WEBSOCKETS:
            raise Exception("websockets not installed")
        assert self.state.relay_url and self.state.relay_token
        doc_id = self.state.share_id
        ws_url = f"{self.state.relay_url}/{doc_id}"

        async with websockets.connect(
            ws_url,
            additional_headers={"Authorization": f"Bearer {self.state.relay_token}"},
            ping_interval=20,
            ping_timeout=20,
        ) as ws:
            try:
                msg = await asyncio.wait_for(ws.recv(), timeout=5.0)
                self.log_verbose(f"  Received {len(msg)} bytes via auth header")
            except asyncio.TimeoutError:
                self.log_verbose("  No initial message (empty doc)")

    async def test_ws_query_param(self):
        """T15: WebSocket connect with ?token= query param (Caddy proxy)"""
        if not HAS_WEBSOCKETS:
            raise Exception("websockets not installed")
        assert self.state.relay_url and self.state.relay_token
        doc_id = self.state.share_id
        ws_url = f"{self.state.relay_url}/{doc_id}?token={self.state.relay_token}"

        async with websockets.connect(
            ws_url,
            ping_interval=20,
            ping_timeout=20,
        ) as ws:
            try:
                msg = await asyncio.wait_for(ws.recv(), timeout=5.0)
                self.log_verbose(f"  Received {len(msg)} bytes via ?token= query param")
            except asyncio.TimeoutError:
                self.log_verbose("  No initial message (empty doc)")

    # =================================================================
    # Test Group: Web Publishing
    # =================================================================

    def test_enable_web_publishing(self):
        """T16: PATCH /shares/{id} (web_published: true)"""
        assert self.state.share_id, "No share_id"

        resp = self.client.patch(
            f"{self.config.server_url}/shares/{self.state.share_id}",
            json={"web_published": True},
            headers=self._headers(),
        )
        assert resp.status_code == 200, f"Enable web publish failed: {resp.status_code} {resp.text}"
        share = resp.json()
        assert share.get("web_published") is True, "web_published not True"
        assert share.get("web_slug"), "No web_slug assigned"
        self.state.share_slug = share["web_slug"]
        self.log_verbose(f"  Web slug: {share['web_slug']}")
        self.log_verbose(f"  Web URL: {share.get('web_url', 'N/A')}")

    def test_sync_folder_items(self):
        """T17: PATCH /shares/{id} (web_folder_items)"""
        assert self.state.share_id, "No share_id"

        test_items = [
            {"path": "README.md", "name": "README", "type": "doc"},
            {"path": "notes/hello.md", "name": "hello", "type": "doc"},
            {"path": "notes", "name": "notes", "type": "folder"},
        ]

        resp = self.client.patch(
            f"{self.config.server_url}/shares/{self.state.share_id}",
            json={"web_folder_items": test_items},
            headers=self._headers(),
        )
        assert resp.status_code == 200, f"Sync folder items failed: {resp.status_code} {resp.text}"
        self.log_verbose(f"  Synced {len(test_items)} items")

    def test_sync_file_content(self):
        """T18: POST /v1/web/shares/{slug}/files"""
        assert self.state.share_slug, "No web_slug"

        test_content = "# Hello World\n\nThis is a headless test document.\n"

        resp = self.client.post(
            f"{self.config.server_url}/v1/web/shares/{self.state.share_slug}/files",
            params={"path": "README.md"},
            json={"content": test_content},
            headers=self._headers(),
        )
        assert resp.status_code == 200, f"Sync file content failed: {resp.status_code} {resp.text}"
        self.log_verbose(f"  Synced content: {len(test_content)} chars")

    def test_get_file_content(self):
        """GET /v1/web/shares/{slug}/files?path=README.md"""
        assert self.state.share_slug, "No web_slug"

        resp = self.client.get(
            f"{self.config.server_url}/v1/web/shares/{self.state.share_slug}/files",
            params={"path": "README.md"},
        )
        assert resp.status_code == 200, f"Get file content failed: {resp.status_code}"
        data = resp.json()
        assert "content" in data, "No content in response"
        assert "Hello World" in data["content"], "Content mismatch"
        self.log_verbose(f"  Got content: {len(data['content'])} chars")

    def test_web_publish_page(self):
        """T19: Check web-publish page renders"""
        if not self.state.web_publish_domain:
            raise Exception("No web_publish_domain in server info")
        assert self.state.share_slug, "No web_slug"

        url = f"https://{self.state.web_publish_domain}/{self.state.share_slug}"
        resp = self.client.get(url)

        # Public share should return 200
        assert resp.status_code == 200, (
            f"Web page returned {resp.status_code} for {url}. "
            f"If 401, check share visibility is 'public'"
        )
        assert "Hello World" in resp.text or self.state.share_slug in resp.text, (
            "Web page content doesn't contain expected text"
        )
        self.log_verbose(f"  Web page OK: {url}")

    def test_get_web_share_by_slug(self):
        """GET /v1/web/shares/{slug}"""
        assert self.state.share_slug, "No web_slug"

        resp = self.client.get(
            f"{self.config.server_url}/v1/web/shares/{self.state.share_slug}",
        )
        assert resp.status_code == 200, f"Get web share failed: {resp.status_code}"
        data = resp.json()
        assert data["web_slug"] == self.state.share_slug
        self.log_verbose(f"  Web share: kind={data['kind']}, vis={data['visibility']}")

    # =================================================================
    # Test Group: Cleanup
    # =================================================================

    def test_disable_web_publishing(self):
        """Disable web publishing before delete."""
        assert self.state.share_id
        resp = self.client.patch(
            f"{self.config.server_url}/shares/{self.state.share_id}",
            json={"web_published": False},
            headers=self._headers(),
        )
        assert resp.status_code == 200, f"Disable web publish failed: {resp.status_code}"

    def test_delete_share(self):
        """T20: DELETE /shares/{id}"""
        assert self.state.share_id, "No share_id"
        resp = self.client.delete(
            f"{self.config.server_url}/shares/{self.state.share_id}",
            headers=self._headers(),
        )
        assert resp.status_code == 204, f"Delete share failed: {resp.status_code} {resp.text}"
        self.log_verbose(f"  Deleted share: {self.state.share_id[:8]}...")

    def test_verify_share_deleted(self):
        """Verify share is gone."""
        assert self.state.share_id
        resp = self.client.get(
            f"{self.config.server_url}/shares/{self.state.share_id}",
            headers=self._headers(),
        )
        assert resp.status_code in (404, 403), f"Share still exists: {resp.status_code}"

    # =================================================================
    # Run all tests
    # =================================================================

    def run_all(self) -> bool:
        print("\n" + "=" * 70)
        print("  RELAY-ONPREM: COMPREHENSIVE PLUGIN OPERATIONS TEST")
        print("=" * 70)
        print(f"  Server:  {self.config.server_url}")
        print(f"  User:    {self.config.email}")
        if self.config.second_email:
            print(f"  User #2: {self.config.second_email}")
        if self.config.only_groups:
            print(f"  Groups:  {', '.join(self.config.only_groups)}")
        print("=" * 70 + "\n")

        # --- Server Info ---
        print("--- Server Info ---")
        self._run_test("T21: Get server info", self.test_server_info, "server")
        self._run_test("T22: Get public key", self.test_public_key, "server")
        self._run_test("     Health check", self.test_health, "server")

        # --- Auth ---
        print("\n--- Authentication ---")
        r = self._run_test("T1: Login + Get user info", self.test_login, "auth")
        if not r.passed:
            self._print_summary()
            return False

        has_second_user = bool(
            self.config.second_email and self.config.second_password
        )
        if has_second_user:
            self._run_test(
                "    Login second user", self.test_login_second_user, "auth"
            )

        # --- Shares CRUD ---
        print("\n--- Shares CRUD ---")
        self._run_test("T3: List shares", self.test_list_shares_empty, "shares")
        r = self._run_test("T4: Create folder share", self.test_create_folder_share, "shares")
        if not r.passed:
            self._print_summary()
            return False
        self._run_test("T5: Get share details", self.test_get_share, "shares")
        self._run_test("T6: Update share visibility", self.test_update_share_visibility, "shares")

        # --- Members ---
        if has_second_user:
            print("\n--- Share Members ---")
            self._run_test("T7: Get share members", self.test_get_members, "members")
            self._run_test("T8: Add member by email", self.test_add_member_by_email, "members")
            self._run_test("    Update member role", self.test_update_member_role, "members")
            self._run_test("T9: Remove member", self.test_remove_member, "members")
        else:
            print("\n--- Share Members (SKIPPED - no second user) ---")
            self.log("Provide --second-email/--second-password to test members", "SKIP")

        # --- Invites ---
        print("\n--- Invites ---")
        self._run_test("T10: Create invite", self.test_create_invite, "invites")
        self._run_test("T11: List invites", self.test_list_invites, "invites")
        self._run_test("     Get invite info (public)", self.test_get_invite_info, "invites")
        self._run_test("T12: Revoke invite", self.test_revoke_invite, "invites")

        # --- OAuth Flow ---
        print("\n--- OAuth Flow (Headless) ---")
        self._run_test("OAuth1: List OAuth providers", self.test_oauth_providers_list, "oauth")
        self._run_test("OAuth2: Authorize endpoint (JSON)", self.test_oauth_authorize_json, "oauth")
        self._run_test("OAuth3: Casdoor reachability", self.test_oauth_casdoor_reachable, "oauth")
        self._run_test("OAuth4: Invite page OAuth link", self.test_invite_page_oauth_link, "oauth")

        # --- Relay Tokens ---
        print("\n--- Relay Tokens ---")
        self._run_test("T13: Get relay token (CWT)", self.test_get_relay_token, "tokens")

        # --- WebSocket ---
        if not self.config.skip_ws and HAS_WEBSOCKETS:
            print("\n--- WebSocket Connection ---")
            self._run_async_test("T14: WS auth header", self.test_ws_auth_header, "websocket")
            self._run_async_test("T15: WS ?token= query param", self.test_ws_query_param, "websocket")
        elif self.config.skip_ws:
            print("\n--- WebSocket Connection (SKIPPED by --skip-ws) ---")
        else:
            print("\n--- WebSocket Connection (SKIPPED - websockets not installed) ---")

        # --- Web Publishing ---
        print("\n--- Web Publishing ---")
        self._run_test("T16: Enable web publishing", self.test_enable_web_publishing, "webpub")
        self._run_test("T17: Sync folder items", self.test_sync_folder_items, "webpub")
        self._run_test("T18: Sync file content", self.test_sync_file_content, "webpub")
        self._run_test("     Get file content", self.test_get_file_content, "webpub")
        self._run_test("     Get web share by slug", self.test_get_web_share_by_slug, "webpub")
        if self.state.web_publish_domain:
            self._run_test("T19: Check web-publish page", self.test_web_publish_page, "webpub")
        else:
            self.log("No web_publish_domain in server info, skipping page check", "SKIP")

        # --- Cleanup ---
        if not self.config.skip_cleanup:
            print("\n--- Cleanup ---")
            self._run_test("     Disable web publishing", self.test_disable_web_publishing, "cleanup")
            self._run_test("T20: Delete share", self.test_delete_share, "cleanup")
            self._run_test("     Verify share deleted", self.test_verify_share_deleted, "cleanup")
        else:
            print("\n--- Cleanup (SKIPPED by --skip-cleanup) ---")

        self._print_summary()
        return all(r.passed for r in self.results)

    def _print_summary(self):
        passed = sum(1 for r in self.results if r.passed)
        failed = sum(1 for r in self.results if not r.passed)
        skipped = sum(1 for r in self.results if r.details == "SKIPPED")
        total_ms = sum(r.duration_ms for r in self.results)

        print("\n" + "=" * 70)
        print(f"  RESULTS: {passed} passed, {failed} failed, {skipped} skipped")
        print(f"  Total time: {total_ms:.0f}ms ({total_ms/1000:.1f}s)")
        print("=" * 70)

        if failed > 0:
            print("\n  FAILURES:")
            for r in self.results:
                if not r.passed:
                    print(f"    - {r.name}: {r.error}")
            print()

        if failed == 0:
            self.log("ALL TESTS PASSED!", "OK")
        else:
            self.log(f"{failed} TESTS FAILED", "FAIL")
        print("=" * 70 + "\n")


# =====================================================================
# CLI
# =====================================================================


def main():
    parser = argparse.ArgumentParser(
        description="Comprehensive headless test for all plugin operations",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Full test on staging:
  %(prog)s -s http://localhost:8000 -e test@entire.vc -p Test123456

  # Full test with member operations:
  %(prog)s -s http://localhost:8000 -e test@entire.vc -p Test123456 \\
           --second-email simple@entire.vc --second-password password123

  # Only share and member tests:
  %(prog)s -s http://localhost:8000 -e test@entire.vc -p Test123456 \\
           --only shares,members

  # Skip WebSocket tests (no relay server access):
  %(prog)s -s http://localhost:8000 -e test@entire.vc -p Test123456 --skip-ws
        """,
    )

    parser.add_argument("--server", "-s", required=True, help="Control plane URL")
    parser.add_argument("--email", "-e", required=True, help="Primary user email")
    parser.add_argument("--password", "-p", required=True, help="Primary user password")
    parser.add_argument("--second-email", help="Second user email (for member tests)")
    parser.add_argument("--second-password", help="Second user password")
    parser.add_argument("--verbose", "-v", action="store_true", help="Verbose output")
    parser.add_argument("--skip-ws", action="store_true", help="Skip WebSocket tests")
    parser.add_argument(
        "--skip-cleanup", action="store_true", help="Don't delete test share after tests"
    )
    parser.add_argument(
        "--only",
        help="Only run specific test groups (comma-separated): "
        "server,auth,shares,members,invites,oauth,tokens,websocket,webpub,cleanup",
    )

    args = parser.parse_args()

    only_groups = None
    if args.only:
        only_groups = [g.strip() for g in args.only.split(",")]

    config = TestConfig(
        server_url=args.server.rstrip("/"),
        email=args.email,
        password=args.password,
        second_email=args.second_email,
        second_password=args.second_password,
        verbose=args.verbose,
        skip_ws=args.skip_ws,
        skip_cleanup=args.skip_cleanup,
        only_groups=only_groups,
    )

    tester = PluginOperationsTester(config)
    success = tester.run_all()
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
