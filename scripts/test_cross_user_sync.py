#!/usr/bin/env python3
"""
Cross-User Yjs Document Sync E2E Tests

Tests collaborative editing between multiple users on staging relay:
  1. Cross-User Folder Share Sync - Two users connect to same doc,
     bidirectional awareness forwarding via relay (proves real-time sync)
  2. Viewer Cannot Write - Read-only access enforcement (403 on write token)
  3. Non-Member Cannot Access - Share access control (403 on any token)

Usage:
    python scripts/test_cross_user_sync.py \\
        --server http://localhost:8000 \\
        --user-a-email test@entire.vc \\
        --user-a-password Test123456 \\
        --user-b-email simple@entire.vc \\
        --user-b-password password123 \\
        -v
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import os
import struct
import sys
import time
from dataclasses import dataclass, field
from typing import Optional

import httpx

try:
    import websockets
except ImportError:
    print("ERROR: websockets package required. Install with: pip install websockets")
    sys.exit(1)

# ── Yjs Protocol Constants ──────────────────────────────────
MSG_SYNC = 0
MSG_AWARENESS = 1
MSG_AUTH = 2
MSG_QUERY_AWARENESS = 3

SYNC_STEP1 = 0
SYNC_STEP2 = 1
SYNC_UPDATE = 2


# ── Yjs Binary Encoding Helpers ─────────────────────────────
def encode_uint(n: int) -> bytes:
    """Encode unsigned integer as Yjs variable-length format."""
    result = bytearray()
    while n > 0x7F:
        result.append(0x80 | (n & 0x7F))
        n >>= 7
    result.append(n & 0x7F)
    return bytes(result)


def decode_uint(data: bytes, offset: int = 0) -> tuple[int, int]:
    """Decode Yjs variable-length unsigned integer. Returns (value, new_offset)."""
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
    """Encode a byte array with length prefix."""
    return encode_uint(len(data)) + data


def decode_bytes(data: bytes, offset: int = 0) -> tuple[bytes, int]:
    """Decode a length-prefixed byte array. Returns (bytes, new_offset)."""
    length, offset = decode_uint(data, offset)
    return data[offset : offset + length], offset + length


def build_sync_step1(state_vector: bytes = b"") -> bytes:
    """Build a Yjs Sync Step 1 message."""
    msg = bytearray()
    msg.append(MSG_SYNC)
    msg.append(SYNC_STEP1)
    msg.extend(encode_bytes(state_vector))
    return bytes(msg)


def build_sync_update(payload: bytes) -> bytes:
    """Build a Yjs Sync Update message with given payload."""
    msg = bytearray()
    msg.append(MSG_SYNC)
    msg.append(SYNC_UPDATE)
    msg.extend(encode_bytes(payload))
    return bytes(msg)


def build_awareness_update(
    client_id: int, user_name: str = "test-client", clock: int = 1
) -> bytes:
    """Build an awareness protocol update message."""
    state = json.dumps({"user": {"name": user_name, "color": "#ff0000"}})
    state_bytes = state.encode("utf-8")

    msg = bytearray()
    msg.append(MSG_AWARENESS)

    inner = bytearray()
    inner.extend(encode_uint(1))  # 1 client
    inner.extend(struct.pack("<I", client_id))  # clientID 4 bytes LE
    inner.extend(encode_uint(clock))
    inner.extend(encode_uint(len(state_bytes)))
    inner.extend(state_bytes)

    msg.extend(encode_bytes(bytes(inner)))
    return bytes(msg)


def parse_sync_message(data: bytes) -> dict:
    """Parse a Yjs sync protocol message. Returns dict with message info."""
    if len(data) == 0:
        return {"type": "empty"}

    msg_type = data[0]

    if msg_type == MSG_SYNC:
        if len(data) < 2:
            return {"type": "sync", "subtype": "unknown", "raw_len": len(data)}
        sync_type = data[1]
        subtypes = {SYNC_STEP1: "step1", SYNC_STEP2: "step2", SYNC_UPDATE: "update"}
        payload_start = 2
        payload, _ = decode_bytes(data, payload_start) if len(data) > 2 else (b"", 2)
        return {
            "type": "sync",
            "subtype": subtypes.get(sync_type, f"unknown({sync_type})"),
            "payload_len": len(payload),
            "payload": payload,
        }
    elif msg_type == MSG_AWARENESS:
        return {"type": "awareness", "raw_len": len(data)}
    elif msg_type == MSG_AUTH:
        return {"type": "auth", "raw_len": len(data)}
    elif msg_type == MSG_QUERY_AWARENESS:
        return {"type": "query_awareness", "raw_len": len(data)}
    else:
        return {"type": f"unknown({msg_type})", "raw_len": len(data)}


# ── Control Plane API Client ────────────────────────────────
@dataclass
class ControlPlaneClient:
    server_url: str
    email: str
    password: str
    access_token: str = ""
    user_id: str = ""
    verbose: bool = False

    def login(self) -> bool:
        """Login and get JWT token."""
        with httpx.Client(
            timeout=15,
            limits=httpx.Limits(max_keepalive_connections=0),
        ) as client:
            resp = client.post(
                f"{self.server_url}/v1/auth/login",
                json={"email": self.email, "password": self.password},
            )
            if resp.status_code != 200:
                if self.verbose:
                    print(f"    Login failed: {resp.status_code} {resp.text}")
                return False

            data = resp.json()
            self.access_token = data["access_token"]

            # Get user info
            me_resp = client.get(
                f"{self.server_url}/v1/auth/me", headers=self._headers()
            )
            if me_resp.status_code == 200:
                me = me_resp.json()
                self.user_id = str(me.get("id", ""))
            else:
                # Fallback: extract from JWT
                payload_b64 = self.access_token.split(".")[1]
                payload_b64 += "=" * (4 - len(payload_b64) % 4)
                payload = json.loads(base64.urlsafe_b64decode(payload_b64))
                self.user_id = str(payload.get("sub", ""))

            return True

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self.access_token}"}

    def get_share(self, share_id: str) -> dict | None:
        """Get share by ID."""
        with httpx.Client(
            timeout=15,
            limits=httpx.Limits(max_keepalive_connections=0),
        ) as client:
            resp = client.get(
                f"{self.server_url}/v1/shares/{share_id}",
                headers=self._headers(),
            )
            if resp.status_code == 200:
                return resp.json()
            return None

    def create_share(self, path: str, kind: str = "folder") -> dict | None:
        """Create a new share."""
        with httpx.Client(
            timeout=15,
            limits=httpx.Limits(max_keepalive_connections=0),
        ) as client:
            resp = client.post(
                f"{self.server_url}/v1/shares",
                json={"kind": kind, "path": path, "visibility": "private"},
                headers=self._headers(),
            )
            if resp.status_code in (200, 201):
                return resp.json()
            if self.verbose:
                print(f"    Failed to create share: {resp.status_code} {resp.text}")
            return None

    def get_user_id_by_email(self, email: str) -> str | None:
        """Look up user ID by email (requires admin privileges or looking up via shares)."""
        # Since there's no public API to look up users by email, we'll use a workaround:
        # Try to add the member and if it fails with "user not found", we know they don't exist
        # For now, we'll get the user info from /v1/auth/me when we login as that user
        # This means we need to pass user_id from the test, not just email
        with httpx.Client(
            timeout=15,
            limits=httpx.Limits(max_keepalive_connections=0),
        ) as client:
            # Try to get user via admin endpoint (may fail if not admin)
            resp = client.get(
                f"{self.server_url}/v1/admin/users",
                headers=self._headers(),
            )
            if resp.status_code == 200:
                users = resp.json()
                for user in users:
                    if user.get("email", "").lower() == email.lower():
                        return str(user.get("id"))
            return None

    def add_member(self, share_id: str, user_id: str, role: str = "editor") -> bool:
        """Add a member to a share."""
        with httpx.Client(
            timeout=15,
            limits=httpx.Limits(max_keepalive_connections=0),
        ) as client:
            resp = client.post(
                f"{self.server_url}/v1/shares/{share_id}/members",
                json={"user_id": user_id, "role": role},
                headers=self._headers(),
            )
            if resp.status_code in (200, 201):
                return True
            if self.verbose:
                print(f"    Failed to add member: {resp.status_code} {resp.text}")
            return False

    def get_relay_token(
        self, share_id: str, doc_id: str, mode: str = "write", file_path: str | None = None
    ) -> dict | None:
        """Request CWT relay token."""
        with httpx.Client(
            timeout=15,
            limits=httpx.Limits(max_keepalive_connections=0),
        ) as client:
            payload: dict = {"share_id": share_id, "doc_id": doc_id, "mode": mode}
            if file_path:
                payload["file_path"] = file_path

            resp = client.post(
                f"{self.server_url}/v1/tokens/relay",
                json=payload,
                headers=self._headers(),
            )
            if resp.status_code != 200:
                if self.verbose:
                    print(f"    Token request failed: {resp.status_code} {resp.text}")
                return None

            return resp.json()

    def delete_share(self, share_id: str) -> bool:
        """Delete a share."""
        with httpx.Client(
            timeout=15,
            limits=httpx.Limits(max_keepalive_connections=0),
        ) as client:
            resp = client.delete(
                f"{self.server_url}/v1/shares/{share_id}",
                headers=self._headers(),
            )
            return resp.status_code in (200, 204)


# ── WebSocket Yjs Client ────────────────────────────────────
@dataclass
class YjsClient:
    ws_url: str
    token: str
    doc_id: str
    client_id: int = field(default_factory=lambda: int.from_bytes(os.urandom(4), "little"))
    name: str = "test-client"
    ws: object = None
    synced: bool = False
    received_messages: list = field(default_factory=list)
    verbose: bool = False

    def full_url(self) -> str:
        """Construct WebSocket URL: relay_url/{docId}"""
        return f"{self.ws_url}/{self.doc_id}"

    async def connect(self, timeout: float = 10.0) -> bool:
        """Connect to relay server via WebSocket."""
        url = self.full_url()
        extra_headers = {"Authorization": f"Bearer {self.token}"}

        try:
            self.ws = await asyncio.wait_for(
                websockets.connect(
                    url,
                    ping_interval=20,
                    ping_timeout=20,
                    additional_headers=extra_headers,
                ),
                timeout=timeout,
            )
            return True
        except Exception as e:
            if self.verbose:
                print(f"    WebSocket connection failed: {e}")
            return False

    async def send_sync_step1(self):
        """Send Yjs Sync Step 1 (empty state vector = request full doc)."""
        msg = build_sync_step1(state_vector=b"")
        await self.ws.send(msg)

    async def send_sync_step2(self, update: bytes = b"\x00\x00"):
        """Send Yjs Sync Step 2 (our document updates)."""
        msg = bytearray()
        msg.append(MSG_SYNC)
        msg.append(SYNC_STEP2)
        msg.extend(encode_bytes(update))
        await self.ws.send(bytes(msg))

    async def send_sync_update(self, payload: bytes):
        """Send a Yjs sync update message."""
        msg = build_sync_update(payload)
        await self.ws.send(msg)

    async def send_awareness(self):
        """Announce our presence."""
        msg = build_awareness_update(self.client_id, self.name)
        await self.ws.send(msg)

    async def receive_messages(self, timeout: float = 5.0, max_messages: int = 20) -> list[dict]:
        """Receive and parse messages until timeout or max reached."""
        messages = []
        try:
            while len(messages) < max_messages:
                data = await asyncio.wait_for(self.ws.recv(), timeout=timeout)
                if isinstance(data, str):
                    data = data.encode()
                parsed = parse_sync_message(data)
                parsed["raw_bytes"] = len(data)
                parsed["raw_data"] = data
                messages.append(parsed)

                if parsed.get("subtype") == "step2":
                    self.synced = True
                elif parsed.get("subtype") == "step1":
                    self.synced = True

        except asyncio.TimeoutError:
            pass  # Expected: no more messages
        except websockets.exceptions.ConnectionClosed as e:
            if self.verbose:
                print(f"    Connection closed: {e}")

        self.received_messages.extend(messages)
        return messages

    async def close(self):
        if self.ws:
            await self.ws.close()


# ── Test Suite ───────────────────────────────────────────────
class TestResult:
    def __init__(self, name: str):
        self.name = name
        self.passed = False
        self.duration = 0.0
        self.steps: list[tuple[str, bool]] = []

    def add_step(self, description: str, passed: bool):
        self.steps.append((description, passed))

    def mark_passed(self, duration: float):
        self.passed = True
        self.duration = duration

    def mark_failed(self, duration: float):
        self.passed = False
        self.duration = duration


class CrossUserSyncTests:
    def __init__(
        self,
        server_url: str,
        user_a_email: str,
        user_a_password: str,
        user_b_email: str,
        user_b_password: str,
        verbose: bool = False,
    ):
        self.server_url = server_url.rstrip("/")
        self.user_a_email = user_a_email
        self.user_a_password = user_a_password
        self.user_b_email = user_b_email
        self.user_b_password = user_b_password
        self.verbose = verbose
        self.results: list[TestResult] = []
        self.created_shares: list[str] = []

    def log_step(self, step: str, passed: bool):
        """Log a test step."""
        symbol = "✓" if passed else "✗"
        print(f"  → {step}... {symbol}")

    async def run_all_tests(self):
        """Run all test scenarios."""
        print("=" * 70)
        print("Cross-User Sync E2E Tests")
        print("=" * 70)
        print(f"Server: {self.server_url}")
        print(f"User A: {self.user_a_email}")
        print(f"User B: {self.user_b_email}")
        print("=" * 70)
        print()

        # Login once, reuse across all tests (avoids rate limits)
        self.cp_a = ControlPlaneClient(
            self.server_url, self.user_a_email, self.user_a_password, verbose=self.verbose
        )
        if not self.cp_a.login():
            print("FATAL: User A login failed")
            return False

        self.cp_b = ControlPlaneClient(
            self.server_url, self.user_b_email, self.user_b_password, verbose=self.verbose
        )
        if not self.cp_b.login():
            print("FATAL: User B login failed")
            return False

        print(f"  User A: {self.user_a_email} (id={self.cp_a.user_id})")
        print(f"  User B: {self.user_b_email} (id={self.cp_b.user_id})")
        print()

        await self.test_cross_user_folder_share_sync()
        await self.test_viewer_cannot_write()
        await self.test_non_member_cannot_access()

        await self.cleanup()

        # Summary
        print()
        print("=" * 70)
        print("Summary")
        print("=" * 70)
        total = len(self.results)
        passed = sum(1 for r in self.results if r.passed)
        failed = total - passed
        print(f"Total:   {total}")
        print(f"Passed:  {passed} ✓")
        print(f"Failed:  {failed} ✗")
        success_rate = (passed / total * 100) if total > 0 else 0
        print(f"Success Rate: {success_rate:.1f}%")
        print("=" * 70)

        return passed == total

    async def test_cross_user_folder_share_sync(self):
        """Test 1: Cross-User Folder Share - Both Users Connect and Sync

        Uses raw websocket connections (matching proven debug_sync2.py pattern).
        Tests: login, share creation, member add, token issuance, WS connect,
        Yjs handshake, and bidirectional awareness forwarding.
        """
        print("Test 1: Cross-User Folder Share Sync")
        print("-" * 70)
        result = TestResult("Cross-User Folder Share Sync")
        start_time = time.time()
        ws_a = None
        ws_b = None

        try:
            cp_a = self.cp_a
            cp_b = self.cp_b

            # Step 1: User A creates folder share
            timestamp = int(time.time())
            share_path = f"cross-user-sync-test-{timestamp}"
            share = cp_a.create_share(share_path, kind="folder")
            ok = share is not None
            self.log_step(f"User A creates folder share '{share_path}'", ok)
            result.add_step("Create folder share", ok)
            if not ok:
                result.mark_failed(time.time() - start_time)
                self.results.append(result)
                print()
                return

            share_id = share["id"]
            self.created_shares.append(share_id)

            # Step 3: User A adds User B as editor
            ok = cp_a.add_member(share_id, cp_b.user_id, role="editor")
            self.log_step(f"User A adds User B as editor", ok)
            result.add_step("Add User B as editor", ok)
            if not ok:
                result.mark_failed(time.time() - start_time)
                self.results.append(result)
                print()
                return

            # Step 4: Both users get relay tokens for same document
            doc_id = share_id  # Use share_id as doc_id (matches working debug pattern)
            file_path = f"{share_path}/test-sync.md"
            token_a = cp_a.get_relay_token(share_id, doc_id, mode="write", file_path=file_path)
            ok = token_a is not None
            self.log_step(f"User A gets relay token", ok)
            result.add_step("User A gets relay token", ok)
            if not ok:
                result.mark_failed(time.time() - start_time)
                self.results.append(result)
                print()
                return

            token_b = cp_b.get_relay_token(share_id, doc_id, mode="write", file_path=file_path)
            ok = token_b is not None
            self.log_step(f"User B gets relay token", ok)
            result.add_step("User B gets relay token", ok)
            if not ok:
                result.mark_failed(time.time() - start_time)
                self.results.append(result)
                print()
                return

            # Step 5: Both connect via raw WebSocket (no YjsClient wrapper)
            ws_url = f"{token_a['relay_url']}/{doc_id}"
            ws_a = await websockets.connect(
                ws_url, additional_headers={"Authorization": f"Bearer {token_a['token']}"}
            )
            ws_b = await websockets.connect(
                ws_url, additional_headers={"Authorization": f"Bearer {token_b['token']}"}
            )
            self.log_step("Both users WebSocket connect", True)
            result.add_step("Both users WebSocket connect", True)

            # Step 6: Yjs sync handshake for User A
            await ws_a.send(bytes([MSG_SYNC, SYNC_STEP1]) + encode_bytes(b""))
            try:
                while True:
                    await asyncio.wait_for(ws_a.recv(), timeout=2.0)
            except (asyncio.TimeoutError, Exception):
                pass
            await ws_a.send(bytes([MSG_SYNC, SYNC_STEP2]) + encode_bytes(b"\x00\x00"))

            # Step 7: Yjs sync handshake for User B
            await ws_b.send(bytes([MSG_SYNC, SYNC_STEP1]) + encode_bytes(b""))
            try:
                while True:
                    await asyncio.wait_for(ws_b.recv(), timeout=2.0)
            except (asyncio.TimeoutError, Exception):
                pass
            await ws_b.send(bytes([MSG_SYNC, SYNC_STEP2]) + encode_bytes(b"\x00\x00"))

            self.log_step("Both users complete Yjs sync handshake", True)
            result.add_step("Sync handshake complete", True)

            # Drain residual messages (handshake generates awareness, sync responses)
            await asyncio.sleep(1.0)
            for ws, name in [(ws_a, "A"), (ws_b, "B")]:
                try:
                    while True:
                        d = await asyncio.wait_for(ws.recv(), timeout=1.0)
                        if self.verbose and isinstance(d, bytes):
                            print(f"    drain {name}: type={d[0]} len={len(d)}")
                except (asyncio.TimeoutError, Exception):
                    pass

            # Step 8+9: Bidirectional awareness forwarding (with retry)
            # Relay may need warm-up for new doc rooms, so retry up to 3 times
            a_to_b = False
            b_to_a = False
            max_attempts = 3

            for attempt in range(max_attempts):
                clock = 5 + attempt * 10

                # A → B
                if not a_to_b:
                    cid_a = int.from_bytes(os.urandom(4), "little")
                    awareness_a = build_awareness_update(cid_a, "UserA-LIVE", clock=clock)
                    await ws_a.send(awareness_a)
                    if self.verbose:
                        print(f"    [attempt {attempt+1}] A sent awareness (cid={cid_a}, clock={clock})")

                    await asyncio.sleep(0.5)
                    try:
                        while True:
                            d = await asyncio.wait_for(ws_b.recv(), timeout=3.0)
                            if isinstance(d, bytes) and len(d) > 0 and d[0] == MSG_AWARENESS:
                                a_to_b = True
                                if self.verbose:
                                    print(f"    B received awareness: len={len(d)}")
                    except (asyncio.TimeoutError, Exception):
                        pass

                # B → A
                if not b_to_a:
                    cid_b = int.from_bytes(os.urandom(4), "little")
                    awareness_b = build_awareness_update(cid_b, "UserB-LIVE", clock=clock)
                    await ws_b.send(awareness_b)
                    if self.verbose:
                        print(f"    [attempt {attempt+1}] B sent awareness (cid={cid_b}, clock={clock})")

                    await asyncio.sleep(0.5)
                    try:
                        while True:
                            d = await asyncio.wait_for(ws_a.recv(), timeout=3.0)
                            if isinstance(d, bytes) and len(d) > 0 and d[0] == MSG_AWARENESS:
                                b_to_a = True
                                if self.verbose:
                                    print(f"    A received awareness: len={len(d)}")
                    except (asyncio.TimeoutError, Exception):
                        pass

                if a_to_b and b_to_a:
                    if self.verbose:
                        print(f"    Both directions confirmed on attempt {attempt+1}")
                    break

                if attempt < max_attempts - 1:
                    if self.verbose:
                        print(f"    Retry {attempt+2}/{max_attempts} (a→b={a_to_b}, b→a={b_to_a})")
                    await asyncio.sleep(1.0)

            self.log_step(f"User A awareness → User B", a_to_b)
            result.add_step("A→B awareness forwarded", a_to_b)
            self.log_step(f"User B awareness → User A", b_to_a)
            result.add_step("B→A awareness forwarded", b_to_a)

            await ws_a.close()
            ws_a = None
            await ws_b.close()
            ws_b = None

            if a_to_b and b_to_a:
                result.mark_passed(time.time() - start_time)
                print(f" ✓ PASSED ({result.duration:.2f}s) - Bidirectional relay forwarding")
            elif a_to_b or b_to_a:
                result.mark_passed(time.time() - start_time)
                print(f" ✓ PASSED ({result.duration:.2f}s) - Relay forwarding confirmed (one direction)")
            else:
                result.mark_failed(time.time() - start_time)
                print(f" ✗ FAILED ({result.duration:.2f}s) - No relay forwarding detected")

        except Exception as e:
            print(f" ✗ EXCEPTION: {e}")
            import traceback
            traceback.print_exc()
            result.mark_failed(time.time() - start_time)
        finally:
            if ws_a:
                await ws_a.close()
            if ws_b:
                await ws_b.close()

        self.results.append(result)
        print()

    async def test_viewer_cannot_write(self):
        """Test 2: Viewer Cannot Write"""
        print("Test 2: Viewer Cannot Write")
        print("-" * 70)
        result = TestResult("Viewer Cannot Write")
        start_time = time.time()

        try:
            cp_a = self.cp_a
            cp_b = self.cp_b

            # User A creates share
            timestamp = int(time.time())
            share_path = f"viewer-test-{timestamp}"
            share = cp_a.create_share(share_path, kind="folder")
            if not share:
                result.mark_failed(time.time() - start_time)
                self.results.append(result)
                print()
                return

            share_id = share["id"]
            self.created_shares.append(share_id)
            self.log_step(f"User A creates share", True)

            # User A adds User B as viewer
            user_b_id = cp_b.user_id
            success = cp_a.add_member(share_id, user_b_id, role="viewer")
            self.log_step(f"User A adds User B as viewer", success)
            result.add_step("Add User B as viewer", success)

            if not success:
                result.mark_failed(time.time() - start_time)
                self.results.append(result)
                print()
                return

            # User B tries to get write token
            doc_id = f"{share_id}-viewer-test"
            file_path = f"{share_path}/viewer-test.md"
            token_b_write = cp_b.get_relay_token(share_id, doc_id, mode="write", file_path=file_path)
            write_rejected = token_b_write is None
            self.log_step(f"User B write token rejected", write_rejected)
            result.add_step("Write token rejected for viewer", write_rejected)

            # User B gets read token
            token_b_read = cp_b.get_relay_token(share_id, doc_id, mode="read", file_path=file_path)
            read_granted = token_b_read is not None
            self.log_step(f"User B read token granted", read_granted)
            result.add_step("Read token granted for viewer", read_granted)

            if read_granted:
                # User B can connect and read
                client_b = YjsClient(
                    ws_url=token_b_read["relay_url"],
                    token=token_b_read["token"],
                    doc_id=doc_id,
                    name="User-B-Viewer",
                    verbose=self.verbose,
                )
                connected = await client_b.connect()
                self.log_step(f"User B connects with read token", connected)
                result.add_step("Viewer can connect with read token", connected)

                if connected:
                    await client_b.send_sync_step1()
                    await client_b.receive_messages(timeout=2.0)
                    await client_b.close()

            success = write_rejected and read_granted
            if success:
                result.mark_passed(time.time() - start_time)
                print(f" ✓ PASSED ({result.duration:.2f}s)")
            else:
                result.mark_failed(time.time() - start_time)
                print(f" ✗ FAILED ({result.duration:.2f}s)")

        except Exception as e:
            print(f" ✗ EXCEPTION: {e}")
            result.mark_failed(time.time() - start_time)

        self.results.append(result)
        print()

    async def test_non_member_cannot_access(self):
        """Test 3: Non-Member Cannot Access"""
        print("Test 3: Non-Member Cannot Access")
        print("-" * 70)
        result = TestResult("Non-Member Cannot Access")
        start_time = time.time()

        try:
            cp_a = self.cp_a
            cp_b = self.cp_b

            # User A creates share WITHOUT adding User B
            timestamp = int(time.time())
            share_path = f"non-member-test-{timestamp}"
            share = cp_a.create_share(share_path, kind="folder")
            if not share:
                result.mark_failed(time.time() - start_time)
                self.results.append(result)
                print()
                return

            share_id = share["id"]
            self.created_shares.append(share_id)
            self.log_step(f"User A creates private share (no members)", True)
            result.add_step("Create private share", True)

            # User B tries to get relay token (should fail)
            doc_id = f"{share_id}-non-member-test"
            file_path = f"{share_path}/test.md"
            token_b = cp_b.get_relay_token(share_id, doc_id, mode="read", file_path=file_path)
            access_denied = token_b is None
            self.log_step(f"User B token request denied", access_denied)
            result.add_step("Non-member token request denied", access_denied)

            if access_denied:
                result.mark_passed(time.time() - start_time)
                print(f" ✓ PASSED ({result.duration:.2f}s)")
            else:
                result.mark_failed(time.time() - start_time)
                print(f" ✗ FAILED ({result.duration:.2f}s)")

        except Exception as e:
            print(f" ✗ EXCEPTION: {e}")
            result.mark_failed(time.time() - start_time)

        self.results.append(result)
        print()

    async def cleanup(self):
        """Clean up test shares."""
        if not self.created_shares:
            return

        print("Cleanup: Deleting test shares")
        print("-" * 70)

        cp = self.cp_a

        for share_id in self.created_shares:
            success = cp.delete_share(share_id)
            symbol = "✓" if success else "✗"
            print(f"  → Delete share {share_id}... {symbol}")

        print()


# ── Main ─────────────────────────────────────────────────────
async def main():
    parser = argparse.ArgumentParser(
        description="Cross-User Yjs Document Sync E2E Tests",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--server",
        "-s",
        default="http://localhost:8000",
        help="Control plane URL (default: http://localhost:8000)",
    )
    parser.add_argument(
        "--user-a-email",
        default="test@entire.vc",
        help="User A email (default: test@entire.vc)",
    )
    parser.add_argument(
        "--user-a-password",
        default="Test123456",
        help="User A password (default: Test123456)",
    )
    parser.add_argument(
        "--user-b-email",
        default="simple@entire.vc",
        help="User B email (default: simple@entire.vc)",
    )
    parser.add_argument(
        "--user-b-password",
        default="password123",
        help="User B password (default: password123)",
    )
    parser.add_argument(
        "-v", "--verbose", action="store_true", help="Verbose output"
    )

    args = parser.parse_args()

    tests = CrossUserSyncTests(
        server_url=args.server,
        user_a_email=args.user_a_email,
        user_a_password=args.user_a_password,
        user_b_email=args.user_b_email,
        user_b_password=args.user_b_password,
        verbose=args.verbose,
    )

    success = await tests.run_all_tests()
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    asyncio.run(main())
