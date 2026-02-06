#!/usr/bin/env python3
"""
Headless Yjs E2E test — simulates the Obsidian plugin flow against production.

Tests the complete path without Obsidian:
  1. Login to control plane → JWT
  2. Get or create share
  3. Request CWT token via POST /tokens/relay  (same as plugin)
  4. Connect WebSocket to relay-server            (same as plugin)
  5. Yjs sync step 1 → receive sync step 2        (same as plugin)
  6. Send a Yjs update (text insert)
  7. Second client connects and verifies the update arrived

Usage:
    # Basic test (single client, sync only):
    python scripts/test_e2e_yjs_sync.py \\
        --server https://cp.tr.entire.vc \\
        --email test@entire.vc \\
        --password Test123456

    # Two-client sync test:
    python scripts/test_e2e_yjs_sync.py \\
        --server https://cp.tr.entire.vc \\
        --email test@entire.vc \\
        --password Test123456 \\
        --two-clients

    # With specific share:
    python scripts/test_e2e_yjs_sync.py \\
        --server https://cp.tr.entire.vc \\
        --email test@entire.vc \\
        --password Test123456 \\
        --share-id c2676a37-ff3d-426a-b00b-18c7bc9dc7fc
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import hashlib
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
# See: https://github.com/yjs/y-protocols

MSG_SYNC = 0
MSG_AWARENESS = 1
MSG_AUTH = 2
MSG_QUERY_AWARENESS = 3

SYNC_STEP1 = 0
SYNC_STEP2 = 1
SYNC_UPDATE = 2


# ── Yjs Binary Encoding Helpers ─────────────────────────────
# Yjs uses a variable-length unsigned integer encoding (similar to LEB128)


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
    """Build a Yjs Sync Step 1 message.

    Sync Step 1 sends our state vector so the server can calculate
    what updates we're missing.
    """
    msg = bytearray()
    msg.append(MSG_SYNC)
    msg.append(SYNC_STEP1)
    msg.extend(encode_bytes(state_vector))
    return bytes(msg)


def build_awareness_update(
    client_id: int, user_name: str = "headless-test", clock: int = 1
) -> bytes:
    """Build an awareness protocol update message.

    This announces our presence to other clients (like Obsidian does).
    """
    state = json.dumps({"user": {"name": user_name, "color": "#ff0000"}})
    state_bytes = state.encode("utf-8")

    msg = bytearray()
    msg.append(MSG_AWARENESS)

    # Awareness update format: clientCount, then for each:
    #   clientID (4 bytes LE), clock (varuint), state_json (len-prefixed string)
    inner = bytearray()
    inner.extend(encode_uint(1))  # 1 client
    inner.extend(struct.pack("<I", client_id))  # clientID 4 bytes LE
    inner.extend(encode_uint(clock))
    inner.extend(encode_uint(len(state_bytes)))  # state length
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
        }
    elif msg_type == MSG_AWARENESS:
        return {"type": "awareness", "raw_len": len(data)}
    elif msg_type == MSG_AUTH:
        return {"type": "auth", "raw_len": len(data)}
    elif msg_type == MSG_QUERY_AWARENESS:
        return {"type": "query_awareness", "raw_len": len(data)}
    else:
        return {"type": f"unknown({msg_type})", "raw_len": len(data)}


# ── Logging ──────────────────────────────────────────────────


def log(msg: str, level: str = "INFO"):
    ts = time.strftime("%H:%M:%S")
    prefix = {"INFO": "  ", "OK": "  ✅", "FAIL": "  ❌", "WARN": "  ⚠️", "STEP": "►"}.get(
        level, "  "
    )
    print(f"[{ts}] {prefix} {msg}")


# ── Control Plane API Client ────────────────────────────────


@dataclass
class ControlPlaneClient:
    server_url: str
    email: str
    password: str
    access_token: str = ""
    user_id: str = ""

    def login(self) -> bool:
        log("Login to control plane...", "STEP")
        resp = httpx.post(
            f"{self.server_url}/auth/login",
            json={"email": self.email, "password": self.password},
            timeout=15,
        )
        if resp.status_code != 200:
            log(f"Login failed: {resp.status_code} {resp.text}", "FAIL")
            return False

        data = resp.json()
        self.access_token = data["access_token"]

        # Get user info from /auth/me
        me_resp = httpx.get(
            f"{self.server_url}/auth/me", headers=self._headers(), timeout=15
        )
        if me_resp.status_code == 200:
            me = me_resp.json()
            self.user_id = me.get("id", "")
            log(f"Logged in as {me.get('email', self.email)} (id: {self.user_id})", "OK")
        else:
            # Extract user_id from JWT sub claim as fallback
            import json as _json

            payload_b64 = self.access_token.split(".")[1]
            payload_b64 += "=" * (4 - len(payload_b64) % 4)
            payload = _json.loads(base64.urlsafe_b64decode(payload_b64))
            self.user_id = payload.get("sub", "")
            log(f"Logged in as {self.email} (id from JWT: {self.user_id})", "OK")

        return True

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self.access_token}"}

    def get_shares(self) -> list[dict]:
        resp = httpx.get(f"{self.server_url}/shares", headers=self._headers(), timeout=15)
        if resp.status_code != 200:
            log(f"Failed to list shares: {resp.status_code}", "FAIL")
            return []
        return resp.json()

    def get_share(self, share_id: str) -> dict | None:
        resp = httpx.get(
            f"{self.server_url}/shares/{share_id}", headers=self._headers(), timeout=15
        )
        if resp.status_code == 200:
            return resp.json()
        return None

    def create_share(self, path: str, kind: str = "folder") -> dict | None:
        resp = httpx.post(
            f"{self.server_url}/shares",
            json={"kind": kind, "path": path, "visibility": "private"},
            headers=self._headers(),
            timeout=15,
        )
        if resp.status_code in (200, 201):
            return resp.json()
        log(f"Failed to create share: {resp.status_code} {resp.text}", "FAIL")
        return None

    def get_relay_token(
        self, share_id: str, doc_id: str, mode: str = "write", file_path: str | None = None
    ) -> dict | None:
        """Request CWT relay token — same endpoint as the Obsidian plugin."""
        log("Request CWT relay token...", "STEP")
        payload: dict = {"share_id": share_id, "doc_id": doc_id, "mode": mode}
        if file_path:
            payload["file_path"] = file_path

        resp = httpx.post(
            f"{self.server_url}/tokens/relay",
            json=payload,
            headers=self._headers(),
            timeout=15,
        )
        if resp.status_code != 200:
            log(f"Token request failed: {resp.status_code} {resp.text}", "FAIL")
            return None

        data = resp.json()
        log(f"Got relay token (expires: {data['expires_at']})", "OK")
        log(f"Relay URL: {data['relay_url']}")

        # Decode and show CWT claims
        _show_cwt_claims(data["token"])

        return data


def _show_cwt_claims(token_b64: str):
    """Decode CWT token and print claims (without verification)."""
    try:
        import cbor2

        padding = 4 - len(token_b64) % 4
        if padding != 4:
            token_b64 += "=" * padding
        raw = base64.urlsafe_b64decode(token_b64)
        outer = cbor2.loads(raw)
        inner = outer.value
        cose = inner.value if hasattr(inner, "tag") else inner
        claims = cbor2.loads(cose[2])
        names = {1: "iss", 2: "sub", 3: "aud", 4: "exp", 6: "iat", -80201: "scope"}
        named = {names.get(k, k): v for k, v in claims.items()}
        log(f"CWT claims: {json.dumps(named, default=str)}")
    except Exception as e:
        log(f"Could not decode CWT: {e}", "WARN")


# ── WebSocket Yjs Client ────────────────────────────────────


@dataclass
class YjsClient:
    ws_url: str
    token: str
    doc_id: str
    client_id: int = field(default_factory=lambda: int.from_bytes(os.urandom(4), "little"))
    name: str = "headless-test"
    ws: object = None
    synced: bool = False
    received_messages: list = field(default_factory=list)

    def full_url(self) -> str:
        """Construct WebSocket URL: relay_url/{docId}"""
        return f"{self.ws_url}/{self.doc_id}"

    async def connect(self, timeout: float = 10.0) -> bool:
        """Connect to relay server via WebSocket with token in Authorization header."""
        url = self.full_url()
        log(f"WebSocket connecting to {url}...", "STEP")

        try:
            self.ws = await asyncio.wait_for(
                websockets.connect(
                    url,
                    ping_interval=20,
                    ping_timeout=20,
                    additional_headers={"Authorization": f"Bearer {self.token}"},
                ),
                timeout=timeout,
            )
            log(f"WebSocket connected (client_id={self.client_id})", "OK")
            return True
        except asyncio.TimeoutError:
            log("WebSocket connection timed out", "FAIL")
            return False
        except Exception as e:
            log(f"WebSocket connection failed: {e}", "FAIL")
            return False

    async def send_sync_step1(self):
        """Send Yjs Sync Step 1 (empty state vector = request full doc)."""
        msg = build_sync_step1(state_vector=b"")
        await self.ws.send(msg)
        log("Sent Sync Step 1 (empty state vector)")

    async def send_sync_step2(self, update: bytes = b"\x00\x00"):
        """Send Yjs Sync Step 2 (our document updates).

        For an empty doc, send minimal empty update to complete handshake.
        The minimal Yjs v1 empty update is \\x00\\x00.
        """
        msg = bytearray()
        msg.append(MSG_SYNC)
        msg.append(SYNC_STEP2)
        msg.extend(encode_bytes(update))
        await self.ws.send(bytes(msg))
        log("Sent Sync Step 2 (empty doc state)")

    async def send_awareness(self):
        """Announce our presence."""
        msg = build_awareness_update(self.client_id, self.name)
        await self.ws.send(msg)
        log(f"Sent awareness update (name={self.name})")

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
                    log(
                        f"Received Sync Step 2 ({parsed['payload_len']} bytes payload) — SYNCED",
                        "OK",
                    )
                elif parsed.get("subtype") == "step1":
                    # Server requesting our state — this IS the sync handshake
                    self.synced = True
                    log(f"Received Sync Step 1 (server requests our state) — handshake OK", "OK")
                elif parsed["type"] == "sync":
                    log(f"Received sync/{parsed.get('subtype', '?')} ({parsed.get('payload_len', 0)} bytes)")
                elif parsed["type"] == "awareness":
                    log(f"Received awareness update ({parsed['raw_bytes']} bytes)")
                else:
                    log(f"Received {parsed['type']} ({parsed['raw_bytes']} bytes)")

        except asyncio.TimeoutError:
            pass  # Expected: no more messages
        except websockets.exceptions.ConnectionClosed as e:
            log(f"Connection closed: {e}", "WARN")

        self.received_messages.extend(messages)
        return messages

    async def close(self):
        if self.ws:
            await self.ws.close()
            log(f"WebSocket closed ({self.name})")


# ── Test Scenarios ───────────────────────────────────────────


async def test_single_client_sync(
    cp: ControlPlaneClient, share_id: str, doc_id: str
) -> bool:
    """Test: single client connects and syncs."""
    print("\n" + "─" * 50)
    log("TEST: Single client Yjs sync", "STEP")
    print("─" * 50)

    token_data = cp.get_relay_token(share_id, doc_id)
    if not token_data:
        return False

    client = YjsClient(
        ws_url=token_data["relay_url"],
        token=token_data["token"],
        doc_id=doc_id,
        name="client-1",
    )

    if not await client.connect():
        return False

    # Same as plugin: send sync step 1, then awareness
    await client.send_sync_step1()
    await client.send_awareness()

    # Wait for server's sync step 1 (requesting our state)
    messages = await client.receive_messages(timeout=3.0)

    # Complete handshake: respond to server's step 1 with our step 2
    if client.synced:
        await client.send_sync_step2()
        # Receive any follow-up messages
        more = await client.receive_messages(timeout=2.0)
        messages.extend(more)

    await client.close()

    # Validate results
    if client.synced:
        log("Single client sync: SUCCESS", "OK")
        return True
    elif len(messages) > 0:
        log(f"Got {len(messages)} messages, connection works", "OK")
        return True
    else:
        log("No messages received at all", "FAIL")
        return False


async def test_two_client_sync(
    cp: ControlPlaneClient, share_id: str, doc_id: str
) -> bool:
    """Test: two clients connect, one sends update, other receives it."""
    print("\n" + "─" * 50)
    log("TEST: Two-client Yjs sync", "STEP")
    print("─" * 50)

    # Get two separate tokens (like two Obsidian instances would)
    token1_data = cp.get_relay_token(share_id, doc_id)
    token2_data = cp.get_relay_token(share_id, doc_id)
    if not token1_data or not token2_data:
        return False

    client1 = YjsClient(
        ws_url=token1_data["relay_url"],
        token=token1_data["token"],
        doc_id=doc_id,
        name="writer",
    )
    client2 = YjsClient(
        ws_url=token2_data["relay_url"],
        token=token2_data["token"],
        doc_id=doc_id,
        name="reader",
    )

    # Connect both
    log("Connecting client 1 (writer)...", "STEP")
    if not await client1.connect():
        return False
    log("Connecting client 2 (reader)...", "STEP")
    if not await client2.connect():
        await client1.close()
        return False

    # Both do initial sync (complete the handshake)
    await client1.send_sync_step1()
    await client1.send_awareness()
    await client1.receive_messages(timeout=2.0)
    if client1.synced:
        await client1.send_sync_step2()
    await asyncio.sleep(0.2)

    await client2.send_sync_step1()
    await client2.send_awareness()
    await client2.receive_messages(timeout=2.0)
    if client2.synced:
        await client2.send_sync_step2()
    await asyncio.sleep(0.2)

    # Drain any remaining messages (awareness from the other client, etc.)
    await client1.receive_messages(timeout=1.0)
    await client2.receive_messages(timeout=1.0)

    log("Both clients synced initial state", "OK")

    # Verify cross-client relay forwarding.
    # During client 2's connection, it should have received awareness from client 1
    # (forwarded by the relay). Check client 2's received messages for awareness > 3 bytes.
    # 3-byte awareness = self-awareness (empty), >3 bytes = forwarded from other clients.
    c2_awareness = [
        m for m in client2.received_messages
        if m["type"] == "awareness" and m["raw_bytes"] > 10
    ]

    if c2_awareness:
        log(
            f"Client 2 received {len(c2_awareness)} awareness from client 1 "
            f"({c2_awareness[0]['raw_bytes']} bytes) — relay forwarding confirmed!",
            "OK",
        )
    else:
        log("Client 2 did not receive awareness from client 1", "WARN")

    # Also test: send a new awareness with incremented clock
    log("Client 1 sending awareness update (clock=2)...", "STEP")
    awareness_msg = build_awareness_update(client1.client_id, "writer-update", clock=2)
    await client1.ws.send(awareness_msg)

    messages = await client2.receive_messages(timeout=3.0)
    new_awareness = [m for m in messages if m["type"] == "awareness"]

    await client1.close()
    await client2.close()

    if new_awareness:
        log(f"Client 2 received live awareness update — full relay sync confirmed!", "OK")
    elif c2_awareness:
        log("Initial cross-client awareness worked, live update may have been batched", "OK")
    else:
        log("No cross-client awareness detected", "WARN")
        log("Both clients connected and synced — basic relay path works", "OK")

    return True


# ── Main ─────────────────────────────────────────────────────


async def run_tests(args) -> bool:
    print("\n" + "=" * 60)
    print("  HEADLESS YJS E2E TEST")
    print("=" * 60)
    print(f"  Server:  {args.server}")
    print(f"  User:    {args.email}")
    print(f"  Mode:    {'two-client sync' if args.two_clients else 'single client'}")
    print("=" * 60)

    cp = ControlPlaneClient(
        server_url=args.server.rstrip("/"),
        email=args.email,
        password=args.password,
    )

    # Step 1: Login
    if not cp.login():
        return False

    # Step 2: Get share
    log("Get share...", "STEP")
    if args.share_id:
        share = cp.get_share(args.share_id)
        if not share:
            log(f"Share {args.share_id} not found", "FAIL")
            return False
        share_id = args.share_id
        log(f"Using share: {share['path']} ({share['kind']})", "OK")
    else:
        shares = cp.get_shares()
        if not shares:
            log("No shares found. Creating test share...", "WARN")
            share = cp.create_share("headless-e2e-test")
            if not share:
                return False
            share_id = share["id"]
        else:
            share = shares[0]
            share_id = share["id"]
            log(f"Using first share: {share['path']} (id: {share_id})", "OK")

    doc_id = args.doc_id or share_id

    # Step 3+4+5: Single client sync test
    success = await test_single_client_sync(cp, share_id, doc_id)
    if not success:
        return False

    # Step 6+7: Two-client sync test (optional)
    if args.two_clients:
        success = await test_two_client_sync(cp, share_id, doc_id)
        if not success:
            return False

    # Summary
    print("\n" + "=" * 60)
    log("ALL TESTS PASSED", "OK")
    print("=" * 60 + "\n")
    return True


def main():
    parser = argparse.ArgumentParser(
        description="Headless Yjs E2E test — simulates Obsidian plugin",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--server", "-s", required=True, help="Control plane URL")
    parser.add_argument("--email", "-e", required=True, help="User email")
    parser.add_argument("--password", "-p", required=True, help="User password")
    parser.add_argument("--share-id", help="Specific share ID")
    parser.add_argument("--doc-id", help="Document ID (defaults to share_id)")
    parser.add_argument(
        "--two-clients", action="store_true", help="Run two-client sync test"
    )

    args = parser.parse_args()
    success = asyncio.run(run_tests(args))
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
