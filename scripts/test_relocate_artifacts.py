"""Regression tests; no production access or credentials required."""

import copy
import importlib.util
import uuid
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


def _operator_client(*, is_admin, role, owner_flag=False):
    import httpx

    share_id = "test-share"

    def request(req):
        if req.url.path == "/v1/auth/me":
            return httpx.Response(
                200,
                json={
                    "id": "owner" if owner_flag else "operator",
                    "is_admin": is_admin,
                },
            )
        if req.url.path == f"/v1/shares/{share_id}":
            return httpx.Response(
                200, json={"owner_user_id": "owner", "kind": "folder"}
            )
        if req.url.path == f"/v1/shares/{share_id}/members":
            members = (
                []
                if role is None
                else [
                    {"user_id": "operator", "role": role},
                    {"user_id": "someone", "role": "editor"},
                ]
            )
            return httpx.Response(200, json=members)
        if req.url.path == "/v1/tokens/relay":
            return httpx.Response(
                200,
                json={"relay_url": f"ws://relay.test/d/{share_id}/ws", "token": "t"},
            )
        return httpx.Response(404)

    return httpx.Client(transport=httpx.MockTransport(request)), share_id


def test_non_owner_refused_without_operator_flag():
    client, share_id = _operator_client(is_admin=True, role="editor")
    with client, pytest.raises(relocate.Refused):
        relocate.Relay(client, "http://cp.test", "t", share_id)
    print("RED CONTROL: non-owner admin editor refused without --operator-admin")


def test_operator_admin_accepts_admin_editor_and_records_actor():
    client, share_id = _operator_client(is_admin=True, role="editor")
    with client:
        relay = relocate.Relay(
            client, "http://cp.test", "t", share_id, operator_admin=True
        )
    assert relay.owner == "owner" and relay.actor == "operator"
    print(
        "GREEN: --operator-admin accepts admin editor; DB owner check uses share owner, audit actor is operator"
    )


@pytest.mark.parametrize(
    "is_admin,role",
    [(False, "editor"), (True, "viewer"), (True, None)],
)
def test_operator_admin_refuses_non_admin_or_non_editor(is_admin, role):
    client, share_id = _operator_client(is_admin=is_admin, role=role)
    with client, pytest.raises(relocate.Refused):
        relocate.Relay(client, "http://cp.test", "t", share_id, operator_admin=True)


def test_operator_flag_with_owner_token_still_works():
    client, share_id = _operator_client(is_admin=False, role=None, owner_flag=True)
    with client:
        relay = relocate.Relay(
            client, "http://cp.test", "t", share_id, operator_admin=True
        )
    assert relay.actor == relay.owner == "owner"


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


def test_unregistered_files_refused_by_default_moved_in_index_only_with_flag():
    doc, items = fixture()
    api_file = {
        "path": "deadbeef/api-upload.md",
        "name": "api-upload.md",
        "type": "doc",
        "storage_key": "sync-uploads/share/api",
        "sha256": "api-hash",
    }
    mixed = [*items, api_file]
    before = doc.get_update()
    with pytest.raises(relocate.Refused, match="no CRDT metadata"):
        relocate.prepare(before, mixed, "argus")
    plan = relocate.prepare(before, mixed, "argus", allow_unregistered=True)
    assert doc.get_update() == before
    # Registered files move in the CRDT and the index; the API upload moves in the
    # index only and gains no CRDT entry.
    after = plan["after_maps"]
    assert "artifacts/deadbeef/note.md" in after["filemeta_v0"]
    assert not any("api-upload" in p for p in after["filemeta_v0"])
    assert [i["path"] for i in plan["after_index"]][-1] == (
        "artifacts/deadbeef/api-upload.md"
    )
    assert {k: v for k, v in plan["after_index"][-1].items() if k != "path"} == {
        k: v for k, v in api_file.items() if k != "path"
    }
    assert ["deadbeef/api-upload.md", "artifacts/deadbeef/api-upload.md"] in plan[
        "moves"
    ]
    # Idempotent: a second pass over the moved state plans nothing.
    server = relocate.load_doc(before)
    server.apply_update(plan["update"])
    repeat = relocate.prepare(
        server.get_update(), plan["after_index"], "argus", allow_unregistered=True
    )
    assert repeat["moves"] == [] and repeat["after_index"] == plan["after_index"]
    # Rollback restores the exact original index, files without metadata included.
    rollback = relocate.prepare(
        server.get_update(),
        plan["after_index"],
        "argus",
        {v: k for k, v in plan["mapping"].items()},
        allow_unregistered=True,
    )
    assert rollback["after_index"] == mixed


def test_unregistered_folder_entry_refused_even_with_flag():
    doc, items = fixture()
    folder = {"path": "deadbeef/sub", "name": "sub", "type": "folder"}
    with pytest.raises(relocate.Refused, match="Unregistered folder"):
        relocate.prepare(
            doc.get_update(), [*items, folder], "argus", allow_unregistered=True
        )


def test_docs_only_entry_refused_even_with_flag():
    # A path without filemeta_v0 but present in the legacy docs map would still be
    # moved in CRDT state by the map loop, so it is not "index-only" and is refused.
    doc, items = fixture()
    api_file = {"path": "deadbeef/legacy.md", "type": "doc"}
    doc.get("docs", type=pycrdt.Map)["deadbeef/legacy.md"] = "legacy-id"
    snapshot = doc.get_update()
    with pytest.raises(relocate.Refused, match="legacy docs"):
        relocate.prepare(snapshot, [*items, api_file], "argus", allow_unregistered=True)
    assert doc.get_update() == snapshot


def placeholder_fixture(children_in_crdt=False, children_in_index=False):
    doc, items = fixture()
    meta = doc.get("filemeta_v0", type=pycrdt.Map)
    with doc.transaction():
        meta["artifacts"] = {"type": "folder", "id": "artifacts-id", "version": 0}
        meta["artifacts/deadbeef"] = {"type": "folder", "id": "empty-id", "version": 0}
        if children_in_crdt:
            meta["artifacts/deadbeef/x.md"] = {
                "type": "markdown",
                "id": "x",
                "version": 0,
            }
    if children_in_index:
        items = [*items, {"path": "artifacts/deadbeef/x.md", "type": "doc"}]
    return doc, items


def test_empty_destination_folder_refused_by_default_with_diagnostics():
    doc, items = placeholder_fixture()
    snapshot = doc.get_update()
    with pytest.raises(relocate.Refused) as caught:
        relocate.prepare(snapshot, items, "argus")
    message = str(caught.value)
    assert "collision" in message and "artifacts/deadbeef" in message
    assert "destination type=folder id=empty-id" in message
    assert "crdt_children=0 index_children=0" in message
    assert "source type=folder id=folder-id in_docs=False" in message
    assert "crdt_children=2 index_children=2" in message
    assert doc.get_update() == snapshot


def test_empty_destination_folder_merged_with_flag_keeping_source_id():
    doc, items = placeholder_fixture()
    before = doc.get_update()
    plan = relocate.prepare(before, items, "argus", merge_empty_destinations=True)
    assert doc.get_update() == before
    after = plan["after_maps"]["filemeta_v0"]
    assert after["artifacts/deadbeef"]["id"] == "folder-id"
    assert "deadbeef" not in after and "artifacts/deadbeef/note.md" in after
    assert plan["merged_empty_destinations"] == ["artifacts/deadbeef"]
    assert [i["path"] for i in plan["after_index"]] == [
        "artifacts/deadbeef/note.md",
        "artifacts/deadbeef/file.bin",
    ]
    # The merged state is a fixed point: nothing further to move.
    server = relocate.load_doc(before)
    server.apply_update(plan["update"])
    repeat = relocate.prepare(
        server.get_update(),
        plan["after_index"],
        "argus",
        merge_empty_destinations=True,
    )
    assert repeat["moves"] == []


@pytest.mark.parametrize("crdt,index", [(True, False), (False, True)])
def test_non_empty_destination_folder_refused_even_with_merge_flag(crdt, index):
    doc, items = placeholder_fixture(crdt, index)
    with pytest.raises(relocate.Refused, match="collision"):
        relocate.prepare(
            doc.get_update(), items, "argus", merge_empty_destinations=True
        )


def duplicate_fixture(sha="hash"):
    doc, items = fixture()
    meta = doc.get("filemeta_v0", type=pycrdt.Map)
    legacy = doc.get("docs", type=pycrdt.Map)
    with doc.transaction():
        meta["artifacts"] = {"type": "folder", "id": "artifacts-id", "version": 0}
        meta["artifacts/deadbeef"] = {"type": "folder", "id": "copy-id", "version": 0}
        meta["artifacts/deadbeef/note.md"] = {
            "type": "markdown",
            "id": "copy-note",
            "version": 0,
        }
        legacy["artifacts/deadbeef/note.md"] = "copy-note"
        meta["artifacts/deadbeef/file.bin"] = {
            "type": "file",
            "id": "copy-bin",
            "version": 0,
        }
    copies = [
        {**items[0], "path": "artifacts/deadbeef/note.md", "sha256": sha},
        {**items[1], "path": "artifacts/deadbeef/file.bin"},
    ]
    return doc, [*items, *copies]


def test_duplicate_root_refused_by_default():
    doc, items = duplicate_fixture()
    with pytest.raises(relocate.Refused, match="collision"):
        relocate.prepare(doc.get_update(), items, "argus")


def test_duplicate_root_removed_in_crdt_and_index_when_copies_identical():
    doc, items = duplicate_fixture()
    before = doc.get_update()
    plan = relocate.prepare(before, items, "argus", remove_duplicates=True)
    assert doc.get_update() == before
    after = plan["after_maps"]
    assert not any(p.split("/")[0] == "deadbeef" for p in after["filemeta_v0"])
    assert not any(p.split("/")[0] == "deadbeef" for p in after["docs"])
    # The copies keep their IDs, and unrelated entries are untouched.
    assert after["filemeta_v0"]["artifacts/deadbeef/note.md"]["id"] == "copy-note"
    assert "specs" in after["filemeta_v0"]
    assert [i["path"] for i in plan["after_index"]] == [
        "artifacts/deadbeef/note.md",
        "artifacts/deadbeef/file.bin",
    ]
    assert plan["moves"] == []
    assert "deadbeef/note.md" in plan["removed_duplicates"]["crdt"]
    assert "deadbeef/file.bin" in plan["removed_duplicates"]["index"]
    server = relocate.load_doc(before)
    server.apply_update(plan["update"])
    repeat = relocate.prepare(
        server.get_update(), plan["after_index"], "argus", remove_duplicates=True
    )
    assert repeat["after_index"] == plan["after_index"]
    assert repeat["removed_duplicates"] == {"crdt": [], "index": []}


def test_duplicate_root_kept_when_a_copy_differs_or_is_missing():
    doc, items = duplicate_fixture(sha="other-hash")
    with pytest.raises(relocate.Refused, match="differs in sha256"):
        relocate.prepare(doc.get_update(), items, "argus", remove_duplicates=True)
    doc, items = duplicate_fixture()
    del doc.get("filemeta_v0", type=pycrdt.Map)["artifacts/deadbeef/file.bin"]
    with pytest.raises(relocate.Refused, match="no matching copy at the destination"):
        relocate.prepare(doc.get_update(), items, "argus", remove_duplicates=True)
    doc, items = duplicate_fixture()
    with pytest.raises(relocate.Refused, match="no matching copy at the destination"):
        relocate.prepare(doc.get_update(), items[:3], "argus", remove_duplicates=True)


def test_duplicate_root_refused_when_source_file_has_no_index_row():
    doc, items = duplicate_fixture()
    unindexed = [i for i in items if i["path"] != "deadbeef/note.md"]
    with pytest.raises(relocate.Refused, match="no matching copy|no index row"):
        relocate.prepare(doc.get_update(), unindexed, "argus", remove_duplicates=True)


def test_duplicate_root_refused_when_index_row_is_typed_as_folder():
    # A folder-typed index row must not bypass the sha256 proof for a CRDT file.
    doc, items = duplicate_fixture()
    typed = [
        {**i, "type": "folder"} if i["path"] == "deadbeef/note.md" else i for i in items
    ]
    with pytest.raises(relocate.Refused, match="folder in the index"):
        relocate.prepare(doc.get_update(), typed, "argus", remove_duplicates=True)


def test_merge_and_duplicate_flags_default_off():
    args = relocate.parser().parse_args(["--share-id", str(uuid.uuid4())])
    assert not args.merge_empty_destination_folders
    assert not args.remove_duplicate_roots


def test_unregistered_flag_keeps_other_protections():
    doc, items = fixture()
    api_file = {"path": "deadbeef/api-upload.md", "type": "doc"}
    meta = doc.get("filemeta_v0", type=pycrdt.Map)
    meta["artifacts/deadbeef/api-upload.md"] = {
        "type": "markdown",
        "id": "twin",
        "version": 0,
    }
    with pytest.raises(relocate.Refused, match="collision"):
        relocate.prepare(
            doc.get_update(), [*items, api_file], "argus", allow_unregistered=True
        )
    doc, items = fixture()
    del doc.get("filemeta_v0", type=pycrdt.Map)["deadbeef"]
    with pytest.raises(relocate.Refused, match="no CRDT ID"):
        relocate.prepare(
            doc.get_update(), [*items, api_file], "argus", allow_unregistered=True
        )
    assert relocate.parser().parse_args(["--share-id", str(uuid.uuid4())])
    assert (
        not relocate.parser()
        .parse_args(["--share-id", str(uuid.uuid4())])
        .allow_unregistered_files
    )


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
    # Retention: only the backup whose run reached VERIFIED is ever removed.
    assert (backup / relocate.VERIFIED_MARKER).is_file()
    assert not (tmp_path / "failed-backup" / relocate.VERIFIED_MARKER).exists()
    retained = ["--backup-dir", str(backup), "--keep-local-days", "0"]
    assert relocate.main([*base, *retained]) == 0
    assert backup.is_dir()
    print("RED CONTROL: --dry-run only lists the expired backup")
    # A no-op resume of the applied inverse journal is the "next run".
    inverse = ["--resume", str(backup / "rollback"), "--keep-local-days", "0"]
    assert relocate.main([*base, *inverse]) == 0
    assert not backup.exists()
    assert (tmp_path / "failed-backup").is_dir()
    print(
        "GREEN: complete CLI dry-run/apply/no-op/resume/rollback, real pg_dump+pg_restore+tar, "
        "corrupt backup refused, verified backup pruned"
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
            self.actor = str(owner)

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


def retention_fixture(root, name, verified_at=None):
    directory = root / name
    directory.mkdir()
    (directory / "database.dump").write_bytes(b"dump")
    relocate.write_manifest(directory)
    if verified_at is not None:
        relocate.mark_verified(directory, verified_at)
    return directory


def test_retention_removes_only_verified_backups_past_the_window(tmp_path, capsys):
    from datetime import timedelta

    now = relocate.datetime.now(relocate.UTC)
    old = retention_fixture(tmp_path, "mesh-1", now - timedelta(days=4))
    fresh = retention_fixture(tmp_path, "mesh-2", now - timedelta(days=1))
    failed = retention_fixture(tmp_path, "mesh-3")
    (tmp_path / "unrelated").mkdir()
    relocate.prune_backups(tmp_path, 3, now, dry_run=True)
    assert old.is_dir()
    assert capsys.readouterr().out == f"would_remove_backup={old}\n"
    relocate.prune_backups(tmp_path, 3, now, dry_run=False)
    assert not old.exists()
    assert fresh.is_dir() and failed.is_dir() and (tmp_path / "unrelated").is_dir()
    relocate.prune_backups(tmp_path, 0, now, dry_run=False)
    assert not fresh.exists()
    assert failed.is_dir(), "a backup whose run never reached VERIFIED is kept"


def test_retention_ignores_corrupt_marker_and_marks_rollback_owner(tmp_path):
    now = relocate.datetime.now(relocate.UTC)
    corrupt = retention_fixture(tmp_path, "mesh-1")
    (corrupt / relocate.VERIFIED_MARKER).write_bytes(b"not json")
    owner = retention_fixture(tmp_path, "mesh-2")
    inverse = owner / "rollback"
    inverse.mkdir()
    relocate.mark_verified(inverse, now)
    relocate.mark_verified(inverse, now)
    assert (owner / relocate.VERIFIED_MARKER).is_file()
    assert not (inverse / relocate.VERIFIED_MARKER).exists()
    relocate.prune_backups(tmp_path, 0, now, dry_run=False)
    assert corrupt.is_dir() and not owner.exists()


def test_keep_local_days_rejects_negative():
    with pytest.raises(SystemExit):
        relocate.parser().parse_args(
            ["--share-id", str(uuid.uuid4()), "--keep-local-days", "-1"]
        )
    args = relocate.parser().parse_args(["--share-id", str(uuid.uuid4())])
    assert args.keep_local_days == 3


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


def test_duplicate_root_refused_when_destination_index_twin_is_a_folder():
    # A folder-typed destination row must not satisfy the sha256 match.
    doc, items = duplicate_fixture()
    typed = [
        {**i, "type": "folder"} if i["path"] == "artifacts/deadbeef/note.md" else i
        for i in items
    ]
    with pytest.raises(relocate.Refused, match="folder in the index"):
        relocate.prepare(doc.get_update(), typed, "argus", remove_duplicates=True)


def test_duplicate_root_refused_when_index_only_source_has_no_crdt_copy():
    # An index-only source row with a matching destination index row must not
    # pass: the CRDT type of the copy cannot be verified.
    doc, items = duplicate_fixture()
    source = items[0]["path"].split("/")[0]
    extra = [
        {**items[0], "path": f"{source}/ghost.md", "sha256": "same"},
        {**items[0], "path": "artifacts/deadbeef/ghost.md", "sha256": "same"},
    ]
    with pytest.raises(relocate.Refused, match="no CRDT record"):
        relocate.prepare(
            doc.get_update(),
            [*items, *extra],
            "argus",
            allow_unregistered=True,
            remove_duplicates=True,
        )


def test_duplicate_root_refused_when_docs_only_source_has_no_crdt_record():
    # A docs-map entry without file metadata must not be removed on the strength
    # of a same-path entry at the destination.
    doc, items = duplicate_fixture()
    source = items[0]["path"].split("/")[0]
    legacy = doc.get("docs", type=pycrdt.Map)
    with doc.transaction():
        legacy[f"{source}/ghost.md"] = "ghost-src"
        legacy["artifacts/deadbeef/ghost.md"] = "ghost-dst"
    with pytest.raises(relocate.Refused, match="no CRDT record"):
        relocate.prepare(doc.get_update(), items, "argus", remove_duplicates=True)


def test_duplicate_root_refused_when_index_twin_has_a_different_type():
    # Same sha256 but a differently typed index twin is not an identical copy.
    doc, items = duplicate_fixture()
    typed = [
        {**i, "type": "other"} if i["path"] == "artifacts/deadbeef/note.md" else i
        for i in items
    ]
    with pytest.raises(relocate.Refused, match="differs in index type"):
        relocate.prepare(doc.get_update(), typed, "argus", remove_duplicates=True)


def test_duplicate_root_refused_when_source_root_is_not_a_folder():
    # The selected root itself must be verified, not only its descendants.
    doc, items = duplicate_fixture()
    source = items[0]["path"].split("/")[0]
    meta = doc.get("filemeta_v0", type=pycrdt.Map)
    with doc.transaction():
        meta[source] = {"type": "file", "id": "root-id", "version": 0}
    with pytest.raises(relocate.Refused, match="not a folder record"):
        relocate.prepare(doc.get_update(), items, "argus", remove_duplicates=True)


def test_duplicate_root_refused_when_source_root_index_row_is_a_file():
    doc, items = duplicate_fixture()
    source = items[0]["path"].split("/")[0]
    rows = [*items, {**items[0], "path": source, "type": "file", "sha256": "x"}]
    with pytest.raises(relocate.Refused, match="source root"):
        relocate.prepare(
            doc.get_update(),
            rows,
            "argus",
            allow_unregistered=True,
            remove_duplicates=True,
        )


def test_duplicate_root_refused_when_root_index_row_has_no_destination_twin():
    doc, items = duplicate_fixture()
    source = items[0]["path"].split("/")[0]
    rows = [*items, {**items[0], "path": source, "type": "folder", "sha256": None}]
    with pytest.raises(relocate.Refused, match="no matching copy"):
        relocate.prepare(
            doc.get_update(),
            rows,
            "argus",
            allow_unregistered=True,
            remove_duplicates=True,
        )


def test_duplicate_root_refused_when_root_docs_entry_has_no_destination_twin():
    doc, items = duplicate_fixture()
    source = items[0]["path"].split("/")[0]
    with doc.transaction():
        doc.get("docs", type=pycrdt.Map)[source] = "root-doc"
    with pytest.raises(relocate.Refused, match="no matching copy|ID mismatch"):
        relocate.prepare(doc.get_update(), items, "argus", remove_duplicates=True)


def test_index_only_row_at_destination_root_is_a_collision():
    doc, items = fixture()
    source = items[0]["path"].split("/")[0]
    rows = [*items, {**items[0], "path": f"artifacts/{source}", "type": "folder"}]
    with pytest.raises(relocate.Refused, match="collision"):
        relocate.prepare(doc.get_update(), rows, "argus", allow_unregistered=True)


def merge_fixture(dest_sha="hash"):
    """Destination holds a copy of note.md and file.bin, plus a file of its own."""
    doc, items = duplicate_fixture(sha=dest_sha)
    meta = doc.get("filemeta_v0", type=pycrdt.Map)
    with doc.transaction():
        meta["deadbeef/extra.bin"] = {"type": "file", "id": "extra-id", "version": 0}
        meta["artifacts/deadbeef/own.bin"] = {
            "type": "file",
            "id": "own-id",
            "version": 0,
        }
    asset = items[1]
    rows = [
        *items,
        {**asset, "path": "deadbeef/extra.bin", "sha256": "extra-hash"},
        {**asset, "path": "artifacts/deadbeef/own.bin", "sha256": "own-hash"},
    ]
    return doc, rows


def test_nonempty_destination_refused_by_default_with_overlap_diagnostics():
    doc, items = merge_fixture()
    with pytest.raises(relocate.Refused, match="collision.*overlap identical=") as e:
        relocate.prepare(doc.get_update(), items, "argus")
    assert "conflicting=0" in str(e.value)


def test_merge_destinations_moves_unpaired_and_drops_identical_pairs():
    doc, items = merge_fixture()
    plan = relocate.prepare(doc.get_update(), items, "argus", merge_destinations=True)
    after = plan["after_maps"]["filemeta_v0"]
    # source-only file moved in, destination-only file untouched, duplicates gone
    assert "artifacts/deadbeef/extra.bin" in after
    assert after["artifacts/deadbeef/own.bin"]["id"] == "own-id"
    assert after["artifacts/deadbeef"]["id"] == "folder-id"  # source folder id kept
    assert not [p for p in after if p.startswith("deadbeef")]
    assert after["artifacts/deadbeef/note.md"]["id"] == "copy-note"
    paths = {i["path"] for i in plan["after_index"]}
    assert "artifacts/deadbeef/extra.bin" in paths
    assert "artifacts/deadbeef/own.bin" in paths
    assert not [p for p in paths if p.startswith("deadbeef")]
    # second run on the result finds nothing left to move
    moved = relocate.load_doc(doc.get_update())
    moved.apply_update(plan["update"])
    again = relocate.prepare(
        moved.get_update(), plan["after_index"], "argus", merge_destinations=True
    )
    assert again["moves"] == []
    assert again["removed_duplicates"] == {"crdt": [], "index": []}


def test_merge_destinations_refuses_same_path_with_different_content():
    doc, items = merge_fixture(dest_sha="other-hash")
    with pytest.raises(relocate.Refused, match="conflicts: note.md.*differs in sha256"):
        relocate.prepare(doc.get_update(), items, "argus", merge_destinations=True)


def test_merge_destinations_lists_every_conflicting_path():
    doc, items = merge_fixture()
    meta = doc.get("filemeta_v0", type=pycrdt.Map)
    rows = list(items)
    with doc.transaction():
        for i in range(8):
            for prefix in ("deadbeef", "artifacts/deadbeef"):
                meta[f"{prefix}/c{i}.bin"] = {"type": "file", "id": f"{prefix}-{i}"}
    for i in range(8):
        for prefix, sha in (("deadbeef", "a"), ("artifacts/deadbeef", "b")):
            rows.append({**items[1], "path": f"{prefix}/c{i}.bin", "sha256": sha})
    with pytest.raises(relocate.Refused) as e:
        relocate.prepare(doc.get_update(), rows, "argus", merge_destinations=True)
    for i in range(8):
        assert f"c{i}.bin" in str(e.value)


def test_merge_destinations_refuses_destination_with_docs_entry():
    doc, items = merge_fixture()
    with doc.transaction():
        doc.get("docs", type=pycrdt.Map)["artifacts/deadbeef"] = "copy-id"
    with pytest.raises(relocate.Refused, match="own docs or index entry"):
        relocate.prepare(doc.get_update(), items, "argus", merge_destinations=True)


def test_merge_destinations_handles_index_only_destination_without_crdt_record():
    # Destination has no CRDT folder record, only API-uploaded index rows.
    doc, items = fixture()
    asset = items[1]
    rows = [
        *items,
        {**asset, "path": "artifacts/deadbeef/file.bin"},  # same sha as source
        {**asset, "path": "artifacts/deadbeef/own.bin", "sha256": "own-hash"},
    ]
    with pytest.raises(relocate.Refused, match="collision"):
        relocate.prepare(doc.get_update(), rows, "argus", allow_unregistered=True)
    # file.bin is registered in the CRDT at the source only: a registered/unregistered
    # pair is not provably identical, so the root is refused rather than merged.
    with pytest.raises(relocate.Refused, match="no matching copy"):
        relocate.prepare(
            doc.get_update(),
            rows,
            "argus",
            allow_unregistered=True,
            merge_destinations=True,
        )
    # Unregistered on both sides with equal sha256: identical, merged in the index.
    meta = doc.get("filemeta_v0", type=pycrdt.Map)
    with doc.transaction():
        del meta["deadbeef/file.bin"]
    plan = relocate.prepare(
        doc.get_update(),
        rows,
        "argus",
        allow_unregistered=True,
        merge_destinations=True,
    )
    paths = {i["path"] for i in plan["after_index"]}
    assert "artifacts/deadbeef/own.bin" in paths
    assert "deadbeef/file.bin" not in paths
    assert "deadbeef/file.bin" in plan["removed_duplicates"]["index"]


def folder_row(path):
    return {"path": path, "name": path.rsplit("/", 1)[-1], "type": "folder"}


def test_merge_destinations_accepts_destination_folder_index_row():
    # Production shape: the destination root has a CRDT folder record AND its
    # own bare folder row in the index; the source root has one too.
    doc, items = merge_fixture()
    rows = [*items, folder_row("artifacts/deadbeef"), folder_row("deadbeef")]
    with pytest.raises(relocate.Refused, match="collision"):
        relocate.prepare(doc.get_update(), rows, "argus")
    plan = relocate.prepare(doc.get_update(), rows, "argus", merge_destinations=True)
    paths = [i["path"] for i in plan["after_index"]]
    assert paths.count("artifacts/deadbeef") == 1
    assert "deadbeef" not in paths
    assert "artifacts/deadbeef/extra.bin" in paths
    assert "artifacts/deadbeef" in plan["removed_duplicates"]["index"]
    after = plan["after_maps"]["filemeta_v0"]
    assert after["artifacts/deadbeef"]["id"] == "folder-id"


def test_merge_destinations_keeps_destination_folder_row_without_source_row():
    doc, items = merge_fixture()
    rows = [*items, folder_row("artifacts/deadbeef")]
    plan = relocate.prepare(doc.get_update(), rows, "argus", merge_destinations=True)
    paths = [i["path"] for i in plan["after_index"]]
    assert paths.count("artifacts/deadbeef") == 1
    assert "artifacts/deadbeef" not in plan["removed_duplicates"]["index"]


def test_merge_destinations_refuses_destination_index_row_that_is_a_file():
    doc, items = merge_fixture()
    rows = [*items, {**items[1], "path": "artifacts/deadbeef", "sha256": "x"}]
    with pytest.raises(relocate.Refused, match="own docs or index entry"):
        relocate.prepare(doc.get_update(), rows, "argus", merge_destinations=True)


def test_merge_destinations_refuses_source_root_index_row_that_is_a_file():
    # A file-typed source root row must not replace the destination folder row.
    doc, items = merge_fixture()
    rows = [
        *items,
        folder_row("artifacts/deadbeef"),
        {**items[1], "path": "deadbeef", "sha256": "x"},
    ]
    with pytest.raises(relocate.Refused, match="index row is not a folder"):
        relocate.prepare(doc.get_update(), rows, "argus", merge_destinations=True)


def test_merge_destinations_folder_row_does_not_weaken_file_protection():
    doc, items = merge_fixture(dest_sha="other-hash")
    rows = [*items, folder_row("artifacts/deadbeef"), folder_row("deadbeef")]
    with pytest.raises(relocate.Refused, match="conflicts: note.md.*differs in sha256"):
        relocate.prepare(doc.get_update(), rows, "argus", merge_destinations=True)


def test_merge_destinations_folder_row_without_crdt_record_at_destination():
    # Spark shape: destination has no CRDT record, only index rows incl. a folder row.
    doc, items = fixture()
    rows = [
        *items,
        folder_row("artifacts/deadbeef"),
        {**items[1], "path": "artifacts/deadbeef/own.bin", "sha256": "own-hash"},
    ]
    plan = relocate.prepare(
        doc.get_update(),
        rows,
        "argus",
        allow_unregistered=True,
        merge_destinations=True,
    )
    paths = [i["path"] for i in plan["after_index"]]
    assert paths.count("artifacts/deadbeef") == 1
    assert "artifacts/deadbeef/own.bin" in paths
    assert "artifacts/deadbeef/file.bin" in paths
