# ADR: relay-server is our own y-sweet fork, NOT the System3 binary

**Status:** approved 2026-07-07.

## Decision (read this before touching relay-server)

`relay-server` runs our **own image `ghcr.io/entire-vc/evc-relay-server`**, pinned by tag,
built from a fork of **`no-instructions/y-sweet`** (MIT).

**The tag lives in `infra/docker-compose.yml` and only there.** This ADR deliberately does not
repeat it: the number written here said `:0.9.7` while the compose file said `:0.9.9` and the
production host was running `:0.9.10` — three answers to one question, because a version copied
into prose has no way of staying true. Read the compose file.

We used to pull the closed `docker.system3.md/relay-server` binary (System3 = a commercial
y-sweet distributor). PR #99 swapped it for our ghcr image. **The System3 binary and its access
token are no longer used.**

## Why we forked
Vendor lock on the most critical component (CRDT sync + document persistence); the image was
pulled `:latest` (unreproducible); we couldn't audit/fix token-scope enforcement (it was their
code); and it required a secret access token from System3. y-sweet is MIT and self-buildable
(`crates/relay` + `crates/Dockerfile`), so we build it ourselves.

## Common confusion — "didn't we write the token logic ourselves?"
Two layers, don't conflate:
- **Issuer (mints/signs tokens) = our control-plane** — `apps/control-plane/app/core/security.py`
  (Ed25519 keygen, `create_relay_token`, `create_relay_token_cwt`) + `token_service.py`. Always ours.
- **Verifier (checks tokens) = relay-server** — `crates/y-sweet-core/src/cwt.rs`. Was upstream's;
  **now ours too** after the fork. The whole token path is under our control.

## Where documents live (matters for backups)
`relay.toml [store] type = "minio", endpoint = "http://minio:9000"` → CRDT docs, web content and
uploads sit in the **local MinIO** volume (`relay_minio-data`), NOT external S3. That MinIO **must
be backed up off-site** (see `docs/backup-restore.md`).

## Still-stale (ignore for our deployment)
`forks/relay-server-template/` are vendored upstream deploy templates (Fly/Docker/k8s) that still
reference `docker.system3.md/relay-server`. **That is not our deployment** — ours is the ghcr image
in `infra/docker-compose.yml`. Kept only as reference.
