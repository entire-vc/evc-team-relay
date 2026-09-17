# Relay Server

[Relay](https://relay.md) adds real-time collaboration to Obsidian. Share exactly the folders you want, keep the rest of your vault private, and work together even when offline. The server in this repository powers that experience.

Relay Server is a fork of [jamsocket/y-sweet](https://github.com/jamsocket/y-sweet). It exposes the same CRDT-based document store under a new name and integrates with Relay's Control Plane for authentication and permissions.

---

## Fork origin

This repository (`entire-vc/evc-relay-server`) is a fork of [`No-Instructions/relay-server`](https://github.com/No-Instructions/relay-server).

**Upstream base pinned:** [`5d4fd161604dde305ac45f200eb8eca09c7c7f15`](https://github.com/No-Instructions/relay-server/commit/5d4fd161604dde305ac45f200eb8eca09c7c7f15) (2026-07-06)

**Cherry-picked on top of that base:**

| Upstream commit | Why taken |
|---|---|
| [`d7ebd31`](https://github.com/No-Instructions/relay-server/commit/d7ebd3158920a6e068fee16b571ce8f57cfd9362) — *fix: guard file metadata paths and backport Yrs delete handling* | Blocks `..` path-traversal keys in the `filemeta_v0` map, and pins `yrs` to a revision that stops an out-of-order delete resurrecting a removed entry. `filemeta_v0` is the shared-folder file index used by both this server and the Obsidian plugin, so both halves are reachable here. |

**Deliberately NOT taken** (reviewed 2026-08-19): the eight-commit document-lifecycle
series `4afa487 · a0daf70 · 58cd5e1 · 82f628b · dd46897 · b3aa795 · 042fa00 ·
2103054`, plus `fa1d3c9` (log-level tuning). That series is a coherent
architectural rework — it introduces `doc_lifecycle.rs`, moves document identity
into a `DocRegistry`, then hands eviction to a lifecycle actor, ~3000 changed
lines in total. Only its first commit cherry-picks cleanly; the rest conflict
without their predecessors. Adopting it is a project with its own behavioural
test plan, not a cherry-pick. `e79633d` (ignore `specs/`) does not apply — this
fork has no `specs/` directory.

**Check how far behind we are:**

```bash
git remote add upstream https://github.com/No-Instructions/relay-server.git  # if absent
git fetch upstream
git log --oneline --date=short --format='%h %ad %s' origin/main..upstream/main
```

### What this fork builds, and what it dropped

**The only thing this repository builds and ships is the Rust `relay` binary** in
[`crates/`](crates/). The published image is built with `context: ./crates` and
copies exactly one artifact out of the builder — `/build/target/release/relay`.

Three upstream subtrees were removed in 2026-08. Nothing referenced them, nothing
built them, and nothing deployed them — but each carried its own dependency
manifest, so they kept generating security alerts against code that could not be
reached from any artifact we publish. At the time of removal **17 of 23 open
alerts (74%) were against these three directories**, and none had ever been
exposed in production.

| Removed | What it was | Why it went |
|---|---|---|
| `debugger/` | upstream y-sweet debug UI (Next.js) | no reference anywhere outside itself; not in the image |
| `crates/y-sweet-worker/` | Cloudflare Workers variant (Rust + wasm + wrangler) | listed in the workspace's `exclude`, so never compiled; its `Cargo.lock` had drifted from its own manifest, making any dependency bump a full re-resolution |
| `python/` | upstream `relay_sdk` Python client for relay.md | consumed by nothing here or in the Team Relay repos; the name `relay_sdk` normalises to `relay-sdk` on PyPI, which belongs to Puppet, Inc., so it could never be published under that name |

Two scripts went with them — `crates/build_python_alpine.sh` and
`crates/test_alpine_wheel.sh`. Both targeted `python/y-sign-py`, a path that has
never existed in this fork, so they were already inoperable.

**Nothing is lost — restore any of them from upstream in one command:**

```bash
git remote add upstream https://github.com/No-Instructions/relay-server.git  # if not present
git fetch upstream
git checkout upstream/main -- debugger/                 # or crates/y-sweet-worker/, python/
```

If you restore `crates/y-sweet-worker/`, put `y-sweet-worker` back into the
`exclude` list in `crates/Cargo.toml`, and expect to regenerate its `Cargo.lock`
from scratch — building it needs the `wasm32-unknown-unknown` target plus
`worker-build`, neither of which this repo's tooling installs.

### Relationship to upstream

This fork has **never merged from upstream** — it was pinned at the commit above
and has carried its own commits since. Upstream continues to move; check
`git log origin/main..upstream/main` before assuming a bug is ours.

---

## Self-hosting

> :information_source: **Note:** The Relay Server and Relay Obsidian Plugin are open source, but the Relay Control Plane is not open source. Using a Self-Hosted Relay Server with more than 3 collaborators requires a paid license to support the development of Relay.


Self-hosting within your private network gives you complete privacy for your documents and attachments. Relay's Control Plane handles login and permissions, but cannot read your content.

### Quick-start

```
# mounts local volume, see production deployment guide for S3-compatible storage
docker run \
  -v data:/app/data \
  -p 8080:8080 \
  ghcr.io/entire-vc/evc-relay-server:0.9.9 \
  http://relay-server.my-network.internal:8080  # Your internal network URL
```

Register your server using the Relay Obsidian plugin:

1. Log in
2. Run the command `Relay: Register self-hosted Relay Server`
3. Enter the relay-server URL (must match above)


Don't expose the Relay Server to the public internet.

### Production deployment

The recommended setup uses Docker with Cloudflare R2 for persistence.

See [relay-server-template](https://github.com/no-instructions/relay-server-template) for detailed hosting instructions and deployment templates.


## Features

 - Real-time collaboration engine built atop y-crdt, enabling high-performance conflict-free shared editing
 - Use the Relay.md control plane for login and access control management
 - Fully private self-hosting of your documents and attachments (no connection to the public internet required!)
 - 1-step deployment into your Tailscale Tailnet
 - Persistence to S3-compatible object storage (S3, Cloudflare R2, Minio)
 - Flexible deployment/isolation with single server or session-per-document model
 - Python SDK
 - Webhook Event Delivery

## Configuration

Configuration can be provided via a relay.toml file, or via environment variables.

```toml
# relay.toml
[server]
url = "https://relay.example.com"
host = "0.0.0.0"
port = 8080

# Public key of the control plane you trust to issue tokens.
# Every key listed here can mint tokens this server accepts, so use your own
# control plane's key — do not carry over keys from another deployment.
[[auth]]
key_id = "my_control_plane_2026_01_01"
public_key = "<base64 public key>"

# Document and attachment persistence
# Supports S3-compatible storage
[store]
type = "aws"
bucket = "my-bucket"
region = "us-east-1"
access_key_id = "AKIA..."        # or set AWS_ACCESS_KEY_ID
secret_access_key = "secret..."  # or set AWS_SECRET_ACCESS_KEY
prefix = ""
```


## Support and contact

- **Bug, question, or feature request** — [open an issue](https://github.com/entire-vc/evc-relay-server/issues/new/choose).
- **Security vulnerability** — [report it privately](https://github.com/entire-vc/evc-relay-server/security/advisories/new), not as a public issue.
- **Anything you would rather not discuss in public** — <support@entire.vc>.

Response times and what to put in a bug report are in [SUPPORT.md](SUPPORT.md).


## Acknowledgements

This repository is a fork of [Relay Server](https://github.com/No-Instructions/relay-server) by No Instructions, LLC, which is itself built on [y-sweet](https://github.com/jamsocket/y-sweet) by the folks at Jamsocket, which in turn uses [y-crdt](https://github.com/y-crdt/y-crdt).

The server source code is MIT licensed.