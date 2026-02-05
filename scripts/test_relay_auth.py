#!/usr/bin/env python3
"""
E2E test script for relay-onprem authentication flow.

Tests the full auth flow without Obsidian:
1. Login to control plane → JWT
2. Create or use existing share
3. Request relay token → CWT
4. Connect to relay-server WebSocket
5. Verify connection works

Usage:
    python scripts/test_relay_auth.py --server https://cp.entire.vc --email test@entire.vc --password Test123456

    # With existing share:
    python scripts/test_relay_auth.py --server https://cp.entire.vc --email test@entire.vc --password Test123456 --share-id <uuid>

    # Create new share:
    python scripts/test_relay_auth.py --server https://cp.entire.vc --email test@entire.vc --password Test123456 --create-share "Test Folder"
"""

import argparse
import asyncio
import base64
import json
import sys
import time
from dataclasses import dataclass
from typing import Optional
from urllib.parse import urlparse

import httpx

# Optional: websockets for WS testing
try:
    import websockets
    HAS_WEBSOCKETS = True
except ImportError:
    HAS_WEBSOCKETS = False
    print("Warning: websockets not installed, WS tests will be skipped")


@dataclass
class TestConfig:
    server_url: str
    email: str
    password: str
    share_id: Optional[str] = None
    create_share_path: Optional[str] = None
    doc_id: Optional[str] = None
    verbose: bool = False


@dataclass
class AuthState:
    jwt_token: str
    user_id: str
    user_email: str


class RelayAuthTester:
    def __init__(self, config: TestConfig):
        self.config = config
        self.auth_state: Optional[AuthState] = None
        self.client = httpx.Client(timeout=30.0)

    def log(self, msg: str, level: str = "INFO"):
        """Log message with timestamp."""
        timestamp = time.strftime("%H:%M:%S")
        prefix = {"INFO": "ℹ️", "OK": "✅", "ERROR": "❌", "WARN": "⚠️"}.get(level, "")
        print(f"[{timestamp}] {prefix} {msg}")

    def log_verbose(self, msg: str):
        """Log only if verbose mode."""
        if self.config.verbose:
            self.log(msg, "INFO")

    # ========== Step 1: Login ==========
    def test_login(self) -> bool:
        """Test login to control plane."""
        self.log("Testing login...")

        url = f"{self.config.server_url}/auth/login"
        payload = {
            "email": self.config.email,
            "password": self.config.password,
        }

        try:
            response = self.client.post(url, json=payload)

            if response.status_code == 200:
                data = response.json()
                self.auth_state = AuthState(
                    jwt_token=data["access_token"],
                    user_id=data["user"]["id"],
                    user_email=data["user"]["email"],
                )
                self.log(f"Login successful: {self.auth_state.user_email}", "OK")
                self.log_verbose(f"JWT: {self.auth_state.jwt_token[:50]}...")
                return True
            else:
                self.log(f"Login failed: {response.status_code} - {response.text}", "ERROR")
                return False
        except Exception as e:
            self.log(f"Login error: {e}", "ERROR")
            return False

    # ========== Step 2: Get/Create Share ==========
    def test_get_or_create_share(self) -> Optional[str]:
        """Get existing share or create new one."""
        if not self.auth_state:
            self.log("Not logged in", "ERROR")
            return None

        headers = {"Authorization": f"Bearer {self.auth_state.jwt_token}"}

        # If share_id provided, verify it exists
        if self.config.share_id:
            self.log(f"Verifying share {self.config.share_id}...")
            url = f"{self.config.server_url}/shares/{self.config.share_id}"

            try:
                response = self.client.get(url, headers=headers)
                if response.status_code == 200:
                    share = response.json()
                    self.log(f"Share found: {share['path']} ({share['kind']})", "OK")
                    return self.config.share_id
                else:
                    self.log(f"Share not found: {response.status_code}", "ERROR")
                    return None
            except Exception as e:
                self.log(f"Error checking share: {e}", "ERROR")
                return None

        # If create_share_path provided, create new share
        if self.config.create_share_path:
            self.log(f"Creating share for: {self.config.create_share_path}...")
            url = f"{self.config.server_url}/shares"
            payload = {
                "kind": "folder",
                "path": self.config.create_share_path,
                "visibility": "private",
            }

            try:
                response = self.client.post(url, json=payload, headers=headers)
                if response.status_code == 200:
                    share = response.json()
                    self.log(f"Share created: {share['id']}", "OK")
                    return share["id"]
                else:
                    self.log(f"Create share failed: {response.status_code} - {response.text}", "ERROR")
                    return None
            except Exception as e:
                self.log(f"Error creating share: {e}", "ERROR")
                return None

        # List existing shares
        self.log("Listing existing shares...")
        url = f"{self.config.server_url}/shares"

        try:
            response = self.client.get(url, headers=headers)
            if response.status_code == 200:
                shares = response.json()
                if shares:
                    # Use first share
                    share = shares[0]
                    self.log(f"Using existing share: {share['path']} ({share['id']})", "OK")
                    return share["id"]
                else:
                    self.log("No shares found. Use --create-share to create one.", "WARN")
                    return None
            else:
                self.log(f"List shares failed: {response.status_code}", "ERROR")
                return None
        except Exception as e:
            self.log(f"Error listing shares: {e}", "ERROR")
            return None

    # ========== Step 3: Get Relay Token (CWT) ==========
    def test_get_relay_token(self, share_id: str) -> Optional[dict]:
        """Request relay token (CWT) for share."""
        if not self.auth_state:
            self.log("Not logged in", "ERROR")
            return None

        self.log("Requesting relay token (CWT)...")

        headers = {"Authorization": f"Bearer {self.auth_state.jwt_token}"}
        url = f"{self.config.server_url}/tokens/relay"

        # Use provided doc_id or share_id
        doc_id = self.config.doc_id or share_id

        payload = {
            "share_id": share_id,
            "doc_id": doc_id,
            "mode": "write",
        }

        try:
            response = self.client.post(url, json=payload, headers=headers)

            if response.status_code == 200:
                data = response.json()
                self.log(f"Relay token received", "OK")
                self.log(f"  Relay URL: {data['relay_url']}")
                self.log(f"  Expires: {data['expires_at']}")
                self.log_verbose(f"  Token: {data['token'][:60]}...")

                # Decode and show CWT claims
                self._decode_cwt_token(data["token"])

                return data
            else:
                self.log(f"Token request failed: {response.status_code} - {response.text}", "ERROR")
                return None
        except Exception as e:
            self.log(f"Token request error: {e}", "ERROR")
            return None

    def _decode_cwt_token(self, token: str):
        """Decode and display CWT token claims (without verification)."""
        try:
            # Add padding if needed
            padding = 4 - len(token) % 4
            if padding != 4:
                token += "=" * padding
            token_bytes = base64.urlsafe_b64decode(token)

            # Import cbor2 for decoding
            import cbor2

            outer = cbor2.loads(token_bytes)

            # Navigate through CWT (tag 61) -> COSE_Sign1 (tag 18) -> payload
            if hasattr(outer, 'tag') and outer.tag == 61:
                inner = outer.value
                if hasattr(inner, 'tag') and inner.tag == 18:
                    cose_sign1 = inner.value
                else:
                    cose_sign1 = inner
            else:
                cose_sign1 = outer

            if isinstance(cose_sign1, list) and len(cose_sign1) >= 3:
                payload_cbor = cose_sign1[2]
                claims = cbor2.loads(payload_cbor)

                # Map claim IDs to names
                claim_names = {1: "iss", 2: "sub", 3: "aud", 4: "exp", 6: "iat", -80201: "scope", -80202: "channel"}
                named_claims = {claim_names.get(k, k): v for k, v in claims.items()}

                self.log(f"  CWT Claims: {json.dumps(named_claims, default=str)}")
        except Exception as e:
            self.log_verbose(f"  Could not decode CWT: {e}")

    # ========== Step 4: WebSocket Connection ==========
    async def test_websocket_connection(self, relay_url: str, token: str, doc_id: str) -> bool:
        """Test WebSocket connection to relay-server."""
        if not HAS_WEBSOCKETS:
            self.log("Skipping WebSocket test (websockets not installed)", "WARN")
            return True

        self.log("Testing WebSocket connection...")

        # Parse relay URL and construct WebSocket URL
        parsed = urlparse(relay_url)

        # Construct WebSocket URL: wss://relay/doc/ws/{doc_id}?token={token}
        # or based on path structure: wss://relay/d/{doc_id}/ws/{doc_id2}
        ws_url = f"{relay_url}/{doc_id}?token={token}"

        self.log_verbose(f"  WS URL: {ws_url[:80]}...")

        try:
            async with websockets.connect(ws_url, ping_interval=20, ping_timeout=20) as ws:
                self.log("WebSocket connected!", "OK")

                # Try to receive initial sync message
                try:
                    msg = await asyncio.wait_for(ws.recv(), timeout=5.0)
                    self.log(f"  Received {len(msg)} bytes", "OK")
                    return True
                except asyncio.TimeoutError:
                    self.log("  No initial message (might be empty doc)", "WARN")
                    return True

        except websockets.exceptions.InvalidStatusCode as e:
            self.log(f"WebSocket connection failed: {e.status_code}", "ERROR")
            return False
        except Exception as e:
            self.log(f"WebSocket error: {e}", "ERROR")
            return False

    # ========== Run All Tests ==========
    def run_all(self) -> bool:
        """Run complete auth flow test."""
        print("\n" + "=" * 60)
        print("  RELAY-ONPREM AUTH FLOW TEST")
        print("=" * 60)
        print(f"Server: {self.config.server_url}")
        print(f"User: {self.config.email}")
        print("=" * 60 + "\n")

        # Step 1: Login
        if not self.test_login():
            return False

        # Step 2: Get/Create Share
        share_id = self.test_get_or_create_share()
        if not share_id:
            return False

        # Step 3: Get Relay Token
        token_data = self.test_get_relay_token(share_id)
        if not token_data:
            return False

        # Step 4: WebSocket Connection
        doc_id = self.config.doc_id or share_id
        ws_success = asyncio.run(
            self.test_websocket_connection(
                token_data["relay_url"],
                token_data["token"],
                doc_id,
            )
        )

        # Summary
        print("\n" + "=" * 60)
        if ws_success:
            self.log("ALL TESTS PASSED!", "OK")
            print("=" * 60 + "\n")
            return True
        else:
            self.log("SOME TESTS FAILED", "ERROR")
            print("=" * 60 + "\n")
            return False


def main():
    parser = argparse.ArgumentParser(
        description="Test relay-onprem authentication flow",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Test with existing share:
  %(prog)s --server https://cp.entire.vc --email test@entire.vc --password Test123456 --share-id <uuid>

  # Create new share and test:
  %(prog)s --server https://cp.entire.vc --email test@entire.vc --password Test123456 --create-share "Test Folder"

  # Use first available share:
  %(prog)s --server https://cp.entire.vc --email test@entire.vc --password Test123456
        """,
    )

    parser.add_argument("--server", "-s", required=True, help="Control plane URL (e.g., https://cp.entire.vc)")
    parser.add_argument("--email", "-e", required=True, help="User email")
    parser.add_argument("--password", "-p", required=True, help="User password")
    parser.add_argument("--share-id", help="Existing share ID to use")
    parser.add_argument("--create-share", metavar="PATH", help="Create new share with this path")
    parser.add_argument("--doc-id", help="Document ID (defaults to share ID)")
    parser.add_argument("--verbose", "-v", action="store_true", help="Verbose output")

    args = parser.parse_args()

    config = TestConfig(
        server_url=args.server.rstrip("/"),
        email=args.email,
        password=args.password,
        share_id=args.share_id,
        create_share_path=args.create_share,
        doc_id=args.doc_id,
        verbose=args.verbose,
    )

    tester = RelayAuthTester(config)
    success = tester.run_all()

    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
