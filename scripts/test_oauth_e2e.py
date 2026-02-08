#!/usr/bin/env python3
"""
OAuth E2E Integration Tests

Tests the complete OAuth flow: Casdoor login → CP token exchange → API access → relay.

This script simulates the OAuth flow that the Obsidian plugin uses:
1. Plugin calls CP: GET /v1/auth/oauth/casdoor/authorize
2. CP returns {authorize_url, state} with PKCE code_challenge
3. Playwright automates Casdoor login (fills credentials, clicks Sign In)
4. Casdoor redirects to callback with ?code=X&state=Y (intercepted by Playwright)
5. Plugin calls CP: GET /v1/auth/oauth/casdoor/callback?code=X&state=Y
6. CP exchanges code with Casdoor, creates/finds user, returns JWT + user info

Usage:
    # Full test suite:
    python scripts/test_oauth_e2e.py --server https://cp.5evofarm.entire.vc -v

    # Server edge case tests only:
    python scripts/test_oauth_e2e.py --server https://cp.5evofarm.entire.vc --only server -v

    # OAuth flow tests only:
    python scripts/test_oauth_e2e.py --server https://cp.5evofarm.entire.vc --only oauth -v

    # With visible browser for debugging:
    python scripts/test_oauth_e2e.py --server https://cp.5evofarm.entire.vc --headed -v

    # Custom Casdoor credentials:
    python scripts/test_oauth_e2e.py --server https://cp.5evofarm.entire.vc \
        --casdoor-user oauth-test --casdoor-password 123456

    # Skip WebSocket tests:
    python scripts/test_oauth_e2e.py --server https://cp.5evofarm.entire.vc --skip-ws

Requirements:
    pip install httpx playwright
    playwright install chromium
    pip install websockets  # optional, for WebSocket tests
"""

import argparse
import asyncio
import base64
import hashlib
import json
import sys
import time
import traceback
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional
from urllib.parse import urlparse, parse_qs, unquote

import httpx

try:
    import websockets
    HAS_WEBSOCKETS = True
except ImportError:
    HAS_WEBSOCKETS = False

try:
    from playwright.sync_api import sync_playwright
    HAS_PLAYWRIGHT = True
except ImportError:
    HAS_PLAYWRIGHT = False


# =====================================================================
# Configuration
# =====================================================================


@dataclass
class TestConfig:
    server_url: str
    casdoor_url: str
    casdoor_org: str
    casdoor_app: str
    casdoor_user: str
    casdoor_password: str
    cp_admin_email: str
    cp_admin_password: str
    verbose: bool = False
    skip_ws: bool = False
    headed: bool = False
    only: Optional[list[str]] = None


@dataclass
class TestStats:
    total: int = 0
    passed: int = 0
    failed: int = 0
    skipped: int = 0
    errors: list[tuple[str, str]] = field(default_factory=list)


# =====================================================================
# Test Result Tracking
# =====================================================================


class TestResult:
    def __init__(self, name: str, config: TestConfig, stats: TestStats):
        self.name = name
        self.config = config
        self.stats = stats
        self.start_time = time.time()

    def __enter__(self):
        self.stats.total += 1
        if self.config.verbose:
            print(f"  → {self.name}...", end="", flush=True)
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        elapsed = time.time() - self.start_time
        if exc_type is None:
            self.stats.passed += 1
            if self.config.verbose:
                print(f" ✓ ({elapsed:.2f}s)")
        elif exc_type == SkipTest:
            self.stats.skipped += 1
            if self.config.verbose:
                print(f" ⊘ SKIPPED: {exc_val}")
            return True
        else:
            self.stats.failed += 1
            error_msg = f"{exc_type.__name__}: {exc_val}"
            self.stats.errors.append((self.name, error_msg))
            if self.config.verbose:
                print(f" ✗ FAILED ({elapsed:.2f}s)")
                print(f"    {error_msg}")
                if self.config.verbose:
                    traceback.print_exc()
            else:
                print(f"✗ {self.name}: {error_msg}")
            return True


class SkipTest(Exception):
    pass


# =====================================================================
# HTTP Client Helpers
# =====================================================================


def create_http_client() -> httpx.Client:
    """Create HTTP client with connection pooling settings."""
    return httpx.Client(
        timeout=30.0,
        limits=httpx.Limits(max_keepalive_connections=0),
        follow_redirects=False,
    )


def log_request(config: TestConfig, method: str, url: str, **kwargs):
    if config.verbose:
        headers = kwargs.get("headers", {})
        auth_header = headers.get("Authorization", "")
        if auth_header:
            auth_header = auth_header[:20] + "..." if len(auth_header) > 20 else auth_header
        print(f"    {method} {url}")
        if auth_header:
            print(f"      Authorization: {auth_header}")


def log_response(config: TestConfig, response: httpx.Response):
    if config.verbose:
        print(f"      ← {response.status_code}")
        if response.headers.get("content-type", "").startswith("application/json"):
            try:
                data = response.json()
                print(f"      {json.dumps(data, indent=8)[:200]}")
            except Exception:
                pass


# =====================================================================
# OAuth Flow Helpers
# =====================================================================


def extract_pkce_from_state(state: str) -> tuple[str, str]:
    """Decode state and extract PKCE code_verifier and redirect_uri."""
    try:
        decoded = base64.urlsafe_b64decode(state + "==").decode("utf-8")
        data = json.loads(decoded)
        return data.get("code_verifier", ""), data.get("redirect_uri", "")
    except Exception as e:
        raise ValueError(f"Failed to decode state: {e}")


def compute_pkce_challenge(verifier: str) -> str:
    """Compute PKCE code_challenge from code_verifier (SHA256, base64url)."""
    challenge = hashlib.sha256(verifier.encode("utf-8")).digest()
    return base64.urlsafe_b64encode(challenge).decode("utf-8").rstrip("=")


def get_casdoor_code(config: TestConfig, authorize_url: str) -> str:
    """
    Get OAuth authorization code from Casdoor using Playwright browser automation.

    Flow:
    1. Navigate to Casdoor authorize URL (login page)
    2. Fill username + password
    3. Click Sign In
    4. Intercept redirect to localhost callback → extract ?code= param
    """
    if not HAS_PLAYWRIGHT:
        raise SkipTest("Playwright not installed (pip install playwright && playwright install chromium)")

    captured_code = None

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=not config.headed)
        page = browser.new_page()

        # Intercept redirect to localhost callback — capture the authorization code
        def handle_callback(route):
            nonlocal captured_code
            url = route.request.url
            parsed = urlparse(url)
            params = parse_qs(parsed.query)
            captured_code = params.get("code", [None])[0]
            if config.verbose:
                print(f"      Intercepted callback → code={captured_code[:20]}..." if captured_code else "      Intercepted callback → NO CODE")
            route.fulfill(
                status=200,
                body="<html><body>OAuth callback captured.</body></html>",
                headers={"Content-Type": "text/html"},
            )

        # Route any request to 127.0.0.1 (the callback server that plugin runs)
        page.route("http://127.0.0.1:**/*", handle_callback)

        # Navigate to Casdoor login page
        page.goto(authorize_url, wait_until="networkidle", timeout=15000)

        if config.verbose:
            print(f"      Casdoor page loaded: {page.title()}")

        # Fill credentials and submit
        page.fill('#input', config.casdoor_user)
        page.fill('#normal_login_password', config.casdoor_password)
        page.click('button[type="submit"]')

        # Wait for redirect to callback
        page.wait_for_timeout(5000)

        browser.close()

    if not captured_code:
        raise RuntimeError("Failed to capture OAuth code from Casdoor redirect")

    return captured_code


# =====================================================================
# Server Edge Case Tests (S5-S10)
# =====================================================================


def test_s5_callback_without_params(config: TestConfig, client: httpx.Client):
    """S5: GET /v1/auth/oauth/casdoor/callback without code/state should return 400/422."""
    with TestResult("S5: Callback without params returns error", config, stats):
        log_request(config, "GET", f"{config.server_url}/v1/auth/oauth/casdoor/callback")

        response = client.get(f"{config.server_url}/v1/auth/oauth/casdoor/callback")
        log_response(config, response)

        assert response.status_code in (400, 422), \
            f"Expected 400/422 for missing params, got {response.status_code}"


def test_s6_callback_invalid_state(config: TestConfig, client: httpx.Client):
    """S6: Callback with invalid state should return error."""
    with TestResult("S6: Callback with invalid state", config, stats):
        log_request(config, "GET", f"{config.server_url}/v1/auth/oauth/casdoor/callback?code=fake&state=invalid")

        response = client.get(
            f"{config.server_url}/v1/auth/oauth/casdoor/callback",
            params={"code": "fake", "state": "invalid_base64!!!"},
        )
        log_response(config, response)

        assert response.status_code >= 400, \
            f"Expected error for invalid state, got {response.status_code}"


def test_s7_callback_fake_code(config: TestConfig, client: httpx.Client):
    """S7: Callback with fake code should return error from Casdoor."""
    with TestResult("S7: Callback with fake code", config, stats):
        # First get a real state from authorize
        log_request(config, "GET", f"{config.server_url}/v1/auth/oauth/casdoor/authorize")

        auth_response = client.get(
            f"{config.server_url}/v1/auth/oauth/casdoor/authorize",
            params={"redirect_uri": "http://127.0.0.1:19876/callback"},
            headers={"Accept": "application/json"},
        )

        assert auth_response.status_code == 200
        auth_data = auth_response.json()
        state = auth_data.get("state")

        # Try to use fake code with real state
        log_request(config, "GET", f"{config.server_url}/v1/auth/oauth/casdoor/callback?code=fake&state={state[:20]}...")

        callback_response = client.get(
            f"{config.server_url}/v1/auth/oauth/casdoor/callback",
            params={"code": "fake_code_12345", "state": state},
        )
        log_response(config, callback_response)

        assert callback_response.status_code >= 400, \
            f"Expected error for fake code, got {callback_response.status_code}"


def test_s8_state_contains_verifier(config: TestConfig, client: httpx.Client):
    """S8: Decode state and verify it contains code_verifier and redirect_uri."""
    with TestResult("S8: State contains PKCE verifier and redirect_uri", config, stats):
        log_request(config, "GET", f"{config.server_url}/v1/auth/oauth/casdoor/authorize")

        response = client.get(
            f"{config.server_url}/v1/auth/oauth/casdoor/authorize",
            params={"redirect_uri": "http://127.0.0.1:19876/callback"},
            headers={"Accept": "application/json"},
        )

        log_response(config, response)
        assert response.status_code == 200

        data = response.json()
        state = data.get("state")
        assert state, "No state in response"

        # Decode state
        verifier, redirect_uri = extract_pkce_from_state(state)

        assert verifier, "No code_verifier in state"
        assert len(verifier) >= 43, f"code_verifier too short: {len(verifier)}"
        assert redirect_uri == "http://127.0.0.1:19876/callback", \
            f"Wrong redirect_uri: {redirect_uri}"

        if config.verbose:
            print(f"      Verifier length: {len(verifier)}")
            print(f"      Redirect URI: {redirect_uri}")


def test_s9_pkce_challenge_valid(config: TestConfig, client: httpx.Client):
    """S9: Verify PKCE code_challenge is SHA256(code_verifier) with S256 method."""
    with TestResult("S9: PKCE challenge matches SHA256(verifier)", config, stats):
        log_request(config, "GET", f"{config.server_url}/v1/auth/oauth/casdoor/authorize")

        response = client.get(
            f"{config.server_url}/v1/auth/oauth/casdoor/authorize",
            params={"redirect_uri": "http://127.0.0.1:19876/callback"},
            headers={"Accept": "application/json"},
        )

        assert response.status_code == 200
        data = response.json()

        authorize_url = data.get("authorize_url")
        state = data.get("state")

        assert authorize_url, "No authorize_url in response"

        # Extract challenge from authorize_url
        parsed = urlparse(authorize_url)
        params = parse_qs(parsed.query)

        challenge = params.get("code_challenge", [None])[0]
        challenge_method = params.get("code_challenge_method", [None])[0]

        assert challenge, "No code_challenge in authorize_url"
        assert challenge_method == "S256", f"Wrong method: {challenge_method}"

        # Decode state to get verifier
        verifier, _ = extract_pkce_from_state(state)

        # Compute expected challenge
        expected_challenge = compute_pkce_challenge(verifier)

        assert challenge == expected_challenge, \
            f"Challenge mismatch: {challenge} != {expected_challenge}"

        if config.verbose:
            print(f"      Challenge method: {challenge_method}")
            print(f"      Challenge: {challenge[:20]}...")


def test_s10_https_redirect_uri(config: TestConfig, client: httpx.Client):
    """S10: Authorize with HTTPS redirect_uri should preserve it in state."""
    with TestResult("S10: HTTPS redirect_uri preserved in state", config, stats):
        https_redirect = "https://example.com/oauth/callback"

        log_request(config, "GET", f"{config.server_url}/v1/auth/oauth/casdoor/authorize")

        response = client.get(
            f"{config.server_url}/v1/auth/oauth/casdoor/authorize",
            params={"redirect_uri": https_redirect},
            headers={"Accept": "application/json"},
        )

        assert response.status_code == 200
        data = response.json()
        state = data.get("state")

        _, redirect_uri = extract_pkce_from_state(state)

        assert redirect_uri == https_redirect, \
            f"Redirect URI not preserved: {redirect_uri} != {https_redirect}"


# =====================================================================
# Full OAuth Flow Tests (E1-E7)
# =====================================================================


def test_e1_full_oauth_new_user(config: TestConfig, client: httpx.Client) -> dict:
    """E1: Full OAuth login with new user auto-register."""
    with TestResult("E1: Full OAuth flow (new user auto-register)", config, stats):
        redirect_uri = "http://127.0.0.1:19876/callback"

        # Step 1: Get authorize URL from CP
        log_request(config, "GET", f"{config.server_url}/v1/auth/oauth/casdoor/authorize")

        auth_response = client.get(
            f"{config.server_url}/v1/auth/oauth/casdoor/authorize",
            params={"redirect_uri": redirect_uri},
            headers={"Accept": "application/json"},
        )

        log_response(config, auth_response)
        assert auth_response.status_code == 200, f"Authorize failed: {auth_response.status_code}"

        auth_data = auth_response.json()
        authorize_url = auth_data.get("authorize_url")
        state = auth_data.get("state")

        assert authorize_url, "No authorize_url in response"
        assert state, "No state in response"

        # Step 2: Get authorization code from Casdoor via Playwright
        if config.verbose:
            print(f"      Automating Casdoor login...")

        code = get_casdoor_code(config, authorize_url)

        if config.verbose:
            print(f"      Got code: {code[:20]}...")

        # Step 3: Exchange code for token via CP callback
        log_request(config, "GET", f"{config.server_url}/v1/auth/oauth/casdoor/callback")

        callback_response = client.get(
            f"{config.server_url}/v1/auth/oauth/casdoor/callback",
            params={"code": code, "state": state},
        )

        log_response(config, callback_response)
        assert callback_response.status_code == 200, \
            f"Callback failed: {callback_response.status_code} {callback_response.text}"

        callback_data = callback_response.json()

        # Verify response structure
        assert "access_token" in callback_data, "No access_token in response"
        assert "refresh_token" in callback_data, "No refresh_token in response"
        assert "expires_in" in callback_data, "No expires_in in response"
        assert "user_id" in callback_data, "No user_id in response"
        assert "user_email" in callback_data, "No user_email in response"

        if config.verbose:
            print(f"      User ID: {callback_data['user_id']}")
            print(f"      User Email: {callback_data['user_email']}")
            print(f"      Expires in: {callback_data['expires_in']}s")

        return callback_data


def test_e2_full_oauth_existing_user(config: TestConfig, client: httpx.Client) -> dict:
    """E2: Full OAuth login with existing user."""
    with TestResult("E2: Full OAuth flow (existing user)", config, stats):
        redirect_uri = "http://127.0.0.1:19876/callback"

        # Get authorize URL
        auth_response = client.get(
            f"{config.server_url}/v1/auth/oauth/casdoor/authorize",
            params={"redirect_uri": redirect_uri},
            headers={"Accept": "application/json"},
        )

        assert auth_response.status_code == 200
        auth_data = auth_response.json()

        # Get code from Casdoor via Playwright
        code = get_casdoor_code(config, auth_data["authorize_url"])

        # Exchange for token
        callback_response = client.get(
            f"{config.server_url}/v1/auth/oauth/casdoor/callback",
            params={"code": code, "state": auth_data["state"]},
        )

        log_response(config, callback_response)
        assert callback_response.status_code == 200

        callback_data = callback_response.json()

        # Should be the same user as E1
        assert callback_data.get("user_email") == config.casdoor_user

        return callback_data


def test_e3_refresh_token(config: TestConfig, client: httpx.Client, oauth_data: dict):
    """E3: Verify refresh_token works."""
    with TestResult("E3: Refresh token exchange", config, stats):
        refresh_token = oauth_data.get("refresh_token")
        assert refresh_token, "No refresh_token from OAuth"

        log_request(config, "POST", f"{config.server_url}/v1/auth/refresh")

        response = client.post(
            f"{config.server_url}/v1/auth/refresh",
            json={"refresh_token": refresh_token},
        )

        log_response(config, response)
        assert response.status_code == 200, f"Refresh failed: {response.status_code}"

        data = response.json()
        assert "access_token" in data, "No new access_token"
        assert "expires_in" in data, "No expires_in"

        if config.verbose:
            print(f"      New token expires in: {data['expires_in']}s")


def test_e4_oauth_user_shares(config: TestConfig, client: httpx.Client, oauth_data: dict):
    """E4: OAuth user can create and see shares."""
    with TestResult("E4: OAuth user can manage shares", config, stats):
        access_token = oauth_data.get("access_token")
        headers = {"Authorization": f"Bearer {access_token}"}

        # Create a share
        log_request(config, "POST", f"{config.server_url}/v1/shares", headers=headers)

        create_response = client.post(
            f"{config.server_url}/v1/shares",
            json={
                "path": "test-oauth-share",
                "kind": "folder",
                "visibility": "private",
            },
            headers=headers,
        )

        log_response(config, create_response)
        assert create_response.status_code == 201, \
            f"Create share failed: {create_response.status_code}"

        share = create_response.json()
        share_id = share.get("id")

        if config.verbose:
            print(f"      Created share ID: {share_id}")

        # List shares
        log_request(config, "GET", f"{config.server_url}/v1/shares", headers=headers)

        list_response = client.get(f"{config.server_url}/v1/shares", headers=headers)

        log_response(config, list_response)
        assert list_response.status_code == 200

        shares = list_response.json()
        assert any(s["id"] == share_id for s in shares), "Created share not in list"

        # Delete the test share
        client.delete(f"{config.server_url}/v1/shares/{share_id}", headers=headers)


def test_e5_oauth_user_relay_token(config: TestConfig, client: httpx.Client, oauth_data: dict) -> dict:
    """E5: OAuth user can get relay token."""
    with TestResult("E5: OAuth user can get relay token", config, stats):
        access_token = oauth_data.get("access_token")
        headers = {"Authorization": f"Bearer {access_token}"}

        # Create a share for relay token testing
        share_response = client.post(
            f"{config.server_url}/v1/shares",
            json={
                "path": "test-oauth-relay.md",
                "kind": "doc",
                "visibility": "private",
            },
            headers=headers,
        )
        assert share_response.status_code == 201, \
            f"Create share failed: {share_response.status_code} {share_response.text}"
        share = share_response.json()
        share_id = share["id"]

        # Request relay token — use share_id as doc_id
        log_request(config, "POST", f"{config.server_url}/v1/tokens/relay", headers=headers)

        token_response = client.post(
            f"{config.server_url}/v1/tokens/relay",
            json={"share_id": share_id, "doc_id": share_id, "mode": "write"},
            headers=headers,
        )

        log_response(config, token_response)
        assert token_response.status_code == 200, \
            f"Relay token failed: {token_response.status_code}"

        token_data = token_response.json()
        assert "token" in token_data, "No token in response"
        assert "relay_url" in token_data, "No relay_url in response"

        if config.verbose:
            print(f"      Token: {token_data['token'][:30]}...")
            print(f"      Relay URL: {token_data['relay_url']}")

        # Add doc_id to token_data for E6 WebSocket test
        token_data["doc_id"] = share_id

        # Cleanup
        client.delete(f"{config.server_url}/v1/shares/{share['id']}", headers=headers)

        return token_data


async def test_e6_oauth_relay_websocket(config: TestConfig, client: httpx.Client, token_data: dict):
    """E6: OAuth user relay token works via WebSocket."""
    if config.skip_ws or not HAS_WEBSOCKETS:
        raise SkipTest("WebSocket tests disabled or websockets not installed")

    with TestResult("E6: OAuth relay token WebSocket connection", config, stats):
        relay_url = token_data.get("relay_url")
        token = token_data.get("token")
        doc_id = token_data.get("doc_id")

        # Construct WebSocket URL: {relay_url}/{doc_id}
        ws_url = relay_url.replace("https://", "wss://").replace("http://", "ws://")
        ws_url = f"{ws_url.rstrip('/')}/{doc_id}"

        if config.verbose:
            print(f"      Connecting to: {ws_url}")

        # Try connection with Authorization header
        try:
            async with websockets.connect(
                ws_url,
                additional_headers={"Authorization": f"Bearer {token}"},
            ) as websocket:
                if config.verbose:
                    print(f"      ✓ Connected with Authorization header")

                # Send a ping to verify connection is alive
                await websocket.ping()
        except Exception as e:
            raise RuntimeError(f"WebSocket connection failed: {e}")


def test_e7_oauth_logout(config: TestConfig, client: httpx.Client, oauth_data: dict):
    """E7: OAuth logout endpoint works.

    Note: JWT tokens are stateless — logout clears the server-side session
    but the token itself may remain valid until expiration. We only verify
    that the logout endpoint responds correctly.
    """
    with TestResult("E7: OAuth logout", config, stats):
        access_token = oauth_data.get("access_token")
        headers = {"Authorization": f"Bearer {access_token}"}

        # First verify token works
        me_response = client.get(f"{config.server_url}/v1/auth/me", headers=headers)
        assert me_response.status_code == 200, "Token should work before logout"

        # Logout
        log_request(config, "POST", f"{config.server_url}/v1/auth/logout", headers=headers)

        logout_response = client.post(f"{config.server_url}/v1/auth/logout", headers=headers)

        log_response(config, logout_response)
        assert logout_response.status_code == 200, f"Logout failed: {logout_response.status_code}"

        if config.verbose:
            # Check if session-based invalidation works (may or may not depending on implementation)
            me_after = client.get(f"{config.server_url}/v1/auth/me", headers=headers)
            if me_after.status_code == 401:
                print(f"      Token invalidated (session-based)")
            else:
                print(f"      Token still valid (stateless JWT, expires naturally)")


# =====================================================================
# Test Runner
# =====================================================================


def should_run_group(group: str, config: TestConfig) -> bool:
    """Check if test group should run based on --only filter."""
    if not config.only:
        return True
    return group in config.only or "all" in config.only


def run_tests(config: TestConfig) -> bool:
    """Run all OAuth E2E tests. Returns True if all passed."""
    global stats
    stats = TestStats()

    print("=" * 70)
    print("OAuth E2E Integration Tests")
    print("=" * 70)
    print(f"Server: {config.server_url}")
    print(f"Casdoor: {config.casdoor_url}")
    print(f"Casdoor User: {config.casdoor_user}")
    print(f"Skip WebSocket: {config.skip_ws}")
    print(f"Playwright: {'available' if HAS_PLAYWRIGHT else 'NOT installed (OAuth flow tests will be skipped)'}")
    if config.headed:
        print(f"Browser: headed (visible)")
    if config.only:
        print(f"Test Groups: {', '.join(config.only)}")
    print()

    client = create_http_client()

    # Server edge case tests (S5-S10)
    if should_run_group("server", config):
        print("Server Edge Cases (S5-S10)")
        print("-" * 70)

        try:
            test_s5_callback_without_params(config, client)
        except Exception as e:
            if config.verbose:
                traceback.print_exc()

        try:
            test_s6_callback_invalid_state(config, client)
        except Exception as e:
            if config.verbose:
                traceback.print_exc()

        try:
            test_s7_callback_fake_code(config, client)
        except Exception as e:
            if config.verbose:
                traceback.print_exc()

        try:
            test_s8_state_contains_verifier(config, client)
        except Exception as e:
            if config.verbose:
                traceback.print_exc()

        try:
            test_s9_pkce_challenge_valid(config, client)
        except Exception as e:
            if config.verbose:
                traceback.print_exc()

        try:
            test_s10_https_redirect_uri(config, client)
        except Exception as e:
            if config.verbose:
                traceback.print_exc()

        print()

    # Full OAuth flow tests (E1-E7)
    if should_run_group("oauth", config):
        print("Full OAuth Flow (E1-E7)")
        print("-" * 70)

        oauth_data = None
        token_data = None

        try:
            oauth_data = test_e1_full_oauth_new_user(config, client)
        except Exception as e:
            if config.verbose:
                traceback.print_exc()

        try:
            oauth_data = test_e2_full_oauth_existing_user(config, client)
        except Exception as e:
            if config.verbose:
                traceback.print_exc()

        if oauth_data:
            try:
                test_e3_refresh_token(config, client, oauth_data)
            except Exception as e:
                if config.verbose:
                    traceback.print_exc()

            try:
                test_e4_oauth_user_shares(config, client, oauth_data)
            except Exception as e:
                if config.verbose:
                    traceback.print_exc()

            try:
                token_data = test_e5_oauth_user_relay_token(config, client, oauth_data)
            except Exception as e:
                if config.verbose:
                    traceback.print_exc()

            if token_data:
                try:
                    asyncio.run(test_e6_oauth_relay_websocket(config, client, token_data))
                except Exception as e:
                    if config.verbose:
                        traceback.print_exc()

            try:
                test_e7_oauth_logout(config, client, oauth_data)
            except Exception as e:
                if config.verbose:
                    traceback.print_exc()

        print()

    client.close()

    # Print summary
    print("=" * 70)
    print("Summary")
    print("=" * 70)
    print(f"Total:   {stats.total}")
    print(f"Passed:  {stats.passed} ✓")
    print(f"Failed:  {stats.failed} ✗")
    print(f"Skipped: {stats.skipped} ⊘")
    print()

    if stats.errors and not config.verbose:
        print("Errors:")
        for name, error in stats.errors:
            print(f"  ✗ {name}")
            print(f"    {error}")
        print()

    success_rate = (stats.passed / stats.total * 100) if stats.total > 0 else 0
    print(f"Success Rate: {success_rate:.1f}%")

    return stats.failed == 0


# =====================================================================
# CLI Entry Point
# =====================================================================


def main():
    parser = argparse.ArgumentParser(
        description="OAuth E2E integration tests for Relay Control Plane",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    parser.add_argument(
        "--server",
        required=True,
        help="Control plane URL (e.g., https://cp.5evofarm.entire.vc)",
    )
    parser.add_argument(
        "--casdoor-url",
        default="https://login.entire.vc",
        help="Casdoor URL (default: https://login.entire.vc)",
    )
    parser.add_argument(
        "--casdoor-org",
        default="entire_vc",
        help="Casdoor organization (default: entire_vc)",
    )
    parser.add_argument(
        "--casdoor-app",
        default="entire_vc",
        help="Casdoor application (default: entire_vc)",
    )
    parser.add_argument(
        "--casdoor-user",
        default="oauth-test",
        help="Casdoor test user username (default: oauth-test)",
    )
    parser.add_argument(
        "--casdoor-password",
        default="123456",
        help="Casdoor test user password (default: 123456)",
    )
    parser.add_argument(
        "--cp-admin-email",
        default="test@entire.vc",
        help="CP admin email for setup (default: test@entire.vc)",
    )
    parser.add_argument(
        "--cp-admin-password",
        default="Test123456",
        help="CP admin password (default: Test123456)",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Verbose output",
    )
    parser.add_argument(
        "--skip-ws",
        action="store_true",
        help="Skip WebSocket tests",
    )
    parser.add_argument(
        "--headed",
        action="store_true",
        help="Run browser in headed mode (visible window for debugging)",
    )
    parser.add_argument(
        "--only",
        help="Only run specific test groups (comma-separated: server,oauth,all)",
    )

    args = parser.parse_args()

    config = TestConfig(
        server_url=args.server.rstrip("/"),
        casdoor_url=args.casdoor_url.rstrip("/"),
        casdoor_org=args.casdoor_org,
        casdoor_app=args.casdoor_app,
        casdoor_user=args.casdoor_user,
        casdoor_password=args.casdoor_password,
        cp_admin_email=args.cp_admin_email,
        cp_admin_password=args.cp_admin_password,
        verbose=args.verbose,
        skip_ws=args.skip_ws,
        headed=args.headed,
        only=args.only.split(",") if args.only else None,
    )

    success = run_tests(config)
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
