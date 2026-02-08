#!/usr/bin/env python3
"""
Two-Client Folder Sync E2E Tests

Simulates the plugin's SharedFolder sync flow using real Yjs documents
and the relay server. Tests that Y.Map entries (filemeta_v0) sync
between two clients connected to the same share_id.

This directly tests the data path that the plugin's SyncStore relies on:
- Owner writes file metadata to Y.Map("filemeta_v0")
- Member connects, receives the Y.Map state via Yjs sync
- Incremental updates propagate in real-time

Tests:
  1. Owner writes file metadata → Member receives it (initial sync)
  2. Incremental update: Owner adds file while member is connected
  3. Member writes file metadata → Owner receives it
  4. Both write simultaneously → Both receive all entries (CRDT merge)
  5. Owner deletes a file entry → Member sees deletion
  6. Member disconnects and reconnects → receives missed updates
  7. Large file tree: sync folder with 50+ files

Requirements:
    pip install httpx websockets pycrdt

Usage:
    python scripts/test_folder_sync.py
    python scripts/test_folder_sync.py -v          # verbose
    python scripts/test_folder_sync.py --test 1    # run specific test
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time
import uuid
from dataclasses import dataclass, field

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


# -- Yjs Protocol Constants --
MSG_SYNC = 0
MSG_AWARENESS = 1
SYNC_STEP1 = 0
SYNC_STEP2 = 1
SYNC_UPDATE = 2


# -- Yjs Binary Encoding --
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


# -- Folder Sync Client --
@dataclass
class FolderSyncClient:
    """Simulates the plugin's SharedFolder + SyncStore behavior.

    Uses Y.Map("filemeta_v0") just like the real plugin's SyncStore.
    """

    name: str
    ws: object = None
    doc: pycrdt.Doc = field(default_factory=pycrdt.Doc)
    verbose: bool = False
    _filemeta: pycrdt.Map = field(init=False, default=None)
    _legacy_docs: pycrdt.Map = field(init=False, default=None)

    def __post_init__(self):
        self._filemeta = self.doc.get("filemeta_v0", type=pycrdt.Map)
        self._legacy_docs = self.doc.get("docs", type=pycrdt.Map)

    async def connect(self, ws_url: str, token: str, retries: int = 3):
        """Connect to relay via WebSocket with retry on SSL/timeout errors."""
        for attempt in range(retries):
            try:
                self.ws = await websockets.connect(
                    ws_url,
                    additional_headers={"Authorization": f"Bearer {token}"},
                    open_timeout=20,
                )
                if self.verbose:
                    print(f"    [{self.name}] WS connected")
                return
            except Exception as e:
                if attempt < retries - 1:
                    wait = 3 * (attempt + 1)
                    if self.verbose:
                        print(f"    [{self.name}] connect retry {attempt+1}/{retries} after {wait}s: {e}")
                    await asyncio.sleep(wait)
                else:
                    raise

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
                    diff = self.doc.get_update(payload)
                    await self.ws.send(bytes([MSG_SYNC, SYNC_STEP2]) + encode_bytes(diff))
                elif sub == SYNC_STEP2:
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
        """Receive and apply sync updates from relay. Returns count."""
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

    def add_file(self, vpath: str, file_type: str = "markdown") -> bytes:
        """Simulate addLocalDocs: write to filemeta_v0 Y.Map.
        Returns the Yjs update bytes.
        """
        sv_before = self.doc.get_state()
        meta = pycrdt.Map(
            {
                "id": str(uuid.uuid4()),
                "type": file_type,
                "hash": f"h{uuid.uuid4().hex[:8]}",
            }
        )
        self._filemeta[vpath] = meta
        return self.doc.get_update(sv_before)

    def remove_file(self, vpath: str) -> bytes:
        """Remove a file entry from filemeta_v0. Returns the Yjs update."""
        sv_before = self.doc.get_state()
        del self._filemeta[vpath]
        return self.doc.get_update(sv_before)

    def get_files(self) -> dict[str, dict]:
        """Read current filemeta_v0 state as plain dicts."""
        result = {}
        for key in self._filemeta:
            val = self._filemeta[key]
            if isinstance(val, pycrdt.Map):
                result[key] = {k: val[k] for k in val}
            else:
                result[key] = val
        return result

    def file_count(self) -> int:
        return len(self._filemeta)

    async def close(self):
        if self.ws:
            await self.ws.close()


# -- Test Suite --
class FolderSyncTests:
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
        self.jwt_a = ""
        self.jwt_b = ""
        self.uid_b = ""

    def _client(self) -> httpx.Client:
        return httpx.Client(
            timeout=httpx.Timeout(30.0, connect=20.0),
            limits=httpx.Limits(max_keepalive_connections=0),
        )

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

    def create_folder_share(self) -> tuple[str, dict, dict]:
        """Create a folder share, add User B, get relay tokens for both.
        Returns: (share_id, token_a, token_b)
        """
        with self._client() as c:
            ts = int(time.time())
            r = c.post(
                f"{self.server_url}/v1/shares",
                json={"kind": "folder", "path": f"foldersync-{ts}", "visibility": "private"},
                headers={"Authorization": f"Bearer {self.jwt_a}"},
            )
            assert r.status_code in (200, 201), f"Create share failed: {r.status_code} {r.text}"
            share = r.json()
            share_id = share["id"]
            self.created_shares.append(share_id)

            c.post(
                f"{self.server_url}/v1/shares/{share_id}/members",
                json={"user_id": self.uid_b, "role": "editor"},
                headers={"Authorization": f"Bearer {self.jwt_a}"},
            )

            # doc_id = share_id for folder metadata (same as plugin)
            doc_id = share_id
            fp = f"{share['path']}/test.md"

            r = c.post(
                f"{self.server_url}/v1/tokens/relay",
                json={"share_id": share_id, "doc_id": doc_id, "mode": "write", "file_path": fp},
                headers={"Authorization": f"Bearer {self.jwt_a}"},
            )
            assert r.status_code == 200, f"Token A failed: {r.status_code} {r.text}"
            tok_a = r.json()

            r = c.post(
                f"{self.server_url}/v1/tokens/relay",
                json={"share_id": share_id, "doc_id": doc_id, "mode": "write", "file_path": fp},
                headers={"Authorization": f"Bearer {self.jwt_b}"},
            )
            assert r.status_code == 200, f"Token B failed: {r.status_code} {r.text}"
            tok_b = r.json()

            return share_id, tok_a, tok_b

    async def connect_client(
        self, name: str, share_id: str, token_info: dict
    ) -> FolderSyncClient:
        """Create and connect a single folder sync client."""
        client = FolderSyncClient(name=name, verbose=self.verbose)
        url = f"{token_info['relay_url']}/{share_id}"
        await client.connect(url, token_info["token"])
        await client.handshake()
        await asyncio.sleep(0.5)
        await client.drain()
        return client

    async def connect_pair(
        self, share_id: str, tok_a: dict, tok_b: dict
    ) -> tuple[FolderSyncClient, FolderSyncClient]:
        """Connect two clients, complete handshake, drain residual messages."""
        a = await self.connect_client("Owner", share_id, tok_a)
        b = await self.connect_client("Member", share_id, tok_b)
        return a, b

    def log(self, step: str, ok: bool):
        sym = "+" if ok else "x"
        print(f"  -> {step}... {sym}")

    async def run_all(self, test_filter: int | None = None):
        print("=" * 70)
        print("Folder Sync E2E Tests (Y.Map filemeta_v0)")
        print("=" * 70)
        print(f"Server: {self.server_url}")
        print(f"Owner (A): {self.user_a_email}")
        print(f"Member (B): {self.user_b_email}")
        print("=" * 70)

        self.login()
        print("  Both users logged in")
        print()

        tests = [
            (1, "Owner->Member Initial Sync", self.test_1_owner_to_member_sync),
            (2, "Incremental Update (live)", self.test_2_incremental_update),
            (3, "Member->Owner Sync", self.test_3_member_to_owner),
            (4, "Concurrent Writes (CRDT)", self.test_4_concurrent_writes),
            (5, "Delete Propagation", self.test_5_delete_propagation),
            (6, "Reconnect Recovery", self.test_6_reconnect_after_disconnect),
            (7, "Large File Tree (50+ files)", self.test_7_large_file_tree),
        ]

        for i, (num, name, fn) in enumerate(tests):
            if test_filter is not None and num != test_filter:
                continue
            if i > 0:
                await asyncio.sleep(3.0)  # Avoid staging SSL rate limits
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
            sym = "+" if ok else "x"
            print(f"  {sym} {name} ({dur:.2f}s)")
        print(f"\nTotal: {total}  Passed: {passed}  Failed: {total - passed}")
        print(f"Success Rate: {passed / total * 100:.0f}%" if total else "N/A")
        print("=" * 70)
        return passed == total

    async def test_1_owner_to_member_sync(self, num: int, name: str):
        """Owner writes file metadata, member connects later and receives it."""
        print(f"Test {num}: {name}")
        print("-" * 70)
        t0 = time.time()

        try:
            share_id, tok_a, tok_b = self.create_folder_share()

            # Owner connects first and adds files
            owner = await self.connect_client("Owner", share_id, tok_a)

            update1 = owner.add_file("/notes/hello.md", "markdown")
            await owner.send_update(update1)
            update2 = owner.add_file("/attachments/image.png", "syncFile")
            await owner.send_update(update2)
            update3 = owner.add_file("/canvas/board.canvas", "canvas")
            await owner.send_update(update3)
            self.log("Owner adds 3 files", True)

            # Wait for relay to persist
            await asyncio.sleep(1.5)

            # Member connects — should receive all metadata via SyncStep2
            member = await self.connect_client("Member", share_id, tok_b)

            files = member.get_files()
            ok1 = "/notes/hello.md" in files
            ok2 = "/attachments/image.png" in files
            ok3 = "/canvas/board.canvas" in files
            self.log(f"Member sees {len(files)} files", ok1 and ok2 and ok3)

            if ok1:
                ftype = files["/notes/hello.md"].get("type")
                ok_type = ftype == "markdown"
                self.log(f"File type correct: {ftype}", ok_type)
            else:
                ok_type = False
                self.log("File type check skipped (file missing)", False)

            if ok1:
                fid = files["/notes/hello.md"].get("id")
                ok_id = fid is not None and len(str(fid)) > 0
                self.log(f"File has UUID: {fid}", ok_id)
            else:
                ok_id = False

            await owner.close()
            await member.close()

            ok = ok1 and ok2 and ok3 and ok_type and ok_id
            dur = time.time() - t0
            self.results.append((name, ok, dur))
            print(f" {'+ PASSED' if ok else 'x FAILED'} ({dur:.2f}s)")
        except Exception as e:
            dur = time.time() - t0
            self.results.append((name, False, dur))
            print(f" x EXCEPTION: {e}")
        print()

    async def test_2_incremental_update(self, num: int, name: str):
        """Owner adds file while member is already connected — live update."""
        print(f"Test {num}: {name}")
        print("-" * 70)
        t0 = time.time()

        try:
            share_id, tok_a, tok_b = self.create_folder_share()

            # Both connect first
            owner, member = await self.connect_pair(share_id, tok_a, tok_b)
            self.log("Both connected", True)

            # Verify both start empty
            ok_empty = owner.file_count() == 0 and member.file_count() == 0
            self.log("Both start with empty filemeta", ok_empty)

            # Owner adds a file
            update = owner.add_file("/docs/readme.md", "markdown")
            await owner.send_update(update)
            self.log("Owner adds /docs/readme.md", True)

            # Member receives the update
            await asyncio.sleep(1.0)
            n = await member.receive_updates(timeout=3.0)
            files = member.get_files()
            ok1 = "/docs/readme.md" in files
            self.log(f"Member receives file ({n} updates, {len(files)} files)", ok1)

            # Owner adds another file
            update2 = owner.add_file("/docs/changelog.md", "markdown")
            await owner.send_update(update2)
            self.log("Owner adds /docs/changelog.md", True)

            await asyncio.sleep(1.0)
            n2 = await member.receive_updates(timeout=3.0)
            files2 = member.get_files()
            ok2 = "/docs/changelog.md" in files2 and len(files2) == 2
            self.log(f"Member sees both files ({n2} updates, {len(files2)} files)", ok2)

            await owner.close()
            await member.close()

            ok = ok_empty and ok1 and ok2
            dur = time.time() - t0
            self.results.append((name, ok, dur))
            print(f" {'+ PASSED' if ok else 'x FAILED'} ({dur:.2f}s)")
        except Exception as e:
            dur = time.time() - t0
            self.results.append((name, False, dur))
            print(f" x EXCEPTION: {e}")
        print()

    async def test_3_member_to_owner(self, num: int, name: str):
        """Member writes file metadata, owner receives it."""
        print(f"Test {num}: {name}")
        print("-" * 70)
        t0 = time.time()

        try:
            share_id, tok_a, tok_b = self.create_folder_share()
            owner, member = await self.connect_pair(share_id, tok_a, tok_b)

            # Member adds files
            update1 = member.add_file("/member-notes/ideas.md", "markdown")
            await member.send_update(update1)
            update2 = member.add_file("/member-notes/draft.md", "markdown")
            await member.send_update(update2)
            self.log("Member adds 2 files", True)

            await asyncio.sleep(1.0)
            n = await owner.receive_updates(timeout=3.0)
            files = owner.get_files()
            ok1 = "/member-notes/ideas.md" in files
            ok2 = "/member-notes/draft.md" in files
            self.log(f"Owner sees {len(files)} files ({n} updates)", ok1 and ok2)

            await owner.close()
            await member.close()

            ok = ok1 and ok2
            dur = time.time() - t0
            self.results.append((name, ok, dur))
            print(f" {'+ PASSED' if ok else 'x FAILED'} ({dur:.2f}s)")
        except Exception as e:
            dur = time.time() - t0
            self.results.append((name, False, dur))
            print(f" x EXCEPTION: {e}")
        print()

    async def test_4_concurrent_writes(self, num: int, name: str):
        """Both write files simultaneously, both get all entries (CRDT merge)."""
        print(f"Test {num}: {name}")
        print("-" * 70)
        t0 = time.time()

        try:
            share_id, tok_a, tok_b = self.create_folder_share()
            owner, member = await self.connect_pair(share_id, tok_a, tok_b)

            # Both add files before receiving each other's updates
            upd_a = owner.add_file("/owner/file-a.md", "markdown")
            upd_b = member.add_file("/member/file-b.md", "markdown")

            # Send both at (almost) the same time
            await owner.send_update(upd_a)
            await member.send_update(upd_b)
            self.log("Both add files concurrently", True)

            # Let relay forward
            await asyncio.sleep(1.5)

            # Both receive the other's update
            await owner.receive_updates(timeout=3.0)
            await member.receive_updates(timeout=3.0)

            files_a = owner.get_files()
            files_b = member.get_files()

            ok1 = "/owner/file-a.md" in files_a and "/member/file-b.md" in files_a
            ok2 = "/owner/file-a.md" in files_b and "/member/file-b.md" in files_b
            converged = set(files_a.keys()) == set(files_b.keys())
            self.log(f"Owner sees: {sorted(files_a.keys())}", ok1)
            self.log(f"Member sees: {sorted(files_b.keys())}", ok2)
            self.log(f"Both converged: {converged}", converged)

            await owner.close()
            await member.close()

            ok = ok1 and ok2 and converged
            dur = time.time() - t0
            self.results.append((name, ok, dur))
            print(f" {'+ PASSED' if ok else 'x FAILED'} ({dur:.2f}s)")
        except Exception as e:
            dur = time.time() - t0
            self.results.append((name, False, dur))
            print(f" x EXCEPTION: {e}")
        print()

    async def test_5_delete_propagation(self, num: int, name: str):
        """Owner deletes a file entry, member sees deletion."""
        print(f"Test {num}: {name}")
        print("-" * 70)
        t0 = time.time()

        try:
            share_id, tok_a, tok_b = self.create_folder_share()
            owner, member = await self.connect_pair(share_id, tok_a, tok_b)

            # Owner adds 3 files
            for path in ["/a.md", "/b.md", "/c.md"]:
                upd = owner.add_file(path, "markdown")
                await owner.send_update(upd)

            await asyncio.sleep(1.0)
            await member.receive_updates(timeout=3.0)
            ok_initial = member.file_count() == 3
            self.log(f"Member sees 3 files: {ok_initial}", ok_initial)

            # Owner deletes /b.md
            upd_del = owner.remove_file("/b.md")
            await owner.send_update(upd_del)
            self.log("Owner deletes /b.md", True)

            await asyncio.sleep(1.0)
            await member.receive_updates(timeout=3.0)

            files = member.get_files()
            ok_deleted = "/b.md" not in files
            ok_remaining = "/a.md" in files and "/c.md" in files
            ok_count = len(files) == 2
            self.log(f"Member sees {len(files)} files, /b.md removed: {ok_deleted}", ok_deleted and ok_remaining)

            await owner.close()
            await member.close()

            ok = ok_initial and ok_deleted and ok_remaining and ok_count
            dur = time.time() - t0
            self.results.append((name, ok, dur))
            print(f" {'+ PASSED' if ok else 'x FAILED'} ({dur:.2f}s)")
        except Exception as e:
            dur = time.time() - t0
            self.results.append((name, False, dur))
            print(f" x EXCEPTION: {e}")
        print()

    async def test_6_reconnect_after_disconnect(self, num: int, name: str):
        """Member disconnects and reconnects, receives missed updates."""
        print(f"Test {num}: {name}")
        print("-" * 70)
        t0 = time.time()

        try:
            share_id, tok_a, tok_b = self.create_folder_share()

            # Both connect, owner adds initial files
            owner, member = await self.connect_pair(share_id, tok_a, tok_b)

            upd = owner.add_file("/initial.md", "markdown")
            await owner.send_update(upd)
            await asyncio.sleep(1.0)
            await member.receive_updates(timeout=3.0)
            ok_initial = "/initial.md" in member.get_files()
            self.log("Member sees initial file", ok_initial)

            # Member disconnects
            await member.close()
            self.log("Member disconnected", True)

            # Owner adds more files while member is offline
            await asyncio.sleep(1.0)
            upd2 = owner.add_file("/offline-add-1.md", "markdown")
            await owner.send_update(upd2)
            upd3 = owner.add_file("/offline-add-2.md", "markdown")
            await owner.send_update(upd3)
            self.log("Owner adds 2 files while member offline", True)

            # Wait for relay to persist
            await asyncio.sleep(1.5)

            # Member reconnects with a fresh Yjs doc (simulates plugin restart)
            member2 = await self.connect_client("Member2", share_id, tok_b)

            files = member2.get_files()
            ok_all = (
                "/initial.md" in files
                and "/offline-add-1.md" in files
                and "/offline-add-2.md" in files
            )
            self.log(f"Reconnected member sees {len(files)} files", ok_all)

            await owner.close()
            await member2.close()

            ok = ok_initial and ok_all
            dur = time.time() - t0
            self.results.append((name, ok, dur))
            print(f" {'+ PASSED' if ok else 'x FAILED'} ({dur:.2f}s)")
        except Exception as e:
            dur = time.time() - t0
            self.results.append((name, False, dur))
            print(f" x EXCEPTION: {e}")
        print()

    async def test_7_large_file_tree(self, num: int, name: str):
        """Sync folder with 50+ files."""
        print(f"Test {num}: {name}")
        print("-" * 70)
        t0 = time.time()

        try:
            share_id, tok_a, tok_b = self.create_folder_share()

            # Owner connects and adds many files in one batch
            owner = await self.connect_client("Owner", share_id, tok_a)

            file_count = 50
            # Batch all changes into one transaction/update for reliability
            sv_before = owner.doc.get_state()
            for i in range(file_count):
                ext = "md" if i % 3 != 2 else "canvas"
                ftype = "markdown" if ext == "md" else "canvas"
                path = f"/vault/folder-{i // 10}/file-{i}.{ext}"
                meta = pycrdt.Map(
                    {
                        "id": str(uuid.uuid4()),
                        "type": ftype,
                        "hash": f"h{uuid.uuid4().hex[:8]}",
                    }
                )
                owner._filemeta[path] = meta

            batch_update = owner.doc.get_update(sv_before)
            await owner.send_update(batch_update)
            self.log(f"Owner adds {file_count} files ({len(batch_update)} bytes)", True)

            # Wait for relay to persist
            await asyncio.sleep(2.0)

            # Member connects
            member = await self.connect_client("Member", share_id, tok_b)

            files = member.get_files()
            ok_count = len(files) == file_count
            self.log(f"Member sees {len(files)}/{file_count} files", ok_count)

            # Verify a few specific files
            ok_sample1 = "/vault/folder-0/file-0.md" in files
            ok_sample2 = "/vault/folder-2/file-25.md" in files
            ok_sample3 = "/vault/folder-4/file-47.canvas" in files
            self.log("Sample files present", ok_sample1 and ok_sample2 and ok_sample3)

            # Verify types
            if ok_sample3:
                ftype = files["/vault/folder-4/file-47.canvas"].get("type")
                ok_type = ftype == "canvas"
                self.log(f"Canvas type correct: {ftype}", ok_type)
            else:
                ok_type = False

            await owner.close()
            await member.close()

            ok = ok_count and ok_sample1 and ok_sample2 and ok_sample3 and ok_type
            dur = time.time() - t0
            self.results.append((name, ok, dur))
            print(f" {'+ PASSED' if ok else 'x FAILED'} ({dur:.2f}s)")
        except Exception as e:
            dur = time.time() - t0
            self.results.append((name, False, dur))
            print(f" x EXCEPTION: {e}")
        print()

    async def cleanup(self):
        if not self.created_shares:
            return
        print("Cleanup")
        print("-" * 70)
        for sid in self.created_shares:
            try:
                with self._client() as c:
                    r = c.delete(
                        f"{self.server_url}/v1/shares/{sid}",
                        headers={"Authorization": f"Bearer {self.jwt_a}"},
                    )
                    sym = "+" if r.status_code in (200, 204) else "x"
                    print(f"  -> Delete share {sid[:8]}... {sym}")
            except Exception as e:
                print(f"  -> Delete share {sid[:8]}... x ({e.__class__.__name__})")
        print()


async def main():
    parser = argparse.ArgumentParser(description="Folder Sync E2E Tests")
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

    tests = FolderSyncTests(
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
