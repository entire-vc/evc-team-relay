"""Regression tests; no production access or credentials required."""

import copy
import importlib.util
from pathlib import Path

import pycrdt
import pytest

spec = importlib.util.spec_from_file_location(
    "relocate", Path(__file__).with_name("relocate_artifacts.py")
)
relocate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(relocate)


def fixture():
    doc = pycrdt.Doc()
    meta = doc.get("filemeta_v0", type=pycrdt.Map)
    legacy = doc.get("docs", type=pycrdt.Map)
    with doc.transaction():
        meta["deadbeef"] = {"type": "folder", "id": "folder-id", "version": 0}
        meta["deadbeef/note.md"] = {"type": "markdown", "id": "note-id", "version": 0}
        legacy["deadbeef/note.md"] = "note-id"
        meta["deadbeef/file.bin"] = {
            "type": "file",
            "id": "binary-id",
            "version": 0,
            "hash": "hash-unchanged",
            "mimetype": "application/octet-stream",
        }
        meta["specs"] = {"type": "folder", "id": "specs-id", "version": 0}
    items = [
        {
            "path": "deadbeef/note.md",
            "name": "note.md",
            "type": "doc",
            "storage_key": "sync-uploads/share/hash",
            "sha256": "hash",
            "content": "body",
        },
        {
            "path": "deadbeef/file.bin",
            "name": "file.bin",
            "type": "asset",
            "storage_key": "web-assets/share/deadbeef/file.bin",
            "sha256": "binary-hash",
        },
    ]
    return doc, items


@pytest.mark.parametrize("scheme", ["ws", "wss"])
def test_relay_uses_registered_http_routes(scheme):
    import httpx

    share_id = "test-share"
    calls = []

    def request(req):
        if req.url.path == "/v1/auth/me":
            return httpx.Response(200, json={"id": "owner"})
        if req.url.path == f"/v1/shares/{share_id}":
            return httpx.Response(
                200, json={"owner_user_id": "owner", "kind": "folder"}
            )
        if req.url.path == "/v1/tokens/relay":
            return httpx.Response(
                200,
                json={
                    "relay_url": f"{scheme}://relay.test/d/{share_id}/ws",
                    "token": "test",
                },
            )
        if req.url.path == f"/d/{share_id}/as-update" and req.method == "GET":
            calls.append(req.url)
            return httpx.Response(200, content=b"snapshot")
        if req.url.path == f"/d/{share_id}/update" and req.method == "POST":
            assert req.content == b"update"
            calls.append(req.url)
            return httpx.Response(200)
        return httpx.Response(404)

    with httpx.Client(transport=httpx.MockTransport(request)) as client:
        for method, suffix in [("GET", "as-update"), ("POST", "update")]:
            response = client.request(
                method, f"http://relay.test/d/{share_id}//{suffix}"
            )
            with pytest.raises(httpx.HTTPStatusError):
                response.raise_for_status()
        print(f"RED CONTROL: {scheme} doubled-slash GET/POST routes rejected")
        relay = relocate.Relay(client, "http://cp.test", "test", share_id)
        assert relay.snapshot() == b"snapshot"
        relay.post(b"update")
    assert len(calls) == 2
    assert all(url.scheme == {"ws": "http", "wss": "https"}[scheme] for url in calls)
    print(f"GREEN: {scheme} snapshot and update use exact registered HTTP routes")


def test_offline_peer_negative_control_and_atomic_move(tmp_path):
    server, items = fixture()
    offline = relocate.load_doc(server.get_update())
    # Naive API/index deletion changes no CRDT. Reconnecting republishes old paths.
    naive_index = []
    offline.apply_update(server.get_update(offline.get_state()))
    naive_index.extend({"path": p} for p in relocate.maps(offline)["docs"])
    with pytest.raises(AssertionError):
        assert not any(p["path"].startswith("deadbeef/") for p in naive_index)
    print("RED CONTROL: index-only deletion resurrects deadbeef/note.md")
    before = server.get_update()
    plan = relocate.prepare(before, items, "argus")
    assert server.get_update() == before
    server.apply_update(plan["update"])
    offline.apply_update(server.get_update(offline.get_state()))
    server.apply_update(offline.get_update(server.get_state()))
    assert relocate.maps(offline) == relocate.maps(server) == plan["after_maps"]
    assert set(relocate.maps(offline)["docs"]) == {"artifacts/deadbeef/note.md"}
    assert (
        relocate.maps(offline)["filemeta_v0"]["artifacts/deadbeef"]["id"] == "folder-id"
    )
    for old, new in zip(items, plan["after_index"], strict=True):
        assert {k: v for k, v in old.items() if k != "path"} == {
            k: v for k, v in new.items() if k != "path"
        }
    assert len(items) == len(plan["after_index"])
    repeat = relocate.prepare(server.get_update(), plan["after_index"], "argus")
    assert repeat["moves"] == [] and repeat["after_maps"] == repeat["before_maps"]
    rollback = relocate.prepare(
        server.get_update(),
        plan["after_index"],
        "argus",
        {v: k for k, v in plan["mapping"].items()},
    )
    offline.apply_update(rollback["update"])
    assert relocate.maps(offline)["docs"] == {"deadbeef/note.md": "note-id"}
    assert rollback["after_index"] == items
    # Cross-runtime artifacts for the actual plugin FolderIndex harness.
    (tmp_path / "before.yjs").write_bytes(before)
    (tmp_path / "move.yjs").write_bytes(plan["update"])
    print("GREEN: offline CRDT peer, IDs, storage metadata, repeat and inverse move")


@pytest.mark.parametrize("path", ["/bad", "../bad", "a//b", "a/./b", "a\\b", "a\x00b"])
def test_bad_paths(path):
    with pytest.raises(relocate.Refused):
        relocate.checked_path(path)


def test_collisions_and_missing_metadata_write_nothing():
    doc, items = fixture()
    before = doc.get_update()
    with pytest.raises(relocate.Refused, match="no CRDT metadata"):
        relocate.prepare(before, [*items, {"path": "deadbeef/missing.md"}], "argus")
    meta = doc.get("filemeta_v0", type=pycrdt.Map)
    meta["artifacts/deadbeef/note.md"] = {
        "type": "markdown",
        "id": "other",
        "version": 0,
    }
    snapshot = doc.get_update()
    with pytest.raises(relocate.Refused, match="collision"):
        relocate.prepare(snapshot, items, "argus")
    assert doc.get_update() == snapshot
    doc, items = fixture()
    with pytest.raises(relocate.Refused, match="collision"):
        relocate.prepare(
            doc.get_update(), [*items, {"path": "artifacts/deadbeef/note.md"}], "argus"
        )
    with pytest.raises(relocate.Refused, match="Duplicate"):
        relocate.prepare(doc.get_update(), [*items, items[0]], "argus")


def test_selection_and_suffix():
    assert relocate.selected("ABCDEF12__notes", "argus")
    assert relocate.selected("_probe", "mesh")
    assert not relocate.selected("_probe", "argus")
    assert relocate.selected("_legacy-check", "mesh", ("_legacy-check",))
    assert not relocate.selected("_legacy-check", "argus", ("_legacy-check",))
    doc, items = fixture()
    doc.get("filemeta_v0", type=pycrdt.Map)["_legacy-check"] = {
        "type": "folder",
        "id": "diagnostic-id",
        "version": 0,
    }
    planned = relocate.prepare(
        doc.get_update(), items, "mesh", extra_roots=("_legacy-check",)
    )
    assert planned["mapping"]["_legacy-check"] == "artifacts/_legacy-check"
    with pytest.raises(relocate.Refused, match="underscore"):
        relocate.prepare(doc.get_update(), items, "mesh", extra_roots=("knowledge",))
    for name in (
        "specs",
        "adrs",
        "knowledge",
        "dev-docs",
        "archive",
        "artifacts",
        "deadbeef.md",
    ):
        assert not relocate.selected(name, "mesh")


def test_id_mismatch_nested_shared_type_and_missing_folder():
    doc, items = fixture()
    doc.get("docs", type=pycrdt.Map)["deadbeef/note.md"] = "wrong"
    with pytest.raises(relocate.Refused, match="mismatch"):
        relocate.prepare(doc.get_update(), items, "argus")
    doc, items = fixture()
    doc.get("filemeta_v0", type=pycrdt.Map)["deadbeef/note.md"] = pycrdt.Map(
        {"id": "note-id"}
    )
    with pytest.raises(relocate.Refused, match="Unsupported"):
        relocate.prepare(doc.get_update(), items, "argus")
    doc, items = fixture()
    del doc.get("filemeta_v0", type=pycrdt.Map)["deadbeef"]
    with pytest.raises(relocate.Refused, match="no CRDT ID"):
        relocate.prepare(doc.get_update(), items, "argus")


def test_corrupt_journal(tmp_path):
    for name in (
        "journal.json",
        "move.yjs",
        "folder-before.yjs",
        "database.dump",
        "relay-data.tar",
    ):
        relocate.write_private(tmp_path / name, b"bad")
    relocate.dump_json(tmp_path / "checksums.json", {"move.yjs": "wrong"})
    with pytest.raises(relocate.Refused, match="checksum"):
        relocate.verify_journal(tmp_path, "unused")
    assert (tmp_path / "move.yjs").stat().st_mode & 0o777 == 0o600


def test_duplicate_ids_and_artifacts_file_refused():
    doc, items = fixture()
    meta = doc.get("filemeta_v0", type=pycrdt.Map)
    meta["specs"] = {"type": "folder", "id": "folder-id", "version": 0}
    with pytest.raises(relocate.Refused, match="Duplicate"):
        relocate.prepare(doc.get_update(), items, "argus")
    for legacy in (False, True):
        doc, items = fixture()
        if legacy:
            doc.get("docs", type=pycrdt.Map)["artifacts"] = "file-id"
        else:
            doc.get("filemeta_v0", type=pycrdt.Map)["artifacts"] = {
                "id": "file-id",
                "type": "file",
                "version": 0,
            }
        with pytest.raises(relocate.Refused, match="exists as a file"):
            relocate.prepare(doc.get_update(), items, "argus")


def test_cli_backup_apply_resume_rollback(postgres_engine, tmp_path, monkeypatch):
    import os
    import shutil
    import subprocess

    import httpx
    from sqlalchemy import JSON, Column, DateTime, MetaData, String, Table, Uuid, select

    container = os.environ.get("RELOCATION_TEST_PGDUMP_DOCKER")
    if not container and not all(
        shutil.which(name) for name in ("pg_dump", "pg_restore")
    ):
        pytest.skip("Install PostgreSQL clients or set RELOCATION_TEST_PGDUMP_DOCKER")
    real_run = subprocess.run

    def run(command, **kwargs):
        if container and command[0] == "pg_dump":
            return real_run(
                [
                    "docker",
                    "exec",
                    container,
                    "pg_dump",
                    "-U",
                    "relocation",
                    "-d",
                    "artifact_relocation_test",
                    "--format=custom",
                ],
                **kwargs,
            )
        if container and command[0] == "pg_restore":
            with open(command[-1], "rb") as stream:
                return real_run(
                    ["docker", "exec", "-i", container, "pg_restore", "--list"],
                    stdin=stream,
                    **kwargs,
                )
        return real_run(command, **kwargs)

    monkeypatch.setattr(relocate.subprocess, "run", run)
    metadata = MetaData()
    shares = Table(
        "shares",
        metadata,
        Column("id", Uuid, primary_key=True),
        Column("owner_user_id", Uuid),
        Column("path", String),
        Column("kind", String),
        Column("web_folder_items", JSON),
        Column("updated_at", DateTime),
        Column("web_content_updated_at", DateTime),
    )
    Table(
        "audit_logs",
        metadata,
        Column("id", Uuid, primary_key=True),
        Column("action", String),
        Column("actor_user_id", Uuid),
        Column("target_share_id", Uuid),
        Column("details", JSON),
    )
    metadata.create_all(postgres_engine)
    owner, share_id = relocate.uuid.uuid4(), relocate.uuid.uuid4()
    doc, items = fixture()
    with postgres_engine.begin() as connection:
        connection.execute(
            shares.insert().values(
                id=share_id,
                owner_user_id=owner,
                path="_relocation-test-cli",
                kind="folder",
                web_folder_items=items,
            )
        )
    posts = []
    unknown_post = [False]

    def request(req):
        if req.url.path == "/v1/auth/me":
            return httpx.Response(200, json={"id": str(owner)})
        if req.url.path == f"/v1/shares/{share_id}":
            return httpx.Response(
                200,
                json={
                    "id": str(share_id),
                    "owner_user_id": str(owner),
                    "path": "_relocation-test-cli",
                    "kind": "folder",
                },
            )
        if req.url.path == "/v1/tokens/relay":
            return httpx.Response(
                200,
                json={
                    "relay_url": f"ws://localhost/d/{share_id}/ws",
                    "token": "test-only",
                },
            )
        if req.url.path == f"/d/{share_id}/as-update":
            return httpx.Response(200, content=doc.get_update())
        if req.url.path == f"/d/{share_id}/update":
            posts.append(req.content)
            doc.apply_update(req.content)
            if unknown_post[0]:
                unknown_post[0] = False
                raise httpx.ReadError("Unknown test POST outcome", request=req)
            return httpx.Response(200)
        return httpx.Response(404)

    real_client = httpx.Client
    monkeypatch.setattr(
        relocate.httpx,
        "Client",
        lambda **kwargs: real_client(transport=httpx.MockTransport(request), **kwargs),
    )
    monkeypatch.setenv("TR_OWNER_TOKEN", "test-only")
    monkeypatch.setenv("DATABASE_URL", os.environ["RELOCATION_TEST_DATABASE_URL"])
    base = ["--share-id", str(share_id)]
    assert relocate.main(base) == 0 and posts == []
    relay_data = tmp_path / "storage"
    relay_data.mkdir()
    (relay_data / "doc.ysweet").write_bytes(doc.get_update())
    backup = tmp_path / "backup"
    monkeypatch.setattr(
        relocate.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args[0], 1),
    )
    assert (
        relocate.main(
            [
                *base,
                "--apply",
                "--backup-dir",
                str(tmp_path / "failed-backup"),
                "--relay-data-dir",
                str(relay_data),
            ]
        )
        == 1
    )
    assert posts == []
    print("RED CONTROL: failed pg_dump refuses all writes")
    monkeypatch.setattr(relocate.subprocess, "run", run)
    assert (
        relocate.main(
            [
                *base,
                "--apply",
                "--backup-dir",
                str(backup),
                "--relay-data-dir",
                str(relay_data),
            ]
        )
        == 0
    )
    assert len(posts) == 1
    journal = relocate.verify_journal(backup, str(share_id))
    assert journal["before_index"] == items
    for mode in ([], ["--apply"], ["--resume", str(backup)]):
        assert relocate.main([*base, *mode]) == 0
    assert len(posts) == 1
    unknown_post[0] = True
    assert relocate.main([*base, "--rollback", str(backup)]) == 1
    print(
        "RED CONTROL: rollback POST applied but response lost; SQL index remains moved"
    )
    assert relocate.main([*base, "--rollback", str(backup)]) == 0
    assert relocate.main([*base, "--rollback", str(backup)]) == 0
    assert relocate.main([*base, "--resume", str(backup / "rollback")]) == 0
    assert len(posts) == 2
    with postgres_engine.connect() as connection:
        assert (
            connection.execute(select(shares.c.web_folder_items)).scalar_one() == items
        )
        assert len(connection.execute(select(metadata.tables["audit_logs"])).all()) == 2
    assert relocate.maps(doc)["docs"] == {"deadbeef/note.md": "note-id"}
    (backup / "move.yjs").write_bytes(b"corrupt")
    assert relocate.main([*base, "--resume", str(backup)]) == 1
    assert len(posts) == 2
    print(
        "GREEN: complete CLI dry-run/apply/no-op/resume/rollback, real pg_dump+pg_restore+tar, corrupt backup refused"
    )


def test_finish_resume_after_unknown_post_and_row_lock(postgres_engine):
    from sqlalchemy import JSON, Column, DateTime, MetaData, String, Table, Uuid, select

    metadata = MetaData()
    shares = Table(
        "shares",
        metadata,
        Column("id", Uuid, primary_key=True),
        Column("owner_user_id", Uuid),
        Column("web_folder_items", JSON),
        Column("updated_at", DateTime),
        Column("web_content_updated_at", DateTime),
    )
    audits = Table(
        "audit_logs",
        metadata,
        Column("id", Uuid, primary_key=True),
        Column("action", String),
        Column("actor_user_id", Uuid),
        Column("target_share_id", Uuid),
        Column("details", JSON),
    )
    metadata.create_all(postgres_engine)
    owner = relocate.uuid.uuid4()
    key = relocate.uuid.uuid4()
    doc, items = fixture()
    plan = relocate.prepare(doc.get_update(), items, "argus")

    class TestRelay:
        fail_once = True

        def __init__(self):
            self.owner = str(owner)

        def snapshot(self):
            return doc.get_update()

        def post(self, update):
            doc.apply_update(update)
            if self.fail_once:
                self.fail_once = False
                raise relocate.Refused("Unknown POST result")

    relay = TestRelay()
    with postgres_engine.begin() as connection:
        connection.execute(
            shares.insert().values(id=key, owner_user_id=owner, web_folder_items=items)
        )
    with (
        pytest.raises(relocate.Refused, match="Unknown"),
        postgres_engine.begin() as connection,
    ):
        relocate.finish(connection, shares, audits, key, relay, plan)
    with postgres_engine.connect() as connection:
        assert (
            connection.execute(
                select(shares.c.web_folder_items).where(shares.c.id == key)
            ).scalar_one()
            == items
        )
    for _ in range(2):
        with postgres_engine.begin() as connection:
            relocate.finish(connection, shares, audits, key, relay, plan)
    with postgres_engine.connect() as connection:
        assert (
            connection.execute(
                select(shares.c.web_folder_items).where(shares.c.id == key)
            ).scalar_one()
            == plan["after_index"]
        )
        assert len(connection.execute(select(audits)).all()) == 1
    tampered = copy.deepcopy(plan)
    tampered["before_index"] = []
    tampered["after_index"] = []
    with (
        pytest.raises(relocate.Refused, match="Index changed"),
        postgres_engine.begin() as connection,
    ):
        relocate.finish(connection, shares, audits, key, relay, tampered)
    print("GREEN: PostgreSQL index rollback, unknown POST resume and idempotent audit")


@pytest.fixture
def postgres_engine():
    import os

    from sqlalchemy import create_engine

    url = os.environ.get("RELOCATION_TEST_DATABASE_URL")
    if not url:
        pytest.skip(
            "Set RELOCATION_TEST_DATABASE_URL to a disposable PostgreSQL database"
        )
    parsed = relocate.make_url(url)
    allowed_host = parsed.host == "127.0.0.1" or (
        os.environ.get("CI") == "true" and parsed.host == "postgres"
    )
    if not allowed_host or parsed.database != "artifact_relocation_test":
        pytest.fail(
            "Integration tests require loopback artifact_relocation_test database"
        )
    engine = create_engine(url)
    yield engine
    from sqlalchemy import MetaData

    metadata = MetaData()
    metadata.reflect(bind=engine)
    metadata.drop_all(engine)
    engine.dispose()
