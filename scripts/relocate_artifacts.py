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
import shutil
import subprocess
import tarfile
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path, PurePosixPath
from urllib.parse import urlsplit, urlunsplit

import httpx
import pycrdt
from sqlalchemy import MetaData, Table, create_engine, select
from sqlalchemy.engine import make_url

EXTRA_ROOTS = {"_probe", "mesh-artifacts"}
ROOT_PATTERN = re.compile(r"[0-9a-fA-F]{8}(?:__[^/]+)?\Z")
VERIFIED_MARKER = "verified.json"


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


def children(paths, root: str) -> list[str]:
    return sorted(p for p in paths if p.startswith(root + "/"))


def empty_placeholder(before: dict, indexed: set, target: str) -> bool:
    """A bare folder record with no children and no index rows of its own."""
    record = before["filemeta_v0"].get(target)
    return bool(
        record
        and record.get("type") == "folder"
        and target not in before["docs"]
        and target not in indexed
        and not children(before["filemeta_v0"], target)
        and not children(before["docs"], target)
        and not children(indexed, target)
    )


def describe_destination(before: dict, items: list[dict], source: str, target: str):
    record = before["filemeta_v0"].get(target)
    src = before["filemeta_v0"].get(source)
    indexed = {i["path"] for i in items}
    return (
        f"destination type={record.get('type') if record else None} "
        f"id={record.get('id') if record else None} "
        f"in_docs={target in before['docs']} "
        f"crdt_children={len(children(before['filemeta_v0'], target))} "
        f"index_children={len(children(indexed, target))}; "
        f"source type={src.get('type') if src else None} "
        f"id={src.get('id') if src else None} "
        f"in_docs={source in before['docs']} "
        f"crdt_children={len(children(before['filemeta_v0'], source))} "
        f"index_children={len(children(indexed, source))}"
    )


def duplicate_problem(before: dict, items: list[dict], source: str, target: str):
    """Return None when every source entry has an identical copy at the target.

    One uniform rule over the CRDT metadata, the docs map and the index: a
    source path is removable only if the same set of maps holds it and its
    copy, both have a CRDT record and (for files) an index row, the types match
    in every map, and the sha256 values are present and equal.
    """
    meta = before["filemeta_v0"]
    if (meta.get(target) or {}).get("type") != "folder":
        return "destination is not a folder record"
    by_path = {i["path"]: i for i in items}
    if (meta.get(source) or {}).get("type") != "folder":
        return f"source root {source} is not a folder record"
    root_row = by_path.get(source)
    if root_row and root_row.get("type") != "folder":
        return f"source root {source} is a folder in the CRDT but a file in the index"
    maps_ = {"filemeta_v0": meta, "docs": before["docs"], "index": by_path}
    # "" is the root itself: it gets the same parity proof as its descendants.
    rels = {""} | {
        path[len(source) :]
        for values in maps_.values()
        for path in children(values, source)
    }
    for rel in sorted(rels):
        reason = pair_problem(before, by_path, source + rel, target + rel)
        if reason:
            return reason
    return None


def pair_problem(
    before: dict, by_path: dict, path: str, twin: str, allow_unregistered=False
):
    """Return None when ``path`` and ``twin`` are provably identical entries."""
    meta = before["filemeta_v0"]
    maps_ = {"filemeta_v0": meta, "docs": before["docs"], "index": by_path}
    for name, values in maps_.items():
        if (path in values) != (twin in values):
            return f"{name}/{path} has no matching copy at the destination"
    row, twin_row = by_path.get(path), by_path.get(twin)
    if (
        allow_unregistered
        and path not in meta
        and path not in before["docs"]
        and row
        and twin_row
    ):
        # Index-only files (uploaded through the API): compare the index rows.
        if "folder" in (row.get("type"), twin_row.get("type")):
            return f"{path} is an unregistered folder entry"
        if row.get("type") != twin_row.get("type"):
            return f"{path} differs in index type from its copy"
        if not row.get("sha256") or row.get("sha256") != twin_row.get("sha256"):
            return f"{path} differs in sha256 from its copy"
        return None
    if path not in meta or twin not in meta:
        return f"{path} has no CRDT record for it or its copy"
    kind = meta[path].get("type")
    if kind != meta[twin].get("type"):
        return f"{path} differs in type from its copy"
    if kind == "folder":
        if row and (row.get("type") != "folder" or twin_row.get("type") != "folder"):
            return f"{path} is a folder in the CRDT but a file in the index"
        return None
    if not row:
        return f"{path} has no index row to verify its sha256"
    if row.get("type") == "folder" or twin_row.get("type") == "folder":
        return f"{path} is a file in the CRDT but a folder in the index"
    if row.get("type") != twin_row.get("type"):
        return f"{path} differs in index type from its copy"
    if not row.get("sha256") or row.get("sha256") != twin_row.get("sha256"):
        return f"{path} differs in sha256 from its copy"
    return None


def merge_plan(
    before: dict, items: list[dict], source: str, target: str, allow_unregistered: bool
) -> dict:
    """Classify every source entry against ``target``: move, duplicate or conflict.

    Entries with no counterpart at the target move as usual. Entries whose
    counterpart is provably identical (see ``pair_problem``) are removed from
    the source side. Anything else is a conflict and nothing is overwritten.
    """
    meta = before["filemeta_v0"]
    by_path = {i["path"]: i for i in items}
    plan = {"crdt": [], "index": [], "conflicts": [], "problem": None}
    record = meta.get(target)
    if target in by_path and source in by_path:
        # A bare folder row at the destination yields to the source root row,
        # which moves onto the same path.
        if by_path[source].get("type") == "folder":
            plan["index"].append(target)
        else:
            plan["problem"] = f"source root {source} index row is not a folder"
    if record and record.get("type") != "folder":
        plan["problem"] = "destination is not a folder record"
    elif target in before["docs"] or (
        target in by_path and by_path[target].get("type") != "folder"
    ):
        plan["problem"] = "destination has its own docs or index entry"
    elif (meta.get(source) or {}).get("type") != "folder":
        plan["problem"] = f"source root {source} is not a folder record"
    maps_ = {"filemeta_v0": meta, "docs": before["docs"], "index": by_path}
    for rel in sorted(
        {p[len(source) :] for v in maps_.values() for p in children(v, source)}
    ):
        path, twin = source + rel, target + rel
        if not any(twin in values for values in maps_.values()):
            continue
        reason = pair_problem(before, by_path, path, twin, allow_unregistered)
        if reason:
            plan["conflicts"].append(f"{rel[1:]}: {reason}")
            continue
        plan["crdt"] += [path for v in (meta, before["docs"]) if path in v]
        if path in by_path:
            plan["index"].append(path)
    return plan


def prepare(
    snapshot: bytes,
    items: list[dict],
    share_name: str,
    reverse: dict | None = None,
    extra_roots: tuple[str, ...] = (),
    allow_unregistered: bool = False,
    merge_empty_destinations: bool = False,
    remove_duplicates: bool = False,
    merge_destinations: bool = False,
) -> dict:
    """Pure planning: never mutates the supplied snapshot or index.

    ``merge_empty_destinations`` lets a root move onto an existing empty folder
    record at ``artifacts/<root>`` (no children in the CRDT or the index): the
    empty record is dropped and the source folder keeps its ID.
    ``merge_destinations`` merges a root into a NON-empty ``artifacts/<root>``:
    unpaired source files move in, identical pairs (same type and sha256) are
    removed from the source side, and any same-path pair that differs refuses
    the whole root; nothing is ever overwritten. A destination root that has a
    bare folder row of its own in the index (no docs entry) is accepted: the row
    is dropped when the source root has one, otherwise it is kept.
    ``remove_duplicates`` removes a legacy root whose ``artifacts/<root>`` copy
    already holds every file with an identical sha256, in the CRDT and the index.

    With ``allow_unregistered`` an indexed file that has no CRDT metadata is
    moved in the SQL index only. Such files were uploaded through the API and no
    client ever registered them, so there is no CRDT entry to move.
    """
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
    merged: list[str] = []
    duplicates: dict[str, list[str]] = {"crdt": [], "index": []}
    if not reverse:
        problems = []
        for source in sorted(mapping):
            target = mapping[source]
            exists = (
                target in before["filemeta_v0"]
                or target in before["docs"]
                or target in indexed
                or any(path.startswith(target + "/") for path in paths | indexed)
            )
            if not exists:
                continue
            reason = None
            if merge_empty_destinations and empty_placeholder(before, indexed, target):
                merged.append(target)
                continue
            overlap = merge_plan(before, items, source, target, allow_unregistered)
            if merge_destinations:
                if overlap["problem"]:
                    reason = overlap["problem"]
                elif overlap["conflicts"]:
                    reason = "conflicts: " + "; ".join(sorted(overlap["conflicts"]))
                else:
                    merged.append(target)
                    duplicates["crdt"] += overlap["crdt"]
                    duplicates["index"] += overlap["index"]
                    continue
            if remove_duplicates and not merge_destinations:
                reason = duplicate_problem(before, items, source, target)
                if reason is None:
                    del mapping[source]
                    duplicates["crdt"] += [
                        p
                        for values in before.values()
                        for p in values
                        if p == source or p.startswith(source + "/")
                    ]
                    duplicates["index"] += [
                        i["path"]
                        for i in items
                        if i["path"] == source or i["path"].startswith(source + "/")
                    ]
                    continue
            detail = describe_destination(before, items, source, target)
            detail += (
                f"; overlap identical={len(set(overlap['index']) | set(overlap['crdt']))} "
                f"conflicting={len(overlap['conflicts'])}"
            )
            problems.append(
                f"{target} (source {source}): {detail}"
                + (f"; not a verified duplicate: {reason}" if reason else "")
            )
        if problems:
            raise Refused("CRDT destination collision: " + " | ".join(problems[:20]))
        duplicates["crdt"] = sorted(set(duplicates["crdt"]))
    dropped = set(duplicates["index"])

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
        if not meta and not allow_unregistered:
            raise Refused(f"Indexed file has no CRDT metadata: {path}")
        if (
            not meta
            and next(i for i in items if i["path"] == path).get("type") == "folder"
        ):
            raise Refused(f"Unregistered folder entry cannot be moved: {path}")
        if not meta and path in before["docs"]:
            raise Refused(f"Unregistered file is in legacy docs map: {path}")
        if not meta and any(target in values for values in before.values()):
            raise Refused(f"CRDT destination collision: {target} (source {path})")
    for name, values in before.items():
        for path in values:
            if path in duplicates["crdt"]:
                continue
            target = destination(path)
            if target != path and target in values and target not in merged:
                raise Refused(
                    f"CRDT destination collision: {name}/{target} (source {path})"
                )
    targets = [destination(i["path"]) for i in items if i["path"] not in dropped]
    if len(targets) != len(set(targets)):
        raise Refused("Index destination collision")
    updated = copy.deepcopy([i for i in items if i["path"] not in dropped])
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
            for path in duplicates["crdt"]:
                if path in values:
                    del root[path]
            for target in merged:
                if target in values:
                    del root[target]
            for path, value in values.items():
                if path in duplicates["crdt"]:
                    continue
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
        "merged_empty_destinations": merged,
        "removed_duplicates": duplicates,
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
    def __init__(
        self,
        client: httpx.Client,
        cp_url: str,
        jwt: str,
        share_id: str,
        operator_admin: bool = False,
    ):
        self.client = client
        headers = {"Authorization": f"Bearer {jwt}"}
        me = client.get(f"{cp_url}/v1/auth/me", headers=headers)
        me.raise_for_status()
        response = client.get(f"{cp_url}/v1/shares/{share_id}", headers=headers)
        response.raise_for_status()
        self.share = response.json()
        profile = me.json()
        # `owner` is the share owner reported by the API (compared with the
        # database row later); `actor` is the user the token belongs to.
        self.owner = self.share["owner_user_id"]
        self.actor = profile["id"]
        if self.share["kind"] != "folder":
            raise Refused("A folder share's own user token is required")
        if not operator_admin:
            if self.owner != self.actor:
                raise Refused("A folder share's own user token is required")
        elif self.owner != self.actor:
            if profile.get("is_admin") is not True:
                raise Refused("--operator-admin requires an administrator account")
            members = client.get(
                f"{cp_url}/v1/shares/{share_id}/members", headers=headers
            )
            members.raise_for_status()
            if not any(
                member.get("user_id") == self.actor and member.get("role") == "editor"
                for member in members.json()
            ):
                raise Refused(
                    "--operator-admin requires editor membership on the share"
                )
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


def backup_anchor(directory: Path) -> Path:
    """The relocation backup that owns a directory (an inverse journal lives inside it)."""
    if directory.name == "rollback" and (directory.parent / "checksums.json").is_file():
        return directory.parent
    return directory


def mark_verified(directory: Path, now: datetime) -> None:
    # Retention may only ever remove a backup whose run reached VERIFIED.
    marker = backup_anchor(directory) / VERIFIED_MARKER
    marker.unlink(missing_ok=True)
    dump_json(marker, {"verified_at": now.isoformat()})


def expired_backups(root: Path, keep_days: int, now: datetime) -> list[Path]:
    if not root.is_dir():
        return []
    result = []
    for directory in sorted(root.iterdir()):
        marker = directory / VERIFIED_MARKER
        if (
            directory.is_symlink()
            or not directory.is_dir()
            or marker.is_symlink()
            or not marker.is_file()
            or not (directory / "checksums.json").is_file()
        ):
            continue
        try:
            verified_at = datetime.fromisoformat(
                json.loads(marker.read_bytes())["verified_at"]
            )
            expired = now - verified_at >= timedelta(days=keep_days)
        except (ValueError, KeyError, TypeError):
            continue
        if expired:
            result.append(directory)
    return result


def prune_backups(root: Path, keep_days: int, now: datetime, dry_run: bool) -> None:
    for directory in expired_backups(root, keep_days, now):
        if dry_run:
            print(f"would_remove_backup={directory}")
        else:
            shutil.rmtree(directory)
            print(f"removed_backup={directory}")


def retention(args: argparse.Namespace, dry_run: bool) -> None:
    directory = args.backup_dir or args.resume or args.rollback
    if directory is not None:
        root = backup_anchor(directory).parent
        prune_backups(root, args.keep_local_days, datetime.now(UTC), dry_run)


def non_negative(value: str) -> int:
    result = int(value)
    if result < 0:
        raise argparse.ArgumentTypeError("must be 0 or more")
    return result


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
        now = datetime.now(UTC)
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
                actor_user_id=uuid.UUID(relay.actor),
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
    result.add_argument(
        "--operator-admin",
        action="store_true",
        help="Use an administrator's token (editor member of the share) instead of the share owner's",
    )
    result.add_argument(
        "--allow-unregistered-files",
        action="store_true",
        help="Move indexed files that have no CRDT metadata in the index only "
        "(default: refuse)",
    )
    result.add_argument(
        "--merge-empty-destination-folders",
        action="store_true",
        help="Let a root move onto an existing EMPTY artifacts/<root> folder record "
        "(default: refuse)",
    )
    result.add_argument(
        "--remove-duplicate-roots",
        action="store_true",
        help="Remove a legacy root whose artifacts/<root> copy holds every file with "
        "an identical sha256 (default: refuse)",
    )
    result.add_argument(
        "--merge-destination-folders",
        action="store_true",
        help="Merge a root into an existing NON-empty artifacts/<root>: move unpaired "
        "files, drop identical (sha256) duplicates, refuse on any differing "
        "same-path file (default: refuse)",
    )
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
    result.add_argument(
        "--keep-local-days",
        type=non_negative,
        default=3,
        help="Remove sibling backups of --backup-dir/--resume/--rollback whose run "
        "reached VERIFIED at least N days ago (default 3; unverified backups are "
        "never removed; --dry-run only lists them)",
    )
    return result


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    os.umask(0o077)
    try:
        token = os.environ.get(args.token_env)
        database_url = os.environ.get(args.database_url_env)
        if not token or not database_url:
            raise Refused("Token and database URL environment variables are required")
        engine = create_engine(database_url, hide_parameters=True)
        if (
            args.apply or args.resume or args.rollback
        ) and engine.dialect.name != "postgresql":
            raise Refused("Writes require PostgreSQL row locks")
        metadata = MetaData()
        shares = Table("shares", metadata, autoload_with=engine)
        audits = Table("audit_logs", metadata, autoload_with=engine)
        with httpx.Client(timeout=60, follow_redirects=False) as client:
            relay = Relay(
                client,
                args.cp_url.rstrip("/"),
                token,
                str(args.share_id),
                operator_admin=args.operator_admin,
            )
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
                        allow_unregistered=args.allow_unregistered_files,
                        merge_empty_destinations=args.merge_empty_destination_folders,
                        remove_duplicates=args.remove_duplicate_roots,
                        merge_destinations=args.merge_destination_folders,
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
                    elif journal.get("merged_empty_destinations") or any(
                        journal.get("removed_duplicates", {}).values()
                    ):
                        raise Refused(
                            "This relocation merged or removed entries; automatic "
                            "rollback cannot restore them. Restore the backup "
                            "(database.dump, folder-before.yjs) instead"
                        )
                    else:
                        plan = prepare(
                            snapshot,
                            row.web_folder_items,
                            share_name,
                            {v: k for k, v in journal["mapping"].items()},
                            # The recorded after-state was just verified; files
                            # moved in the index only have no CRDT entry to undo.
                            allow_unregistered=True,
                        )
                        # Retain the new empty artifacts directory and inverse journal.
                        journal_rollback(directory, snapshot, plan, str(args.share_id))
                    print(f"rollback_resume_journal={inverse}")
                else:
                    plan = journal
            for old, new in plan["moves"]:
                print(f"{share_name}\t{old}\t{new}")
            for target in plan.get("merged_empty_destinations", []):
                print(f"{share_name}\tmerge-empty\t{target}")
            for path in plan.get("removed_duplicates", {}).get("crdt", []):
                print(f"{share_name}\tremove-duplicate-crdt\t{path}")
            for path in plan.get("removed_duplicates", {}).get("index", []):
                print(f"{share_name}\tremove-duplicate-index\t{path}")
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
                retention(args, dry_run=not write)
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
                    f"{'--operator-admin ' if args.operator_admin else ''}"
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
            verified = args.backup_dir if args.apply else args.resume or args.rollback
            mark_verified(verified, datetime.now(UTC))
            retention(args, dry_run=False)
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
