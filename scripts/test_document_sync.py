#!/usr/bin/env python3
"""
Cross-User Document Synchronization E2E Tests

Tests ACTUAL Yjs document content synchronization between two users
through the relay server. Uses pycrdt (Python Yjs bindings) to create
real Yjs documents, insert content, and verify it arrives on the other side.

Tests:
  1. A→B Text Sync - User A types text, User B receives it
  2. B→A Text Sync - User B types text, User A receives it
  3. Concurrent Edits - Both users type simultaneously, CRDT merges
  4. Large Document Sync - Sync a document with many paragraphs
  5. Map Data Sync - Sync structured metadata (Yjs Map)

Requirements:
    pip install httpx websockets pycrdt

Usage:
    python scripts/test_document_sync.py
    python scripts/test_document_sync.py -v          # verbose
    python scripts/test_document_sync.py --test 1    # run specific test
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import struct
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
    result = bytearray()
    while n > 0x7F:
        result.append(0x80 | (n & 0x7F))
        n >>= 7
    result.append(n & 0x7F)
    return bytes(result)


def decode_uint(data: bytes, offset: int = 0) -> tuple[int, int]:
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
    return encode_uint(len(data)) + data


def decode_bytes(data: bytes, offset: int = 0) -> tuple[bytes, int]:
    length, offset = decode_uint(data, offset)
    return data[offset : offset + length], offset + length


# ── Relay Connection ─────────────────────────────────────────
@dataclass
class RelayClient:
    """WebSocket client with local Yjs document."""

    name: str
    ws: object
    doc: pycrdt.Doc
    verbose: bool = False

    async def handshake(self):
        """Complete Yjs sync handshake with relay server."""
        sv = self.doc.get_state()
        await self.ws.send(bytes([MSG_SYNC, SYNC_STEP1]) + encode_bytes(sv))

        for _ in range(20):
            try:
                data = await asyncio.wait_for(self.ws.recv(), timeout=2.0)
                if isinstance(data, str):
                    data = data.encode()
                if len(data) < 2 or data[0] != MSG_SYNC:
                    continue

                sub = data[1]
                payload, _ = decode_bytes(data, 2)

                if sub == SYNC_STEP1:
                    # Server requests our updates based on its state vector
                    diff = self.doc.get_update(payload)
                    await self.ws.send(bytes([MSG_SYNC, SYNC_STEP2]) + encode_bytes(diff))
                elif sub == SYNC_STEP2:
                    # Server sends its document state
                    self.doc.apply_update(payload)
            except asyncio.TimeoutError:
                break

        if self.verbose:
            print(f"    [{self.name}] handshake complete")

    async def send_update(self, update: bytes):
        """Send a Yjs sync update via WebSocket."""
        msg = bytes([MSG_SYNC, SYNC_UPDATE]) + encode_bytes(update)
        await self.ws.send(msg)
        if self.verbose:
            print(f"    [{self.name}] sent update ({len(update)} bytes)")

    async def receive_updates(self, timeout: float = 3.0) -> int:
        """Receive and apply sync updates from relay. Returns count of applied updates."""
        count = 0
        try:
            while True:
                data = await asyncio.wait_for(self.ws.recv(), timeout=timeout)
                if isinstance(data, bytes) and len(data) >= 2:
                    if data[0] == MSG_SYNC and data[1] == SYNC_UPDATE:
                        payload, _ = decode_bytes(data, 2)
                        self.doc.apply_update(payload)
                        count += 1
                        if self.verbose:
                            print(f"    [{self.name}] applied update ({len(payload)} bytes)")
        except (asyncio.TimeoutError, Exception):
            pass
        return count

    async def drain(self, timeout: float = 1.0):
        """Drain all pending messages."""
        try:
            while True:
                await asyncio.wait_for(self.ws.recv(), timeout=timeout)
        except (asyncio.TimeoutError, Exception):
            pass

    async def close(self):
        if self.ws:
            await self.ws.close()


# ── Test Suite ───────────────────────────────────────────────
class DocumentSyncTests:
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
        self.results: list[tuple[str, bool, float]] = []
        self.created_shares: list[str] = []
        # Shared login sessions
        self.jwt_a = ""
        self.jwt_b = ""
        self.uid_b = ""

    def _client(self) -> httpx.Client:
        return httpx.Client(timeout=15, limits=httpx.Limits(max_keepalive_connections=0))

    def login(self):
        """Login both users once."""
        with self._client() as c:
            r = c.post(
                f"{self.server_url}/v1/auth/login",
                json={"email": self.user_a_email, "password": self.user_a_password},
            )
            assert r.status_code == 200, f"User A login failed: {r.status_code}"
            self.jwt_a = r.json()["access_token"]

            r = c.post(
                f"{self.server_url}/v1/auth/login",
                json={"email": self.user_b_email, "password": self.user_b_password},
            )
            assert r.status_code == 200, f"User B login failed: {r.status_code}"
            self.jwt_b = r.json()["access_token"]

            r = c.get(
                f"{self.server_url}/v1/auth/me",
                headers={"Authorization": f"Bearer {self.jwt_b}"},
            )
            self.uid_b = r.json()["id"]

    def create_shared_doc(self) -> tuple[str, str, dict, dict]:
        """Create a share, add User B, get relay tokens for both.
        Returns: (share_id, doc_id, token_a, token_b)
        """
        with self._client() as c:
            ts = int(time.time())
            r = c.post(
                f"{self.server_url}/v1/shares",
                json={"kind": "folder", "path": f"docsync-{ts}", "visibility": "private"},
                headers={"Authorization": f"Bearer {self.jwt_a}"},
            )
            assert r.status_code in (200, 201), f"Create share failed: {r.status_code}"
            share = r.json()
            share_id = share["id"]
            self.created_shares.append(share_id)

            c.post(
                f"{self.server_url}/v1/shares/{share_id}/members",
                json={"user_id": self.uid_b, "role": "editor"},
                headers={"Authorization": f"Bearer {self.jwt_a}"},
            )

            doc_id = share_id
            fp = f"{share['path']}/test.md"

            r = c.post(
                f"{self.server_url}/v1/tokens/relay",
                json={"share_id": share_id, "doc_id": doc_id, "mode": "write", "file_path": fp},
                headers={"Authorization": f"Bearer {self.jwt_a}"},
            )
            assert r.status_code == 200
            tok_a = r.json()

            r = c.post(
                f"{self.server_url}/v1/tokens/relay",
                json={"share_id": share_id, "doc_id": doc_id, "mode": "write", "file_path": fp},
                headers={"Authorization": f"Bearer {self.jwt_b}"},
            )
            assert r.status_code == 200
            tok_b = r.json()

            return share_id, doc_id, tok_a, tok_b

    async def connect_pair(
        self, doc_id: str, tok_a: dict, tok_b: dict
    ) -> tuple[RelayClient, RelayClient]:
        """Connect two clients, complete handshake, drain residual messages."""
        doc_a = pycrdt.Doc()
        doc_b = pycrdt.Doc()

        url = f"{tok_a['relay_url']}/{doc_id}"
        ws_a = await websockets.connect(
            url, additional_headers={"Authorization": f"Bearer {tok_a['token']}"}
        )
        ws_b = await websockets.connect(
            url, additional_headers={"Authorization": f"Bearer {tok_b['token']}"}
        )

        client_a = RelayClient("A", ws_a, doc_a, verbose=self.verbose)
        client_b = RelayClient("B", ws_b, doc_b, verbose=self.verbose)

        await client_a.handshake()
        await client_b.handshake()

        # Let relay settle
        await asyncio.sleep(1.0)
        await client_a.drain()
        await client_b.drain()

        return client_a, client_b

    def log(self, step: str, ok: bool):
        sym = "✓" if ok else "✗"
        print(f"  → {step}... {sym}")

    async def run_all(self, test_filter: int | None = None):
        print("=" * 70)
        print("Document Synchronization E2E Tests")
        print("=" * 70)
        print(f"Server: {self.server_url}")
        print(f"User A: {self.user_a_email}")
        print(f"User B: {self.user_b_email}")
        print("=" * 70)

        self.login()
        print(f"  Both users logged in")
        print()

        tests = [
            (1, "A→B Text Sync", self.test_a_to_b_text_sync),
            (2, "B→A Text Sync", self.test_b_to_a_text_sync),
            (3, "Bidirectional Sequential Sync", self.test_bidirectional_sync),
            (4, "Concurrent Edits (CRDT merge)", self.test_concurrent_edits),
            (5, "Large Document Sync", self.test_large_document_sync),
            (6, "Map Data Sync", self.test_map_sync),
            (7, "Incremental Multi-Edit Sync", self.test_incremental_edits),
        ]

        for num, name, fn in tests:
            if test_filter is not None and num != test_filter:
                continue
            await fn(num, name)

        await self.cleanup()

        # Summary
        print()
        print("=" * 70)
        print("Summary")
        print("=" * 70)
        total = len(self.results)
        passed = sum(1 for _, ok, _ in self.results if ok)
        for name, ok, dur in self.results:
            sym = "✓" if ok else "✗"
            print(f"  {sym} {name} ({dur:.2f}s)")
        print(f"\nTotal: {total}  Passed: {passed}  Failed: {total - passed}")
        print(f"Success Rate: {passed / total * 100:.0f}%" if total else "N/A")
        print("=" * 70)
        return passed == total

    async def test_a_to_b_text_sync(self, num: int, name: str):
        """User A inserts text, User B receives it via relay."""
        print(f"Test {num}: {name}")
        print("-" * 70)
        t0 = time.time()

        try:
            _, doc_id, tok_a, tok_b = self.create_shared_doc()
            a, b = await self.connect_pair(doc_id, tok_a, tok_b)

            text_a = a.doc.get("content", type=pycrdt.Text)

            sv_before = a.doc.get_state()
            text_a += "Hello from User A!"
            update = a.doc.get_update(sv_before)
            await a.send_update(update)
            self.log("User A inserts text", True)

            await asyncio.sleep(1.0)
            n = await b.receive_updates(timeout=3.0)
            text_b = b.doc.get("content", type=pycrdt.Text)
            content_b = str(text_b)

            ok = content_b == "Hello from User A!"
            self.log(f"User B receives text: \"{content_b}\" (updates={n})", ok)

            await a.close()
            await b.close()

            dur = time.time() - t0
            self.results.append((name, ok, dur))
            print(f" {'✓ PASSED' if ok else '✗ FAILED'} ({dur:.2f}s)")
        except Exception as e:
            dur = time.time() - t0
            self.results.append((name, False, dur))
            print(f" ✗ EXCEPTION: {e}")
        print()

    async def test_b_to_a_text_sync(self, num: int, name: str):
        """User B inserts text, User A receives it via relay."""
        print(f"Test {num}: {name}")
        print("-" * 70)
        t0 = time.time()

        try:
            _, doc_id, tok_a, tok_b = self.create_shared_doc()
            a, b = await self.connect_pair(doc_id, tok_a, tok_b)

            text_b = b.doc.get("content", type=pycrdt.Text)

            sv_before = b.doc.get_state()
            text_b += "Hello from User B!"
            update = b.doc.get_update(sv_before)
            await b.send_update(update)
            self.log("User B inserts text", True)

            await asyncio.sleep(1.0)
            n = await a.receive_updates(timeout=3.0)
            text_a = a.doc.get("content", type=pycrdt.Text)
            content_a = str(text_a)

            ok = content_a == "Hello from User B!"
            self.log(f"User A receives text: \"{content_a}\" (updates={n})", ok)

            await a.close()
            await b.close()

            dur = time.time() - t0
            self.results.append((name, ok, dur))
            print(f" {'✓ PASSED' if ok else '✗ FAILED'} ({dur:.2f}s)")
        except Exception as e:
            dur = time.time() - t0
            self.results.append((name, False, dur))
            print(f" ✗ EXCEPTION: {e}")
        print()

    async def test_bidirectional_sync(self, num: int, name: str):
        """Both users edit sequentially, both see full content."""
        print(f"Test {num}: {name}")
        print("-" * 70)
        t0 = time.time()

        try:
            _, doc_id, tok_a, tok_b = self.create_shared_doc()
            a, b = await self.connect_pair(doc_id, tok_a, tok_b)

            text_a = a.doc.get("content", type=pycrdt.Text)
            text_b = b.doc.get("content", type=pycrdt.Text)

            # Step 1: A writes
            sv = a.doc.get_state()
            text_a += "Line 1 from A. "
            await a.send_update(a.doc.get_update(sv))
            self.log("User A writes 'Line 1 from A. '", True)

            await asyncio.sleep(1.0)
            await b.receive_updates(timeout=3.0)
            ok1 = str(text_b) == "Line 1 from A. "
            self.log(f"User B sees: \"{str(text_b)}\"", ok1)

            # Step 2: B appends
            sv = b.doc.get_state()
            text_b += "Line 2 from B. "
            await b.send_update(b.doc.get_update(sv))
            self.log("User B appends 'Line 2 from B. '", True)

            await asyncio.sleep(1.0)
            await a.receive_updates(timeout=3.0)
            ok2 = str(text_a) == "Line 1 from A. Line 2 from B. "
            self.log(f"User A sees: \"{str(text_a)}\"", ok2)

            # Step 3: A appends more
            sv = a.doc.get_state()
            text_a += "Line 3 from A."
            await a.send_update(a.doc.get_update(sv))
            self.log("User A appends 'Line 3 from A.'", True)

            await asyncio.sleep(1.0)
            await b.receive_updates(timeout=3.0)

            expected = "Line 1 from A. Line 2 from B. Line 3 from A."
            final_a = str(text_a)
            final_b = str(text_b)
            ok3 = final_a == expected and final_b == expected
            self.log(f"Both docs equal: {final_a == final_b}", ok3)

            await a.close()
            await b.close()

            ok = ok1 and ok2 and ok3
            dur = time.time() - t0
            self.results.append((name, ok, dur))
            print(f" {'✓ PASSED' if ok else '✗ FAILED'} ({dur:.2f}s)")
        except Exception as e:
            dur = time.time() - t0
            self.results.append((name, False, dur))
            print(f" ✗ EXCEPTION: {e}")
        print()

    async def test_concurrent_edits(self, num: int, name: str):
        """Both users edit at the same time. CRDT resolves to consistent state."""
        print(f"Test {num}: {name}")
        print("-" * 70)
        t0 = time.time()

        try:
            _, doc_id, tok_a, tok_b = self.create_shared_doc()
            a, b = await self.connect_pair(doc_id, tok_a, tok_b)

            text_a = a.doc.get("content", type=pycrdt.Text)
            text_b = b.doc.get("content", type=pycrdt.Text)

            # Both write simultaneously (before receiving each other's updates)
            sv_a = a.doc.get_state()
            text_a += "A-EDIT"
            update_a = a.doc.get_update(sv_a)

            sv_b = b.doc.get_state()
            text_b += "B-EDIT"
            update_b = b.doc.get_update(sv_b)

            # Send both updates at (almost) the same time
            await a.send_update(update_a)
            await b.send_update(update_b)
            self.log("Both users insert text concurrently", True)

            # Let relay forward
            await asyncio.sleep(1.5)

            # Both receive the other's update
            await a.receive_updates(timeout=3.0)
            await b.receive_updates(timeout=3.0)

            final_a = str(text_a)
            final_b = str(text_b)

            # CRDT: both docs must converge to the same content
            # Order may vary (A-EDITB-EDIT or B-EDITA-EDIT) but must be equal
            converged = final_a == final_b
            has_both = "A-EDIT" in final_a and "B-EDIT" in final_a
            self.log(f"A sees: \"{final_a}\"", has_both)
            self.log(f"B sees: \"{final_b}\"", has_both)
            self.log(f"Docs converged (CRDT): {converged}", converged)

            await a.close()
            await b.close()

            ok = converged and has_both
            dur = time.time() - t0
            self.results.append((name, ok, dur))
            print(f" {'✓ PASSED' if ok else '✗ FAILED'} ({dur:.2f}s)")
        except Exception as e:
            dur = time.time() - t0
            self.results.append((name, False, dur))
            print(f" ✗ EXCEPTION: {e}")
        print()

    async def test_large_document_sync(self, num: int, name: str):
        """Sync a document with many paragraphs (~10KB)."""
        print(f"Test {num}: {name}")
        print("-" * 70)
        t0 = time.time()

        try:
            _, doc_id, tok_a, tok_b = self.create_shared_doc()
            a, b = await self.connect_pair(doc_id, tok_a, tok_b)

            text_a = a.doc.get("content", type=pycrdt.Text)
            text_b = b.doc.get("content", type=pycrdt.Text)

            # Build a ~10KB document
            paragraphs = []
            for i in range(50):
                para = f"Paragraph {i+1}: " + "Lorem ipsum dolor sit amet. " * 3
                paragraphs.append(para)
            full_text = "\n\n".join(paragraphs)

            sv = a.doc.get_state()
            text_a += full_text
            update = a.doc.get_update(sv)
            await a.send_update(update)
            self.log(f"User A sends {len(full_text)} chars ({len(update)} bytes update)", True)

            # Give more time for large payload
            await asyncio.sleep(2.0)
            n = await b.receive_updates(timeout=5.0)

            content_b = str(text_b)
            ok = content_b == full_text
            self.log(
                f"User B received {len(content_b)} chars ({n} updates), match={ok}",
                ok,
            )

            await a.close()
            await b.close()

            dur = time.time() - t0
            self.results.append((name, ok, dur))
            print(f" {'✓ PASSED' if ok else '✗ FAILED'} ({dur:.2f}s)")
        except Exception as e:
            dur = time.time() - t0
            self.results.append((name, False, dur))
            print(f" ✗ EXCEPTION: {e}")
        print()

    async def test_map_sync(self, num: int, name: str):
        """Sync structured metadata via Yjs Map (like frontmatter)."""
        print(f"Test {num}: {name}")
        print("-" * 70)
        t0 = time.time()

        try:
            _, doc_id, tok_a, tok_b = self.create_shared_doc()
            a, b = await self.connect_pair(doc_id, tok_a, tok_b)

            meta_a = a.doc.get("metadata", type=pycrdt.Map)
            meta_b = b.doc.get("metadata", type=pycrdt.Map)

            # A sets metadata
            sv = a.doc.get_state()
            meta_a["title"] = "Test Document"
            meta_a["author"] = "User A"
            meta_a["version"] = 1
            meta_a["tags"] = "test,sync,e2e"
            update = a.doc.get_update(sv)
            await a.send_update(update)
            self.log("User A sets metadata (title, author, version, tags)", True)

            await asyncio.sleep(1.0)
            await b.receive_updates(timeout=3.0)

            ok1 = (
                meta_b.get("title") == "Test Document"
                and meta_b.get("author") == "User A"
                and meta_b.get("version") == 1
                and meta_b.get("tags") == "test,sync,e2e"
            )
            self.log(f"User B sees metadata: title=\"{meta_b.get('title')}\"", ok1)

            # B updates metadata
            sv = b.doc.get_state()
            meta_b["author"] = "User B (edited)"
            meta_b["version"] = 2
            update = b.doc.get_update(sv)
            await b.send_update(update)
            self.log("User B updates author and version", True)

            await asyncio.sleep(1.0)
            await a.receive_updates(timeout=3.0)

            ok2 = meta_a.get("author") == "User B (edited)" and int(meta_a.get("version")) == 2
            self.log(f"User A sees updated: author=\"{meta_a.get('author')}\", v={meta_a.get('version')}", ok2)

            # Verify title unchanged
            ok3 = meta_a.get("title") == "Test Document"
            self.log(f"Title preserved: \"{meta_a.get('title')}\"", ok3)

            await a.close()
            await b.close()

            ok = ok1 and ok2 and ok3
            dur = time.time() - t0
            self.results.append((name, ok, dur))
            print(f" {'✓ PASSED' if ok else '✗ FAILED'} ({dur:.2f}s)")
        except Exception as e:
            dur = time.time() - t0
            self.results.append((name, False, dur))
            print(f" ✗ EXCEPTION: {e}")
        print()

    async def test_incremental_edits(self, num: int, name: str):
        """Multiple paragraph-level edits, each verified at the receiver.

        Simulates real Obsidian editing: user types a paragraph, saves,
        other user sees it. Tests sequential document growth.
        """
        print(f"Test {num}: {name}")
        print("-" * 70)
        t0 = time.time()

        try:
            _, doc_id, tok_a, tok_b = self.create_shared_doc()
            a, b = await self.connect_pair(doc_id, tok_a, tok_b)

            text_a = a.doc.get("content", type=pycrdt.Text)
            text_b = b.doc.get("content", type=pycrdt.Text)

            # A sends 5 paragraph-level edits, each verified at B
            paragraphs = [
                "# Meeting Notes\n\n",
                "## Attendees\n- Alice\n- Bob\n- Charlie\n\n",
                "## Discussion\nWe discussed the Q1 roadmap and priorities.\n\n",
                "## Action Items\n1. Alice: prepare budget\n2. Bob: review specs\n\n",
                "## Next Meeting\nScheduled for next Friday at 2pm.",
            ]

            all_ok = True
            expected = ""
            for i, para in enumerate(paragraphs):
                sv = a.doc.get_state()
                text_a += para
                update = a.doc.get_update(sv)
                await a.send_update(update)
                expected += para

                await asyncio.sleep(1.0)
                await b.receive_updates(timeout=3.0)
                content_b = str(text_b)
                ok = content_b == expected
                self.log(f"Edit {i+1}/5: B sees {len(content_b)} chars", ok)
                if not ok:
                    all_ok = False
                    if self.verbose:
                        print(f"    Expected: \"{expected[:60]}...\"")
                        print(f"    Got:      \"{content_b[:60]}...\"")

            await a.close()
            await b.close()

            dur = time.time() - t0
            self.results.append((name, all_ok, dur))
            print(f" {'✓ PASSED' if all_ok else '✗ FAILED'} ({dur:.2f}s)")
        except Exception as e:
            dur = time.time() - t0
            self.results.append((name, False, dur))
            print(f" ✗ EXCEPTION: {e}")
        print()

    async def cleanup(self):
        if not self.created_shares:
            return
        print("Cleanup")
        print("-" * 70)
        with self._client() as c:
            for sid in self.created_shares:
                r = c.delete(
                    f"{self.server_url}/v1/shares/{sid}",
                    headers={"Authorization": f"Bearer {self.jwt_a}"},
                )
                sym = "✓" if r.status_code in (200, 204) else "✗"
                print(f"  → Delete share {sid[:8]}... {sym}")
        print()


async def main():
    parser = argparse.ArgumentParser(description="Document Sync E2E Tests")
    parser.add_argument(
        "--server", "-s", default="https://cp.5evofarm.entire.vc", help="Control plane URL"
    )
    parser.add_argument("--user-a-email", default="test@entire.vc")
    parser.add_argument("--user-a-password", default="Test123456")
    parser.add_argument("--user-b-email", default="simple@entire.vc")
    parser.add_argument("--user-b-password", default="password123")
    parser.add_argument("-v", "--verbose", action="store_true")
    parser.add_argument("--test", type=int, help="Run specific test number (1-7)")
    args = parser.parse_args()

    tests = DocumentSyncTests(
        server_url=args.server,
        user_a_email=args.user_a_email,
        user_a_password=args.user_a_password,
        user_b_email=args.user_b_email,
        user_b_password=args.user_b_password,
        verbose=args.verbose,
    )

    success = await tests.run_all(test_filter=args.test)
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    asyncio.run(main())
