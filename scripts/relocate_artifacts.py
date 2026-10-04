#!/usr/bin/env python3
# /// script
# requires-python = ">=3.12"
# dependencies = ["pycrdt==0.12.50", "httpx>=0.28,<0.29", "sqlalchemy>=2.0,<3", "psycopg[binary]>=3.1,<4"]
# ///
"""Move one share's artifact folders through CRDT, then its locked SQL index.

Run with uv run scripts/relocate_artifacts.py --help. No secrets in arguments.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import re
import shlex
import subprocess
import tarfile
import uuid
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from urllib.parse import urlsplit, urlunsplit

import httpx
import pycrdt
from sqlalchemy import MetaData, Table, create_engine, select
from sqlalchemy.engine import make_url

EXTRA_ROOTS = {"_probe", "mesh-artifacts"}
ROOT_PATTERN = re.compile(r"[0-9a-fA-F]{8}(?:__[^/]+)?\Z")


class Refused(RuntimeError):
    """An operator-actionable failure containing no credential values."""


def checked_path(path: str) -> str:
    if (
        not isinstance(path, str)
        or not path
        or path.startswith("/")
        or "\\" in path
        or "\x00" in path
        or any(part in {"", ".", ".."} for part in path.split("/"))
    ):
        raise Refused("Non-canonical relative path in folder state")
    return path


def selected(root: str, share_name: str, extra_roots: tuple[str, ...] = ()) -> bool:
    return bool(ROOT_PATTERN.fullmatch(root)) or (
        share_name == "mesh" and root in EXTRA_ROOTS.union(extra_roots)
    )


def maps(doc: pycrdt.Doc) -> dict:
    result = {}
    for name in ("filemeta_v0", "docs"):
        root = doc.get(name, type=pycrdt.Map)
        # Integrated nested shared types cannot be moved by assigning their value.
        for path in root:
            checked_path(path)
            value = root[path]
            if name == "filemeta_v0":
                if not isinstance(value, dict) or not isinstance(value.get("id"), str):
                    raise Refused(f"Unsupported ItemRecord at {path}")
            elif not isinstance(value, str):
                raise Refused(f"Unsupported legacy ID at {path}")
        result[name] = root.to_py()
    for path, guid in result["docs"].items():
        meta = result["filemeta_v0"].get(path)
        if meta and meta["id"] != guid:
            raise Refused(f"Legacy/metadata ID mismatch at {path}")
    ids = [meta["id"] for meta in result["filemeta_v0"].values()]
    if len(ids) != len(set(ids)) or any(not guid for guid in ids):
        raise Refused("Duplicate or empty ItemRecord ID; reconcile the folder first")
    return result


def load_doc(snapshot: bytes) -> pycrdt.Doc:
    doc = pycrdt.Doc()
    doc.apply_update(snapshot)
    return doc


def prepare(
    snapshot: bytes,
    items: list[dict],
    share_name: str,
    reverse: dict | None = None,
    extra_roots: tuple[str, ...] = (),
) -> dict:
    """Pure planning: never mutates the supplied snapshot or index."""
    doc = load_doc(snapshot)
    before = maps(doc)
    for root in extra_roots:
        checked_path(root)
        if "/" in root or not root.startswith("_"):
            raise Refused("Extra diagnostic roots must be top-level underscore names")
    artifacts = before["filemeta_v0"].get("artifacts")
    if (artifacts and artifacts.get("type") != "folder") or "artifacts" in before[
        "docs"
    ]:
        raise Refused("artifacts already exists as a file")
    paths = set(before["filemeta_v0"]) | set(before["docs"])
    indexed = set()
    for item in items:
        path = checked_path(item["path"])
        if path in indexed:
            raise Refused(f"Duplicate index path: {path}")
        indexed.add(path)
    roots = {
        p.split("/")[0]
        for p in paths | indexed
        if selected(p.split("/")[0], share_name, extra_roots)
    }
    mapping = reverse or {root: f"artifacts/{root}" for root in sorted(roots)}

    def destination(path: str) -> str:
        for source, target in mapping.items():
            if path == source or path.startswith(source + "/"):
                return target + path[len(source) :]
        return path

    for source in mapping:
        # Paired folder metadata is necessary for FolderIndex's rename aliases.
        folder = before["filemeta_v0"].get(source)
        if not folder:
            raise Refused(
                f"Folder has no CRDT ID: {source}; register it on a client first"
            )
        if folder and folder.get("type") != "folder":
            raise Refused(f"Selected root is not a folder: {source}")
    for path in indexed:
        target = destination(path)
        if target == path:
            continue
        meta = before["filemeta_v0"].get(path)
        if not meta:
            raise Refused(f"Indexed file has no CRDT metadata: {path}")
    for name, values in before.items():
        for path in values:
            target = destination(path)
            if target != path and target in values:
                raise Refused(f"CRDT destination collision: {name}/{target}")
    targets = [destination(item["path"]) for item in items]
    if len(targets) != len(set(targets)):
        raise Refused("Index destination collision")
    updated = copy.deepcopy(items)
    moves = []
    for item in updated:
        old = item["path"]
        item["path"] = destination(old)
        if item["path"] != old:
            moves.append([old, item["path"]])
    state = doc.get_state()
    with doc.transaction():
        for name, values in before.items():
            root = doc.get(name, type=pycrdt.Map)
            for path, value in values.items():
                target = destination(path)
                if target != path:
                    root[target] = value
                    del root[path]
        if mapping and not reverse and "artifacts" not in before["filemeta_v0"]:
            doc.get("filemeta_v0", type=pycrdt.Map)["artifacts"] = {
                "id": str(uuid.uuid4()),
                "version": 0,
                "type": "folder",
            }
    return {
        "before_maps": before,
        "after_maps": maps(doc),
        "before_index": items,
        "after_index": updated,
        "mapping": mapping,
        "moves": moves,
        "update": doc.get_update(state),
    }


def write_private(path: Path, data: bytes) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())


def dump_json(path: Path, data: dict) -> None:
    write_private(path, json.dumps(data, ensure_ascii=False, sort_keys=True).encode())


def backup(
    directory: Path, database_url: str, relay_data: Path, snapshot: bytes, plan: dict
) -> None:
    if not relay_data.is_dir():
        raise Refused("--relay-data-dir must be a readable copy/mount of relay storage")
    entries = list(relay_data.rglob("*"))
    if any(p.is_symlink() for p in [relay_data, *entries]) or not any(
        p.is_file() for p in entries
    ):
        raise Refused("Relay storage must contain real files, without symlinks")
    directory.mkdir(mode=0o700, parents=True, exist_ok=False)
    if directory.resolve().is_relative_to(relay_data.resolve()):
        raise Refused("Backup directory cannot be inside relay storage")
    parsed = make_url(database_url)
    if parsed.get_backend_name() != "postgresql":
        raise Refused("Apply requires PostgreSQL; SQLite has no FOR UPDATE row lock")
    environment = dict(os.environ)
    for key, value in {
        "PGHOST": parsed.host,
        "PGPORT": parsed.port or 5432,
        "PGUSER": parsed.username,
        "PGPASSWORD": parsed.password,
        "PGDATABASE": parsed.database,
    }.items():
        if value is not None:
            environment[key] = str(value)
    for option in (
        "sslmode",
        "sslcert",
        "sslkey",
        "sslrootcert",
        "sslcrl",
        "connect_timeout",
    ):
        if option in parsed.query:
            environment["PG" + option.upper()] = parsed.query[option]
    dump = directory / "database.dump"
    # pg_dump inherits credentials privately through the child environment.
    # stderr is not forwarded: connection errors can include credential material.
    with dump.open("xb") as stream:
        os.chmod(dump, 0o600)
        result = subprocess.run(
            ["pg_dump", "--format=custom", "--no-password"],
            env=environment,
            stdout=stream,
            stderr=subprocess.DEVNULL,
            check=False,
            timeout=600,
        )
    if result.returncode or dump.stat().st_size == 0:
        raise Refused("pg_dump failed; no changes made")
    result = subprocess.run(
        ["pg_restore", "--list", str(dump)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
        timeout=60,
    )
    if result.returncode:
        raise Refused("pg_restore could not validate backup; no changes made")
    archive = directory / "relay-data.tar"
    with archive.open("xb") as stream:
        os.chmod(archive, 0o600)
        with tarfile.open(fileobj=stream, mode="w") as tar:
            tar.add(relay_data, arcname="relay-data")
    with tarfile.open(archive) as tar:
        if not tar.getmembers():
            raise Refused("Relay backup is empty")
    write_private(directory / "folder-before.yjs", snapshot)
    write_private(directory / "move.yjs", plan["update"])
    dump_json(
        directory / "journal.json", {k: v for k, v in plan.items() if k != "update"}
    )
    write_manifest(directory)


def write_manifest(directory: Path) -> None:
    checksums = {}
    for path in directory.iterdir():
        if path.is_file():
            with path.open("rb") as stream:
                checksums[path.name] = hashlib.file_digest(stream, "sha256").hexdigest()
    dump_json(directory / "checksums.json", checksums)


def journal_rollback(
    directory: Path, snapshot: bytes, plan: dict, share_id: str
) -> None:
    """Keep an inverse operation resumable without duplicating large backups."""
    inverse = directory / "rollback"
    inverse.mkdir(mode=0o700, exist_ok=False)
    for name in ("database.dump", "relay-data.tar"):
        os.link(directory / name, inverse / name)
    write_private(inverse / "folder-before.yjs", snapshot)
    write_private(inverse / "move.yjs", plan["update"])
    dump_json(
        inverse / "journal.json",
        {
            **{k: v for k, v in plan.items() if k != "update"},
            "share_id": share_id,
        },
    )
    write_manifest(inverse)


class Relay:
    def __init__(self, client: httpx.Client, cp_url: str, jwt: str, share_id: str):
        self.client = client
        headers = {"Authorization": f"Bearer {jwt}"}
        me = client.get(f"{cp_url}/v1/auth/me", headers=headers)
        me.raise_for_status()
        response = client.get(f"{cp_url}/v1/shares/{share_id}", headers=headers)
        response.raise_for_status()
        self.share = response.json()
        self.owner = me.json()["id"]
        if self.share["owner_user_id"] != self.owner or self.share["kind"] != "folder":
            raise Refused("A folder share's own user token is required")
        response = client.post(
            f"{cp_url}/v1/tokens/relay",
            headers=headers,
            json={"share_id": share_id, "doc_id": share_id, "mode": "write"},
        )
        response.raise_for_status()
        token = response.json()
        parsed = urlsplit(token["relay_url"])
        if (
            parsed.query
            or parsed.fragment
            or not parsed.path.endswith(f"/d/{share_id}/ws")
        ):
            raise Refused("Unexpected relay token URL shape")
        scheme = {"wss": "https", "ws": "http"}.get(parsed.scheme)
        if scheme is None:
            raise Refused("Unexpected relay token URL scheme")
        self.base = urlunsplit(
            (scheme, parsed.netloc, parsed.path.removesuffix("/ws"), "", "")
        )
        self.headers = {"Authorization": f"Bearer {token['token']}"}

    def snapshot(self) -> bytes:
        response = self.client.get(f"{self.base}/as-update", headers=self.headers)
        response.raise_for_status()
        return response.content

    def post(self, update: bytes) -> None:
        response = self.client.post(
            f"{self.base}/update", headers=self.headers, content=update
        )
        response.raise_for_status()


def verify_journal(directory: Path, share_id: str) -> dict:
    checksums = json.loads((directory / "checksums.json").read_bytes())
    for name, expected in checksums.items():
        if Path(name).name != name:
            raise Refused("Invalid backup manifest path")
        with (directory / name).open("rb") as stream:
            if hashlib.file_digest(stream, "sha256").hexdigest() != expected:
                raise Refused(f"Backup checksum mismatch: {name}")
    required = {
        "journal.json",
        "move.yjs",
        "folder-before.yjs",
        "database.dump",
        "relay-data.tar",
    }
    if not required.issubset(checksums):
        raise Refused("Incomplete backup manifest")
    journal = json.loads((directory / "journal.json").read_bytes())
    if journal["share_id"] != share_id:
        raise Refused("Backup belongs to another share")
    journal["update"] = (directory / "move.yjs").read_bytes()
    return journal


def finish(
    connection, shares: Table, audits: Table, key, relay: Relay, plan: dict
) -> None:
    row = connection.execute(
        select(shares).where(shares.c.id == key).with_for_update()
    ).one()
    if str(row.owner_user_id) != relay.owner:
        raise Refused("Database/API owner mismatch")
    if row.web_folder_items not in (plan["before_index"], plan["after_index"]):
        raise Refused("Index changed since planning; stop and reconcile")
    current = maps(load_doc(relay.snapshot()))
    if current not in (plan["before_maps"], plan["after_maps"]):
        raise Refused("CRDT maps changed since planning; stop and reconcile")
    if current != plan["after_maps"]:
        relay.post(plan["update"])
    if maps(load_doc(relay.snapshot())) != plan["after_maps"]:
        raise Refused("CRDT verification failed; preserve journal and use --resume")
    if row.web_folder_items != plan["after_index"]:
        now = datetime.now(timezone.utc)
        connection.execute(
            shares.update()
            .where(shares.c.id == key)
            .values(
                web_folder_items=plan["after_index"],
                web_content_updated_at=now,
                updated_at=now,
            )
        )
        connection.execute(
            audits.insert().values(
                id=uuid.uuid4(),
                action="share_updated",
                actor_user_id=uuid.UUID(relay.owner),
                target_share_id=key,
                details={
                    "operation": "artifact_folder_relocation",
                    "mapping": plan["mapping"],
                },
            )
        )


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--share-id", required=True, type=uuid.UUID)
    result.add_argument("--cp-url", default="http://localhost:8000")
    result.add_argument("--token-env", default="TR_OWNER_TOKEN")
    result.add_argument("--database-url-env", default="DATABASE_URL")
    result.add_argument(
        "--extra-root",
        action="append",
        default=[],
        help="Additional legacy diagnostic root on the mesh share only",
    )
    result.add_argument(
        "--production-share", help="Explicit exact share path for production apply"
    )
    mode = result.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--apply", action="store_true")
    mode.add_argument("--resume", type=Path)
    mode.add_argument("--rollback", type=Path)
    result.add_argument("--backup-dir", type=Path)
    result.add_argument("--relay-data-dir", type=Path)
    return result


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    os.umask(0o077)
    try:
        token = os.environ.get(args.token_env)
        database_url = os.environ.get(args.database_url_env)
        if not token or not database_url:
            raise Refused(
                "Owner token and database URL environment variables are required"
            )
        engine = create_engine(database_url, hide_parameters=True)
        if (
            args.apply or args.resume or args.rollback
        ) and engine.dialect.name != "postgresql":
            raise Refused("Writes require PostgreSQL row locks")
        metadata = MetaData()
        shares = Table("shares", metadata, autoload_with=engine)
        audits = Table("audit_logs", metadata, autoload_with=engine)
        with httpx.Client(timeout=60, follow_redirects=False) as client:
            relay = Relay(client, args.cp_url.rstrip("/"), token, str(args.share_id))
            share_path = relay.share["path"]
            write = args.apply or args.resume or args.rollback
            if (
                write
                and not share_path.startswith("_relocation-test-")
                and args.production_share != share_path
            ):
                raise Refused(
                    "Default apply only accepts _relocation-test-* shares; specify exact --production-share"
                )
            share_name = PurePosixPath(share_path).name.casefold()
            with engine.connect() as connection:
                key = args.share_id
                row = connection.execute(select(shares).where(shares.c.id == key)).one()
                if (
                    str(row.owner_user_id) != relay.owner
                    or str(row.kind) != "folder"
                    or row.path != share_path
                ):
                    raise Refused("Database/API share mismatch")
                snapshot = relay.snapshot()
                plan = (
                    None
                    if args.resume or args.rollback
                    else prepare(
                        snapshot,
                        row.web_folder_items or [],
                        share_name,
                        extra_roots=tuple(args.extra_root),
                    )
                )
            if args.resume or args.rollback:
                directory = args.resume or args.rollback
                journal = verify_journal(directory, str(args.share_id))
                if args.rollback:
                    inverse = directory / "rollback"
                    if inverse.exists():
                        plan = verify_journal(inverse, str(args.share_id))
                    elif (
                        maps(load_doc(snapshot)) != journal["after_maps"]
                        or row.web_folder_items != journal["after_index"]
                    ):
                        raise Refused(
                            "Share changed after relocation; automatic rollback refused"
                        )
                    else:
                        plan = prepare(
                            snapshot,
                            row.web_folder_items,
                            share_name,
                            {v: k for k, v in journal["mapping"].items()},
                        )
                        # Retain the new empty artifacts directory and inverse journal.
                        journal_rollback(directory, snapshot, plan, str(args.share_id))
                    print(f"rollback_resume_journal={inverse}")
                else:
                    plan = journal
            for old, new in plan["moves"]:
                print(f"{share_name}\t{old}\t{new}")
            print(
                f"files_before={sum(i.get('type') != 'folder' for i in plan['before_index'])} "
                f"files_after={sum(i.get('type') != 'folder' for i in plan['after_index'])} "
                f"moved_files={len(plan['moves'])} mode={'apply' if write else 'dry-run'}"
            )
            changed = (
                plan["before_maps"] != plan["after_maps"]
                or plan["before_index"] != plan["after_index"]
            )
            if not write or not changed:
                return 0
            if args.apply:
                if not args.backup_dir or not args.relay_data_dir:
                    raise Refused("Apply requires --backup-dir and --relay-data-dir")
                plan["share_id"] = str(args.share_id)
                backup(
                    args.backup_dir, database_url, args.relay_data_dir, snapshot, plan
                )
                print(f"backup_verified={args.backup_dir}")
                print(
                    f"rollback: uv run scripts/relocate_artifacts.py --share-id {args.share_id} "
                    f"--cp-url {shlex.quote(args.cp_url)} --production-share {shlex.quote(share_path)} "
                    f"--token-env {shlex.quote(args.token_env)} --database-url-env {shlex.quote(args.database_url_env)} "
                    f"--rollback {shlex.quote(str(args.backup_dir))}"
                )
            with engine.begin() as connection:
                finish(connection, shares, audits, args.share_id, relay, plan)
            with engine.connect() as connection:
                actual = connection.execute(
                    select(shares.c.web_folder_items).where(
                        shares.c.id == args.share_id
                    )
                ).scalar_one()
                if (
                    actual != plan["after_index"]
                    or maps(load_doc(relay.snapshot())) != plan["after_maps"]
                ):
                    raise Refused(
                        "Post-commit verification failed; retain backup and reconcile"
                    )
            print(
                "VERIFIED: CRDT maps and complete index match; IDs, file count and storage metadata preserved"
            )
            return 0
    except Refused as exc:
        print(f"REFUSED: {exc}")
    except httpx.HTTPStatusError as exc:
        print(
            f"REFUSED: HTTP {exc.response.status_code}; retain any journal, no automatic retry"
        )
    except Exception as exc:  # noqa: BLE001 -- avoid printing credential-bearing driver errors
        print(
            f"REFUSED: {type(exc).__name__}; retain any journal, inspect locally without printing credentials"
        )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
