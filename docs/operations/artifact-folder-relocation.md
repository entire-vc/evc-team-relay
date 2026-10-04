# Artifact folder relocation

This maintenance tool moves top-level eight-hex task folders (including `__`
suffixes) into `artifacts/`. On the `mesh` share it also moves `_probe` and
`mesh-artifacts`. Supply additional legacy diagnostic names from the private
relocation inventory with repeated `--extra-root` arguments (top-level names
starting with `_` only, effective on the `mesh` share only).
It never selects other roots. Run one share at a time, on a disposable test
share first. Default execution is a dry run.

## Existing contracts

No HTTP endpoint, plugin or MCP contract changes. The operator authenticates
as the share owner through `/v1/auth/me` and `/v1/shares/{id}` and obtains a
write CWT from `/v1/tokens/relay` for `doc_id == share_id`. The relay's existing
`/d/{id}/as-update` and `/d/{id}/update` exchange Yjs v1 updates.

`filemeta_v0` maps relative paths to plain ItemRecord objects; `docs` is the
legacy path-to-document-ID map. Both maps are moved in one CRDT transaction.
Folder IDs, document IDs, metadata, attachment hashes and document bodies stay
the same. Paired folder delete/add lets FolderIndex create aliases and rename
the local folder after an offline client reconnects. Documents retain their
separate CRDT IDs, so offline content edits still merge into the same document.

The database `shares.web_folder_items` paths also change, under a row lock,
preserving every field and object-storage key. A normal share PATCH cannot do
this: its schema drops storage metadata and its merge preserves old artifacts.
The tool runs on the control-plane host with the existing database connection;
it checks the database owner against the authenticated owner and records a
`share_updated` audit entry. It does not create keys or change integrations.

Before any write, every selected indexed file must have metadata at its
source path. Missing metadata is a hard failure: downloading a
file does not prove that an Obsidian client has registered its GUID. Collisions,
invalid paths, mismatching legacy IDs, and unexpected changes fail before writes.
The migration is a maintenance operation: pause artifact producers and clients
that change folder metadata until both representations have been verified.
Offline content edits are supported; concurrent offline structural edits are
not an atomic rename guarantee of the CRDT and require reconciliation first.

## Backup, recovery and proof

`--apply` requires PostgreSQL, `pg_dump`/`pg_restore`, a readable relay data
directory, and a fresh backup directory. It saves a custom-format database dump,
a tar copy of relay storage, the folder CRDT snapshot and exact index, verifies
the dump/archive, then rechecks the folder maps before posting the CRDT update.
An on-disk journal contains the update and both index versions before posting.
No credentials are written to the journal or output. Backup directories are 700,
files 600. Keep the original object store: relocation changes paths, not blobs.

An interrupted POST has an unknown result. `--resume DIR` replays the saved
update (same CRDT IDs/clocks, hence idempotent), and finishes the index update
only when the locked index matches the recorded before or after state. It also
checks current CRDT maps against before/after values to avoid clobbering changes.

`--rollback DIR` performs an inverse CRDT move with the original IDs and restores
the index only if both still match the recorded after state. Applying the old
snapshot is **not** rollback: CRDT snapshots merge and cannot undo tombstones.
After later structural edits, stop and reconcile instead of restoring blindly.
A full database/storage restore requires stopping services and is disaster
recovery, not the normal rollback path.

Verification compares the entire path/metadata maps, the full index (including
hashes/storage keys), file counts and absence of selected root folders. An
already-moved share emits `moved_files=0` and sends no writes or audit event.
The offline regression must include a red control: deleting an index entry
alone leaves the disconnected client's old CRDT path alive and it republishes
the old folder. A synthetic CRDT peer proves protocol behavior; acceptance on
an actual Obsidian client and the production `argus` share remains separate.

## Operator commands

Run on the control-plane host, with a PostgreSQL client matching the server major
version and an existing mounted/copied relay storage directory. For S3-backed
relay deployments use a consistent local copy of that bucket, not an unrelated
empty Docker directory. Pause artifact writers and keep clients disconnected
until the CRDT **and** index verification completes. Retain object storage keys.
Set `DATABASE_URL` to this instance's database connection and `TR_OWNER_TOKEN`
to the share owner's user JWT through a private environment carrier. Do not put
credentials in command arguments, logs or the journal. The API, database and
relay storage must all be from the same instance.

```sh
# No changes by default. Use the disposable share's UUID first.
uv run scripts/relocate_artifacts.py --share-id "$TEST_SHARE_ID" --cp-url "$CP_URL"
uv run scripts/relocate_artifacts.py --share-id "$TEST_SHARE_ID" --cp-url "$CP_URL" \
  --apply --backup-dir /secure/backups/test-relocation \
  --relay-data-dir /secure/relay-storage

# Unknown POST/SQL outcome: use the existing verified journal, not a new plan.
uv run scripts/relocate_artifacts.py --share-id "$TEST_SHARE_ID" --cp-url "$CP_URL" \
  --resume /secure/backups/test-relocation

# Undo the rename through CRDT; an old snapshot alone cannot undo it.
uv run scripts/relocate_artifacts.py --share-id "$TEST_SHARE_ID" --cp-url "$CP_URL" \
  --rollback /secure/backups/test-relocation

# A lost response during rollback: repeat --rollback, or resume its inverse journal.
uv run scripts/relocate_artifacts.py --share-id "$TEST_SHARE_ID" --cp-url "$CP_URL" \
  --resume /secure/backups/test-relocation/rollback

# After offline-client proof on the test share, only one production share.
uv run scripts/relocate_artifacts.py --share-id "$ARGUS_SHARE_ID" --cp-url "$CP_URL" \
  --production-share argus --apply --backup-dir /secure/backups/argus-relocation \
  --relay-data-dir /secure/relay-storage
```

The production-share argument is the exact API share path (use that path if it
differs from `argus`). Reconnect the offline client only after `VERIFIED`. Check
the actual vault: old folders absent, new folders present, no duplicate files.
Export `/v1/shares/{id}/files-index` before and after and compare path-translated
records, hashes and counts, then repeat the command and require `moved_files=0`.
If a client registers previously unindexed records during maintenance, the
tool refuses changed maps. Preserve the journal, reconcile, and re-plan safely.

Protocol regression (without production access):

```sh
uv run --with pytest --with pycrdt==0.12.50 --with sqlalchemy --with httpx \
  --with 'psycopg[binary]' python -m pytest scripts/test_relocate_artifacts.py -q -s
```

The PostgreSQL tests require `RELOCATION_TEST_DATABASE_URL` pointing at a
disposable loopback database named `artifact_relocation_test`. The complete
backup CLI test requires PostgreSQL client binaries or accepts
`RELOCATION_TEST_PGDUMP_DOCKER`, the name
of a disposable PostgreSQL container (user `relocation`, same database name).
That test uses its real `pg_dump` and `pg_restore`, while mocking HTTP transport;
it is not an Obsidian or production acceptance test.

To exercise the actual plugin's FolderIndex observer against the generated
updates (plugin dependencies must be installed):

```sh
node scripts/check_relocation_folder_index.cjs /path/to/plugin /path/to/fixtures
```

Use `before.yjs` and `move.yjs` produced by the offline-peer test in its pytest
temporary directory. This checks the actual rename observer's folder and child
aliases, with logging and feature-toggle host modules stubbed. It does not
exercise Obsidian's filesystem or replace the offline-vault acceptance test.
