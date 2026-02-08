#!/usr/bin/env python3
"""
Plugin CRDT Sync Flow E2E Test

Simulates the exact flow that the Obsidian plugin performs:
1. Login to control plane → JWT
2. List shares via GET /shares
3. For each folder share: request CWT token via POST /tokens/relay
4. Connect to relay server via WebSocket with CWT token
5. Send valid Yjs sync step 1 message
6. Verify relay responds with sync step 2
7. Send a Yjs document update
8. Verify persistence (disconnect and reconnect, check sync step 2 contains data)

This test validates the complete plugin CRDT workflow without requiring Obsidian.

Requirements:
    pip install httpx websockets pycrdt

Usage:
    python scripts/test_plugin_crdt_sync.py
    python scripts/test_plugin_crdt_sync.py --server https://cp.5evofarm.entire.vc
    python scripts/test_plugin_crdt_sync.py --email test@entire.vc --password Test123456
    python scripts/test_plugin_crdt_sync.py -v  # verbose mode
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from dataclasses import dataclass

import httpx

try:
    import websockets
except ImportError:
    print("ERROR: pip install websockets")
    sys.exit(1)

try:
    import pycrdt
except ImportError:
    print("ERROR: pip install pycrdt")
    sys.exit(1)


# ── Yjs Protocol Constants ──────────────────────────────────
MSG_SYNC = 0
MSG_AWARENESS = 1
SYNC_STEP1 = 0
SYNC_STEP2 = 1
SYNC_UPDATE = 2


# ── Yjs Binary Encoding ─────────────────────────────────────
def encode_uint(n: int) -> bytes:
    """Encode integer as Yjs variable-length uint."""
    result = bytearray()
    while n > 0x7F:
        result.append(0x80 | (n & 0x7F))
        n >>= 7
    result.append(n & 0x7F)
    return bytes(result)


def decode_uint(data: bytes, offset: int = 0) -> tuple[int, int]:
    """Decode Yjs variable-length uint. Returns (value, next_offset)."""
    result = 0
    shift = 0
    while True:
        byte = data[offset]
        result |= (byte & 0x7F) << shift
        offset += 1
        if byte & 0x80 == 0:
            break
        shift += 7
    return result, offset


def encode_bytes(data: bytes) -> bytes:
    """Encode bytes with length prefix."""
    return encode_uint(len(data)) + data


def decode_bytes(data: bytes, offset: int = 0) -> tuple[bytes, int]:
    """Decode length-prefixed bytes. Returns (data, next_offset)."""
    length, offset = decode_uint(data, offset)
    return data[offset : offset + length], offset + length


# ── Test Configuration ──────────────────────────────────────
@dataclass
class TestConfig:
    server_url: str
    email: str
    password: str
    verbose: bool = False


# ── Plugin CRDT Sync Tester ─────────────────────────────────
class PluginCRDTSyncTester:
    def __init__(self, config: TestConfig):
        self.config = config
        self.jwt_token = ""
        self.user_id = ""
        self.verbose = config.verbose
        self.test_results: list[tuple[str, bool, float]] = []
        self.created_shares: list[str] = []

    def _client(self) -> httpx.Client:
        """Create HTTP client with keep-alive disabled (avoid Caddy issues)."""
        return httpx.Client(
            timeout=15,
            limits=httpx.Limits(max_keepalive_connections=0),
        )

    def log(self, msg: str, level: str = "INFO"):
        """Log with level indicator."""
        symbols = {
            "INFO": "  →",
            "OK": "  ✓",
            "ERROR": "  ✗",
            "WARN": "  ⚠",
        }
        symbol = symbols.get(level, "  ")
        print(f"{symbol} {msg}")

    def log_verbose(self, msg: str):
        """Log only in verbose mode."""
        if self.verbose:
            self.log(msg, "INFO")

    # ── Step 1: Login ──────────────────────────────────────
    def login(self) -> bool:
        """Login to control plane and get JWT token."""
        self.log("Login to control plane...")

        with self._client() as client:
            try:
                response = client.post(
                    f"{self.config.server_url}/v1/auth/login",
                    json={
                        "email": self.config.email,
                        "password": self.config.password,
                    },
                )

                if response.status_code != 200:
                    self.log(f"Login failed: {response.status_code} - {response.text}", "ERROR")
                    return False

                data = response.json()
                self.jwt_token = data["access_token"]

                # Get user info
                me_resp = client.get(
                    f"{self.config.server_url}/v1/auth/me",
                    headers={"Authorization": f"Bearer {self.jwt_token}"},
                )

                if me_resp.status_code == 200:
                    me = me_resp.json()
                    self.user_id = me["id"]
                    self.log(f"Logged in as: {me['email']}", "OK")
                    self.log_verbose(f"User ID: {self.user_id}")
                    return True
                else:
                    self.log(f"Failed to get user info: {me_resp.status_code}", "ERROR")
                    return False

            except Exception as e:
                self.log(f"Login error: {e}", "ERROR")
                return False

    # ── Step 2: List Shares ────────────────────────────────
    def list_shares(self) -> list[dict]:
        """List all shares for the logged-in user."""
        self.log("List shares...")

        with self._client() as client:
            try:
                response = client.get(
                    f"{self.config.server_url}/v1/shares",
                    headers={"Authorization": f"Bearer {self.jwt_token}"},
                )

                if response.status_code != 200:
                    self.log(f"Failed to list shares: {response.status_code}", "ERROR")
                    return []

                shares = response.json()
                self.log(f"Found {len(shares)} shares", "OK")

                for share in shares:
                    self.log_verbose(
                        f"  - {share['kind']}: {share['path']} (id={share['id'][:8]}...)"
                    )

                return shares

            except Exception as e:
                self.log(f"List shares error: {e}", "ERROR")
                return []

    # ── Step 3: Request Relay Token ───────────────────────
    def request_relay_token(
        self, share_id: str, doc_id: str, file_path: str
    ) -> dict | None:
        """Request CWT token for relay server connection."""
        self.log(f"Request relay token for share {share_id[:8]}...")

        with self._client() as client:
            try:
                response = client.post(
                    f"{self.config.server_url}/v1/tokens/relay",
                    json={
                        "share_id": share_id,
                        "doc_id": doc_id,
                        "mode": "write",
                        "file_path": file_path,
                    },
                    headers={"Authorization": f"Bearer {self.jwt_token}"},
                )

                if response.status_code != 200:
                    self.log(
                        f"Token request failed: {response.status_code} - {response.text}",
                        "ERROR",
                    )
                    return None

                token_data = response.json()
                self.log("Relay token received", "OK")
                self.log_verbose(f"  Relay URL: {token_data['relay_url']}")
                self.log_verbose(f"  Expires: {token_data['expires_at']}")

                return token_data

            except Exception as e:
                self.log(f"Token request error: {e}", "ERROR")
                return None

    # ── Step 4: WebSocket CRDT Sync ───────────────────────
    async def test_crdt_sync_handshake(
        self, relay_url: str, token: str, doc_id: str
    ) -> tuple[bool, pycrdt.Doc | None]:
        """Test Yjs sync handshake with relay server.

        Returns: (success, yjs_doc)
        """
        self.log(f"Connect to relay WebSocket for doc {doc_id[:8]}...")

        ws_url = f"{relay_url}/{doc_id}"
        headers = {"Authorization": f"Bearer {token}"}

        try:
            async with websockets.connect(ws_url, additional_headers=headers) as ws:
                self.log("WebSocket connected", "OK")

                # Create Yjs document
                doc = pycrdt.Doc()
                text = doc.get("content", type=pycrdt.Text)

                # ── SYNC STEP 1: Send state vector ──
                self.log("Send Yjs sync step 1 (state vector)...")
                state_vector = doc.get_state()
                msg = bytes([MSG_SYNC, SYNC_STEP1]) + encode_bytes(state_vector)
                await ws.send(msg)
                self.log_verbose(f"  Sent {len(msg)} bytes")

                # ── Wait for messages from relay ──
                # Relay sends: SYNC_STEP1 (its state vector), then SYNC_STEP2 (its update)
                self.log("Wait for sync messages from relay...")
                got_step1 = False
                got_step2 = False
                try:
                    deadline = time.time() + 5.0
                    while time.time() < deadline:
                        remaining = deadline - time.time()
                        if remaining <= 0:
                            break
                        response = await asyncio.wait_for(ws.recv(), timeout=remaining)

                        if isinstance(response, str):
                            response = response.encode()

                        if len(response) < 2:
                            continue

                        msg_type = response[0]
                        msg_sub = response[1]

                        if msg_type == MSG_SYNC and msg_sub == SYNC_STEP1:
                            # Relay's sync step 1 - respond with our sync step 2
                            got_step1 = True
                            self.log_verbose("  Received relay sync step 1, sending step 2 response")
                            # Decode length-prefixed state vector from relay
                            remote_sv, _ = decode_bytes(response, 2)
                            update = doc.get_update(remote_sv)
                            reply = bytes([MSG_SYNC, SYNC_STEP2]) + encode_bytes(update)
                            await ws.send(reply)
                        elif msg_type == MSG_SYNC and msg_sub == SYNC_STEP2:
                            payload, _ = decode_bytes(response, 2)
                            doc.apply_update(payload)
                            got_step2 = True
                            self.log(
                                f"Received sync step 2: {len(payload)} bytes update", "OK"
                            )
                            self.log_verbose(f"  Document content: \"{str(text)}\"")

                        if got_step1 and got_step2:
                            break

                except asyncio.TimeoutError:
                    pass

                if got_step1 or got_step2:
                    self.log(f"Sync handshake complete (step1={got_step1}, step2={got_step2})", "OK")
                    return True, doc
                else:
                    self.log("No sync messages received (timeout)", "ERROR")
                    return False, None

        except websockets.exceptions.InvalidStatusCode as e:
            self.log(f"WebSocket connection failed: HTTP {e.status_code}", "ERROR")
            return False, None
        except Exception as e:
            self.log(f"WebSocket error: {e}", "ERROR")
            return False, None

    async def _complete_sync_handshake(
        self, ws, doc: pycrdt.Doc, timeout: float = 5.0
    ) -> bool:
        """Complete Yjs sync handshake: send step1, handle relay step1+step2."""
        state_vector = doc.get_state()
        await ws.send(bytes([MSG_SYNC, SYNC_STEP1]) + encode_bytes(state_vector))

        got_step1 = False
        got_step2 = False
        deadline = time.time() + timeout
        while time.time() < deadline:
            remaining = deadline - time.time()
            if remaining <= 0:
                break
            try:
                response = await asyncio.wait_for(ws.recv(), timeout=remaining)
            except asyncio.TimeoutError:
                break
            if isinstance(response, str):
                response = response.encode()
            if len(response) < 2:
                continue
            msg_type, msg_sub = response[0], response[1]
            if msg_type == MSG_SYNC and msg_sub == SYNC_STEP1:
                got_step1 = True
                remote_sv, _ = decode_bytes(response, 2)
                update = doc.get_update(remote_sv)
                reply = bytes([MSG_SYNC, SYNC_STEP2]) + encode_bytes(update)
                await ws.send(reply)
            elif msg_type == MSG_SYNC and msg_sub == SYNC_STEP2:
                payload, _ = decode_bytes(response, 2)
                doc.apply_update(payload)
                got_step2 = True
            if got_step1 and got_step2:
                break
        return got_step1 or got_step2

    async def test_document_update(
        self, relay_url: str, token: str, doc_id: str
    ) -> bool:
        """Test sending a document update and verifying it persists."""
        self.log(f"Test document update for doc {doc_id[:8]}...")

        ws_url = f"{relay_url}/{doc_id}"
        headers = {"Authorization": f"Bearer {token}"}

        try:
            # ── CONNECT 1: Send update ──
            async with websockets.connect(ws_url, additional_headers=headers) as ws:
                doc = pycrdt.Doc()
                text = doc.get("content", type=pycrdt.Text)

                # Complete handshake
                await self._complete_sync_handshake(ws, doc)

                # ── SEND UPDATE ──
                self.log("Insert text into document...")
                sv_before = doc.get_state()
                test_content = (
                    "# Test Document\n\n"
                    "This is a test document created by the plugin CRDT sync test.\n\n"
                    f"Timestamp: {int(time.time())}"
                )
                text += test_content
                update = doc.get_update(sv_before)

                msg = bytes([MSG_SYNC, SYNC_UPDATE]) + encode_bytes(update)
                await ws.send(msg)
                self.log(f"Sent document update: {len(update)} bytes", "OK")
                self.log_verbose(f"  Content: \"{test_content[:60]}...\"")

            # Give relay time to persist
            await asyncio.sleep(1.0)

            # ── CONNECT 2: Verify persistence ──
            self.log("Reconnect to verify persistence...")
            async with websockets.connect(ws_url, additional_headers=headers) as ws:
                doc2 = pycrdt.Doc()
                text2 = doc2.get("content", type=pycrdt.Text)

                # Complete handshake — should receive persisted data
                await self._complete_sync_handshake(ws, doc2)

                persisted_content = str(text2)
                match = persisted_content == test_content

                if match:
                    self.log(
                        f"Document persisted correctly: {len(persisted_content)} chars",
                        "OK",
                    )
                    self.log_verbose(f"  Content: \"{persisted_content[:60]}...\"")
                else:
                    self.log("Document content mismatch!", "ERROR")
                    self.log_verbose(f"  Expected: \"{test_content[:60]}...\"")
                    self.log_verbose(f"  Got:      \"{persisted_content[:60]}...\"")

                return match

        except asyncio.TimeoutError:
            self.log("Timeout waiting for sync response", "ERROR")
            return False
        except Exception as e:
            self.log(f"Document update test error: {e}", "ERROR")
            return False

    # ── Create Test Share ──────────────────────────────────
    def create_test_share(self) -> str | None:
        """Create a test folder share for testing."""
        self.log("Create test folder share...")

        with self._client() as client:
            try:
                ts = int(time.time())
                path = f"plugin-crdt-test-{ts}"

                response = client.post(
                    f"{self.config.server_url}/v1/shares",
                    json={
                        "kind": "folder",
                        "path": path,
                        "visibility": "private",
                    },
                    headers={"Authorization": f"Bearer {self.jwt_token}"},
                )

                if response.status_code not in (200, 201):
                    self.log(
                        f"Create share failed: {response.status_code} - {response.text}",
                        "ERROR",
                    )
                    return None

                share = response.json()
                share_id = share["id"]
                self.created_shares.append(share_id)

                self.log(f"Created share: {path} (id={share_id[:8]}...)", "OK")
                return share_id

            except Exception as e:
                self.log(f"Create share error: {e}", "ERROR")
                return None

    # ── Cleanup ────────────────────────────────────────────
    def cleanup(self):
        """Delete test shares created during testing."""
        if not self.created_shares:
            return

        self.log(f"Cleanup: delete {len(self.created_shares)} test shares...")

        with self._client() as client:
            for share_id in self.created_shares:
                try:
                    response = client.delete(
                        f"{self.config.server_url}/v1/shares/{share_id}",
                        headers={"Authorization": f"Bearer {self.jwt_token}"},
                    )

                    if response.status_code in (200, 204):
                        self.log_verbose(f"  Deleted share {share_id[:8]}...")
                    else:
                        self.log_verbose(
                            f"  Failed to delete {share_id[:8]}: {response.status_code}"
                        )

                except Exception as e:
                    self.log_verbose(f"  Delete error for {share_id[:8]}: {e}")

    # ── Run All Tests ──────────────────────────────────────
    async def run_all(self) -> bool:
        """Run all plugin CRDT sync flow tests."""
        print("\n" + "=" * 70)
        print("Plugin CRDT Sync Flow E2E Test")
        print("=" * 70)
        print(f"Server: {self.config.server_url}")
        print(f"User:   {self.config.email}")
        print("=" * 70)
        print()

        # ── Test 1: Login ──
        t0 = time.time()
        if not self.login():
            self.test_results.append(("Login", False, time.time() - t0))
            self.print_summary()
            return False
        self.test_results.append(("Login", True, time.time() - t0))
        print()

        # ── Test 2: List Shares ──
        t0 = time.time()
        shares = self.list_shares()
        self.test_results.append(("List Shares", len(shares) > 0, time.time() - t0))
        print()

        # ── Test 3: Create Test Share ──
        t0 = time.time()
        test_share_id = self.create_test_share()
        if not test_share_id:
            self.test_results.append(("Create Share", False, time.time() - t0))
            self.print_summary()
            return False
        self.test_results.append(("Create Share", True, time.time() - t0))
        print()

        # Use test share
        doc_id = test_share_id
        file_path = f"plugin-crdt-test-{int(time.time())}/test.md"

        # ── Test 4: Request Relay Token ──
        t0 = time.time()
        token_data = self.request_relay_token(test_share_id, doc_id, file_path)
        if not token_data:
            self.test_results.append(("Request Token", False, time.time() - t0))
            self.cleanup()
            self.print_summary()
            return False
        self.test_results.append(("Request Token", True, time.time() - t0))
        print()

        # ── Test 5: CRDT Sync Handshake ──
        t0 = time.time()
        success, yjs_doc = await self.test_crdt_sync_handshake(
            token_data["relay_url"], token_data["token"], doc_id
        )
        self.test_results.append(("CRDT Sync Handshake", success, time.time() - t0))
        if not success:
            self.cleanup()
            self.print_summary()
            return False
        print()

        # ── Test 6: Document Update & Persistence ──
        t0 = time.time()
        success = await self.test_document_update(
            token_data["relay_url"], token_data["token"], doc_id
        )
        self.test_results.append(("Document Update & Persistence", success, time.time() - t0))
        print()

        # ── Cleanup ──
        self.cleanup()

        # ── Summary ──
        self.print_summary()

        all_passed = all(ok for _, ok, _ in self.test_results)
        return all_passed

    def print_summary(self):
        """Print test results summary."""
        print()
        print("=" * 70)
        print("Summary")
        print("=" * 70)

        if not self.test_results:
            print("  No tests run")
            print("=" * 70)
            return

        for name, ok, duration in self.test_results:
            symbol = "✓" if ok else "✗"
            print(f"  {symbol} {name:<40} ({duration:.2f}s)")

        total = len(self.test_results)
        passed = sum(1 for _, ok, _ in self.test_results if ok)
        failed = total - passed

        print()
        print(f"Total:  {total}")
        print(f"Passed: {passed}")
        print(f"Failed: {failed}")

        if passed == total:
            print()
            print("✓ ALL TESTS PASSED!")
        else:
            print()
            print(f"✗ {failed} TEST(S) FAILED")

        print("=" * 70)


async def main():
    parser = argparse.ArgumentParser(
        description="Plugin CRDT Sync Flow E2E Test",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
This test simulates the exact flow that the Obsidian plugin performs:
1. Login to control plane
2. List shares
3. Request relay token for a share
4. Connect to relay server via WebSocket
5. Complete Yjs sync handshake
6. Send document updates
7. Verify persistence

Examples:
  python scripts/test_plugin_crdt_sync.py
  python scripts/test_plugin_crdt_sync.py --server https://cp.5evofarm.entire.vc
  python scripts/test_plugin_crdt_sync.py --email test@entire.vc --password Test123456
  python scripts/test_plugin_crdt_sync.py -v  # verbose mode
        """,
    )

    parser.add_argument(
        "--server",
        "-s",
        default="https://cp.5evofarm.entire.vc",
        help="Control plane URL (default: https://cp.5evofarm.entire.vc)",
    )
    parser.add_argument(
        "--email",
        "-e",
        default="test@entire.vc",
        help="User email (default: test@entire.vc)",
    )
    parser.add_argument(
        "--password",
        "-p",
        default="Test123456",
        help="User password (default: Test123456)",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Verbose output",
    )

    args = parser.parse_args()

    config = TestConfig(
        server_url=args.server.rstrip("/"),
        email=args.email,
        password=args.password,
        verbose=args.verbose,
    )

    tester = PluginCRDTSyncTester(config)
    success = await tester.run_all()

    sys.exit(0 if success else 1)


if __name__ == "__main__":
    asyncio.run(main())
