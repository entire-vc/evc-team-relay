# Team Relay — Deployment Runbook

> **Deploy discipline:** migrations MUST run and succeed BEFORE the app image starts.
> Failed migration = blocked deploy, never the reverse.

## Overview

Team Relay is deployed on a relay host via Docker Compose. Production images are built locally on
the server from synced source (`$RELAY_DIR/control-plane-src/`, `/opt/relay` by default), not
pulled from a registry.

Throughout this runbook, `tr-relay-vm` is an **SSH alias, not a hostname** — it is the default
`SSH_TARGET` in [`scripts/deploy.sh`](../scripts/deploy.sh). Define it in your own
`~/.ssh/config`, pointing at your relay server (with a `ProxyJump` if the server has no public
address), and every command below works verbatim; or override it per invocation with
`SSH_TARGET=<your-alias>`.

If you keep a standby machine for rollback, it is **not** a deploy target: do not SSH into it and
run these recipes there.

The `control-plane-migrate` service in `docker-compose.yml` is the **fail-closed gate**: it runs
`alembic upgrade head` and must exit 0 before `control-plane` (or any worker) starts. `web-publish`
has no migrations, so its deploy skips straight to build + restart.

Three ways to deploy:
- **[Automated (CI)](#automated-deploy-ci)** — control-plane only, driven from a separate
  deployment project as a **manual** job. It is deliberately not push-triggered: a human starts it.
- **[`scripts/deploy.sh` from a local checkout](#scriptsdeploysh-local-driver)** — still the path
  for **web-publish** (which CD does not cover), and the fallback for control-plane when CI
  is unavailable. Run it from your machine; it rsyncs the source and runs the same
  build/gate/restart logic on the relay host over SSH.
- **[Manual (SSH)](#manual-upgrade-fallback)** — the emergency fallback when you're already on the
  box, or want to run the steps by hand.

---

## Automated deploy (CI)

On-server logic: [`scripts/deploy.sh`](../scripts/deploy.sh) — the single source of the
build/gate/restart sequence, whichever driver invokes it.

**Trigger** — manual only. A `deploy` job builds and ships the control plane, and a `verify` job
follows automatically on success; `DRY_RUN` defaults to `false`. The deploy job clones this repo
at a chosen ref rather than keeping a copy of the source in the deployment project.

> `.github/workflows/deploy.yml` is the reference implementation of the same sequence as a GitHub
> Actions workflow — host endpoints come from repository variables, credentials from secrets. Note
> its path filter deliberately excludes `.github/**`: a CI-plumbing edit is not a reason to restart
> production, and a pipeline listing its own path would redeploy prod the instant it merged.

**What it does** (one job, `environment: production`):
1. Writes the deploy key + pinned host keys and builds an SSH config that reaches the relay host
   (via `ProxyJump` through a bastion, if the host has no public address), then proves
   connectivity with a cheap `hostname` call before touching anything.
2. `rsync -az --delete apps/control-plane/ → <relay host>:/opt/relay/control-plane-src/`
   (syncs only the app source; never touches the server-managed `docker-compose.yml` / `.env`).
3. Runs `scripts/deploy.sh` over SSH, which on the server: tags the current image as
   `:prev`, builds `infra-control-plane:latest`, runs the **migration gate**, and — only on
   gate success — `docker compose up -d control-plane webhook-worker email-worker
   listmonk-sync-worker lifecycle-worker`.

**Fail-closed guarantee.** `scripts/deploy.sh` runs under `set -euo pipefail` and the migration
gate is an explicit check:

```bash
if ! docker compose run --rm control-plane-migrate; then
  die "MIGRATION GATE FAILED — app NOT restarted ..."   # exits non-zero
fi
docker compose up -d control-plane webhook-worker email-worker listmonk-sync-worker lifecycle-worker   # unreachable on failure
```

A non-zero exit from `alembic upgrade head` ends the script (and fails the workflow step)
**before** `compose up` is ever reached. Production keeps running the previous image.

**Rehearsing the gate without restarting the app.**
Setting `DRY_RUN=true` builds the image into a throwaway `:candidate` tag and runs an offline
migration check against it (`alembic upgrade head --sql` — renders the SQL a real upgrade would
execute and validates the revision graph, without opening a database connection), then stops
before touching `:latest`/`:prev` or restarting anything. Run it the same way as a real deploy,
just with the flag set — via the deploy pipeline's `DRY_RUN` job variable, or manually on the
host:

```bash
ssh tr-relay-vm
RELAY_DIR=/opt/relay DRY_RUN=true bash -s -- control-plane < scripts/deploy.sh
```

The check runs through `docker compose run --rm -T control-plane-migrate-dry-run`
(`infra/docker-compose.yml`), not `docker run --env-file` — the latter cannot parse a multi-line
value (`/opt/relay/.env` carries an ED25519 PEM key) and used to fail with `variable
'-----END PRIVATE KEY-----"' contains whitespaces` before alembic ever ran, which read as a
broken migration graph rather than what it actually was. Compose's own `env_file` parser handles
multi-line values correctly, matching the real (online) gate below, which already went through
compose for the same reason. Fixed 2026-08-23 — the rehearsal appears never to
have been executed before that, since CI never set `DRY_RUN=true`; two migrations
(`202608200002`, `202608200003`) also needed a `context.is_offline_mode()` guard around code that
assumed a real online connection, surfaced only once the transport bug stopped masking them.

A genuinely broken revision graph still fails fast and names the graph, not the environment —
e.g. a bad `down_revision` fails with `KeyError: '<revision-id>'` in under a second, no DB
connection attempted either way.

The **real** deploy keeps its own fail-closed gate regardless: migrations run through compose
before `up`, and their failure cancels the deploy.

### Required repository secrets and variables

These are what `.github/workflows/deploy.yml` reads. Set them under **Settings → Secrets and
variables → Actions**, or scope them to the `production` environment, which also lets you add a
manual-approval protection rule. The workflow fails closed on its first step if any of them is
missing or empty.

**Secrets** — values that must never appear in a log:

| Secret | Required | Description |
|--------|----------|-------------|
| `TW_RELAY_SSH_KEY_B64` | yes | Private half of a dedicated ed25519 deploy key, **base64-encoded**. Its public half goes in the relay host's `~/.ssh/authorized_keys` and, if you jump through a bastion, in the bastion account's `authorized_keys` as a restricted entry (see the network note below). |
| `TW_RELAY_KNOWN_HOSTS_B64` | yes | Pinned host keys for **every** hop (bastion and target), **base64-encoded**. There is no TOFU fallback — an unknown or changed host key fails the deploy. |

**Variables** — deploy topology, which is configuration rather than secret:

| Variable | Required | Description |
|----------|----------|-------------|
| `DEPLOY_TARGET_HOST` | yes | Address or resolvable name of the relay host, as seen *from the bastion* when one is used. |
| `DEPLOY_PROXY_HOST` | yes | Address or name of the bastion the runner jumps through. |
| `DEPLOY_TARGET_USER` | no | SSH user on the relay host (default `root`). |
| `DEPLOY_TARGET_PORT` | no | SSH port on the relay host (default `22`). |
| `DEPLOY_PROXY_USER` | no | SSH user on the bastion (default `ghdeploy`). |

> **Why the hosts are variables and not secrets.** GitHub masks every secret as `***` in run logs.
> That is exactly wrong for a hostname: when the target host value once turned out to carry two
> stray characters (13 bytes for an 11-byte address), the resulting `no pinned host key` failure
> was impossible to diagnose from the run output. Variables are unmasked, so the logs stay
> readable — and, unlike a literal in the workflow file, they keep a fork of this repository from
> carrying anyone's deploy topology.

> **Why both secrets are base64.** A raw multi-line value does not survive `gh secret set` intact.
> Measured on this repository during bring-up: a 12-line `known_hosts` arrived at the runner as its
> **last line only** (94 bytes, 0 newlines), leaving the ProxyJump hop entirely unpinned; and a
> 432-byte / 8-line private key arrived as **418 bytes / 7 lines**, which presents as a bare
> `Permission denied (publickey)`. Base64 is a single line, so there is nothing to truncate —
> verified byte-identical on round-trip.
>
> Regenerate them with:
>
> ```bash
> # known_hosts (every hop). BASTION/TARGET are the values you put in
> # DEPLOY_PROXY_HOST / DEPLOY_TARGET_HOST.
> { ssh-keyscan -t rsa,ecdsa,ed25519 "$BASTION"
>   ssh "$BASTION" "ssh-keyscan -t rsa,ecdsa,ed25519 $TARGET"
> } | base64 | tr -d '\n' | gh secret set TW_RELAY_KNOWN_HOSTS_B64 -R <owner>/<repo>
>
> # private key
> base64 < /path/to/deploy_key | tr -d '\n' \
>   | gh secret set TW_RELAY_SSH_KEY_B64 -R <owner>/<repo>
> ```
>
> **The workflow does not trust either value.** After decoding it asserts that both SSH hops are
> actually pinned (`ssh-keygen -F`) and that the private key's fingerprint matches
> `DEPLOY_KEY_FINGERPRINT`, pinned in plain `env:` in the workflow. That pin is a public-key
> fingerprint, not a credential — keeping it in the file means rotating the key and updating its
> pin are the same reviewed change. If you rotate the key without updating it, the deploy fails
> closed, by design, naming the mismatch rather than dying as an anonymous auth error.

> **Network note.** A relay host with no public address can sit on a private network behind a
> bastion; the workflow reaches it from a stock `ubuntu-latest` runner via `ProxyJump`.
>
> Give the bastion account the narrowest grant that still works. Make it a non-shell account
> (`/usr/sbin/nologin`) and pin the deploy key in its `authorized_keys` with a forced command and
> a `permitopen` restriction naming only this product's own host and port:
>
> ```
> command="/bin/false",restrict,port-forwarding,permitopen="<target-host>:22" ssh-ed25519 AAAA... deploy-key-comment
> ```
>
> The credential the workflow holds can then do exactly one thing: open a TCP forward to port 22
> of that one host. Verify it: a shell attempt returns *"This account is currently not available"*,
> and forwarding to any other machine is refused with *"administratively prohibited"*.
>
> > **Do not move this job to a self-hosted runner.** This repository is public and forkable, and
> > `ci.yml`/`trivy.yml` trigger on `pull_request`. GitHub runs a fork PR's workflow file *from the
> > PR branch*, so a self-hosted runner registered here could be hijacked by a fork PR that
> > re-points `runs-on` at it — onto a machine holding a production SSH key. Secrets are never
> > exposed to fork-PR workflows, which is precisely why the GitHub-hosted + secret design is the
> > safe one here.

---

## `scripts/deploy.sh` (local driver)

CD covers **control-plane** only, so this remains the way to ship **web-publish**, and the
fallback for control-plane when Actions is unavailable. Run it from a local checkout; it has
access to `tr-relay-vm` via the same
alias your `~/.ssh/config` already uses for manual SSH.

```bash
bash scripts/deploy.sh control-plane   # control-plane + workers (migration gate + edition smoke gate)
bash scripts/deploy.sh web-publish     # web-publish only (no migrations, no edition gate)
bash scripts/deploy.sh all             # both, control-plane first
bash scripts/deploy.sh                 # defaults to control-plane
```

**What it does**, per component, when `$RELAY_DIR` (default `/opt/relay`) doesn't exist locally
(i.e. you're not already on the server): rsyncs `apps/<component>/` →
`tr-relay-vm:/opt/relay/<component>-src/`, then re-invokes itself over SSH on `tr-relay-vm` to run
the actual build/gate/restart — the exact same server-side logic the Actions workflow invokes,
unified into one script instead of split between a CI rsync step and this script.

- **control-plane**: tag `:prev` → `docker build` → migration gate (fail-closed) →
  `compose up -d --force-recreate` → health check → edition smoke gate (auto-rolls back on
  failure). Unchanged from before this script covered web-publish too.
- **web-publish**: tag `:prev` → `docker build --secret id=github_token,...` (the Dockerfile's
  `npm ci` needs a GitHub token for scoped package installs; taken from `$GITHUB_TOKEN` in your
  shell, or read from `tr-relay-vm:/opt/relay/.env` if unset) → `compose up -d --force-recreate` →
  health check. No migration gate — web-publish has no migrations — and no edition smoke gate,
  that check is control-plane/billing-specific.

`DRY_RUN=true bash scripts/deploy.sh <component>` builds the image and runs the migration gate
(control-plane only) but stops before `compose up` — use it to rehearse before a real deploy.
`SSH_TARGET` overrides the remote host (default `tr-relay-vm`, the alias described in the
overview) if you're ever deploying elsewhere.

If you're **already SSH'd into `tr-relay-vm`** with the source already synced, running the script
there directly (`RELAY_DIR=/opt/relay bash -s -- web-publish < scripts/deploy.sh`, or just running
a copy of it on the box) skips the rsync/re-invoke step and goes straight to build/restart — that's
"direct mode", same as how the Actions workflow always invoked it.

---

## Manual upgrade (fallback)

Use this when CI is unavailable or for an emergency hotfix. It is the same sequence the
automated pipeline runs.

```bash
# 1. SSH to server
ssh tr-relay-vm
cd /opt/relay

# 2. Tag the current image BEFORE overwriting (enables fast rollback)
docker tag infra-control-plane:latest infra-control-plane:prev

# 3. Build new image from updated source
docker build -t infra-control-plane:latest control-plane-src/

# 4. Run the migration gate
#    — exits 0: migrations applied (or already at head) → proceed
#    — exits non-zero: STOP, do NOT restart the app, investigate
docker compose run --rm control-plane-migrate

# 5. Restart app services ONLY after migrate exits 0
docker compose up -d control-plane webhook-worker email-worker listmonk-sync-worker lifecycle-worker
```

Using `docker compose up -d` (whole stack) is also safe: the `depends_on:
condition: service_completed_successfully` on `control-plane` enforces the gate automatically —
migration failure prevents the app from starting.

> **Never** run `docker run infra-control-plane:latest` directly — it bypasses the compose
> dependency graph and the migration gate.

### web-publish

No migration gate — there's nothing to migrate. Source lives in `/opt/relay/web-publish-src/`,
kept in sync the same way as `control-plane-src/` (rsync from a local checkout, or
`scripts/deploy.sh web-publish`, which is the recommended path over doing this by hand).

```bash
# 1. SSH to server
ssh tr-relay-vm
cd /opt/relay

# 2. Tag the current image BEFORE overwriting (enables fast rollback)
docker tag infra-web-publish:latest infra-web-publish:prev

# 3. Build new image — the Dockerfile's `npm ci` needs GITHUB_TOKEN as a BuildKit secret
GITHUB_TOKEN=$(grep -m1 '^GITHUB_TOKEN=' .env | cut -d= -f2-)
docker build --secret id=github_token,env=GITHUB_TOKEN -t infra-web-publish:latest web-publish-src/

# 4. Restart
docker compose up -d --force-recreate web-publish
```

---

## Rollback

**control-plane:**

```bash
# 1. Stop app services (keep postgres and minio running — do NOT stop the DB)
docker compose stop control-plane webhook-worker email-worker listmonk-sync-worker lifecycle-worker

# 2. Restore previous image
docker tag infra-control-plane:prev infra-control-plane:latest

# 3. If a migration was partially applied, revert it first
docker compose run --rm control-plane-migrate python -m alembic current
docker compose run --rm control-plane-migrate python -m alembic downgrade <safe-revision>

# 4. Restart with restored image
docker compose up -d control-plane webhook-worker email-worker listmonk-sync-worker lifecycle-worker
```

Since 2026-08-06 all five services (`control-plane`, `webhook-worker`, `email-worker`,
`listmonk-sync-worker`, `lifecycle-worker`) share the single `infra-control-plane:latest` tag —
step 2 rolls back all five at once, by construction. There is no per-worker `:prev` tag to manage.

**web-publish** (no migrations to revert):

```bash
docker compose stop web-publish
docker tag infra-web-publish:prev infra-web-publish:latest
docker compose up -d web-publish
```

---

## Checking migration state

```bash
# Current applied revision
docker compose run --rm control-plane-migrate python -m alembic current

# Full history
docker compose run --rm control-plane-migrate python -m alembic history
```

---

## docker-compose.yml gate

**All six control-plane-family services MUST share one image tag: `infra-control-plane:latest`.**
`control-plane`, `control-plane-migrate`, `webhook-worker`, `email-worker`,
`listmonk-sync-worker`, and `lifecycle-worker` all run the exact same image — only `command:`
differs. `scripts/deploy.sh` only ever builds and tags `infra-control-plane:latest` (step 2 of
`deploy_control_plane`); it never builds anything else. Any service in this family declaring its
own distinct `image:` (e.g. `infra-webhook-worker:latest`) will **never be rebuilt by the deploy
pipeline** — it sits on whatever content it had the day someone first `docker build`-ed that tag
by hand, silently drifting from the rest of the fleet. This is exactly what happened
2026-07-25 → 2026-08-06: the four workers held `cryptography==48.0.1` while `control-plane` had
already moved to `50.0.0`. Every `up -d --force-recreate` looked successful —
the containers restarted fine — because recreate only swaps which image tag a container runs,
it does not rebuild that tag.

The production `docker-compose.yml` must include:

```yaml
control-plane-migrate:
  image: infra-control-plane:latest
  env_file:
    - ./.env
  environment:
    DATABASE_URL: ${DATABASE_URL}
  depends_on:
    postgres:
      condition: service_healthy
  command: ["python", "-m", "alembic", "upgrade", "head"]
  restart: "no"

control-plane:
  image: infra-control-plane:latest
  depends_on:
    postgres:
      condition: service_healthy
    control-plane-migrate:
      condition: service_completed_successfully   # <-- fail-closed gate
    minio-init:
      condition: service_completed_successfully

webhook-worker:
  image: infra-control-plane:latest        # <-- same tag as control-plane, NOT infra-webhook-worker
  command: ["python", "-m", "app.workers.webhook_worker"]

email-worker:
  image: infra-control-plane:latest        # <-- same tag, NOT infra-email-worker
  command: ["python", "-m", "app.workers.email_worker"]

listmonk-sync-worker:
  image: infra-control-plane:latest        # <-- same tag, NOT infra-listmonk-sync-worker
  command: ["python", "-m", "app.workers.listmonk_sync_worker"]

lifecycle-worker:
  image: infra-control-plane:latest        # <-- same tag, NOT infra-lifecycle-worker
  command: ["python", "-m", "app.workers.lifecycle_worker"]
```

The same `service_completed_successfully` guard applies to every worker (they either depend on
`control-plane` directly, or transitively require `control-plane-migrate` to succeed).

**Verifying the gate holds** (agent-runnable, no eyeballing):

```bash
ssh tr-relay-vm "for c in relay-control-plane-1 relay-email-worker-1 relay-webhook-worker-1 relay-listmonk-sync-worker-1 relay-lifecycle-worker-1; do echo -n \"\$c: \"; docker exec \$c python -c 'import cryptography;print(cryptography.__version__)'; done"
# all five lines must print the same version
```

---

## Notes & limitations

- **Server-managed compose.** The production `docker-compose.yml` lives on `tr-relay-vm`
  (`/opt/relay/`) and uses `image: infra-control-plane:latest` (built locally) on all six
  control-plane-family services, whereas the repo's `infra/docker-compose.yml` is the
  `build:`-context variant of the same gate (each service keeps its own `build:` block for
  self-contained local/self-hosted bring-up, but all six also tag `image: infra-control-plane:latest`
  so a rebuild-then-recreate cycle can't leave one service behind — see §docker-compose.yml gate).
  The deploy pipeline deliberately syncs **only** `apps/control-plane/` → `control-plane-src/` and
  leaves the server's compose file and `.env` alone — this means fixing the compose *structure*
  (as opposed to the app source) always requires a manual edit on `tr-relay-vm` itself, backed up
  first (`cp docker-compose.yml docker-compose.yml.bak-<ts>-<reason>`).
- **Worker image consolidation (2026-08-06).** Before this date `webhook-worker`,
  `email-worker`, `listmonk-sync-worker`, and `lifecycle-worker` each had their own `image:` tag
  (`infra-webhook-worker:latest` etc.), built once by hand and never touched again by
  `scripts/deploy.sh` — 12 days of dependency drift went undetected because `--force-recreate`
  makes a stale container look freshly deployed. All four now share `infra-control-plane:latest`
  with `control-plane`; the old per-worker tags are left on the host (unused, not deleted) as a
  rollback path. The old tags will accumulate as dead weight — safe to `docker rmi` them after a
  few successful deploys confirm the new tag is stable.
- **web-publish** is covered by `scripts/deploy.sh web-publish` (see above) — merges to
  `apps/web-publish/` do NOT deploy themselves; someone has to run the script. This was a real gap:
  three merged fixes once sat undeployed for up to 3 days because nothing rebuilt the
  container. **relay-server** (the Rust Yjs relay, a separate repository) is still not
  covered by anything here — rebuild it manually if its source changes.
- **`infra/Caddyfile` is host-managed and NOT synced by the deploy pipeline** (same gap as the
  compose file above). A fix applied here must ALSO be applied live on `tr-relay-vm`
  (`/opt/relay/Caddyfile`, content-preserving write + `caddy validate` + `caddy reload` inside
  `relay-caddy-1` — it's a bind-mounted single file, don't `sed -i` it, write a fresh copy so the
  inode is preserved) or it silently only exists in git. Confirmed drifted at least once already
  (a `/metrics` block, and a WS-token-stripping fix) — always diff live vs repo before
  assuming they match.
- **Firewall.** See the network note under
  [Required repository secrets and variables](#required-repository-secrets-and-variables) if
  hosted CI runners can't reach the relay host.
- **`infra/Caddyfile` is NOT deployed by CD or the manual steps above — sync it by hand, every
  time.** Neither the automated pipeline nor the manual upgrade recipe touches
  `/opt/relay/Caddyfile`; it's a `docker-compose.yml`-managed bind mount the deploy tooling
  deliberately leaves alone (same reasoning as the server-managed compose file, above), but
  unlike compose/`.env` there is no independent reason for it to diverge from git — it's meant to
  track `infra/Caddyfile` exactly. This has silently regressed the public `/metrics` block twice —
  most recently by surviving a host migration, since a host migration copies data/config that was
  already on the box, not what's in git. **After editing `infra/Caddyfile`, or after any host migration, manually sync
  it:**
  ```bash
  scp infra/Caddyfile tr-relay-vm:/opt/relay/Caddyfile   # back up the old one on the host first
  ssh tr-relay-vm "docker exec relay-caddy-1 caddy validate --config /etc/caddy/Caddyfile"
  ssh tr-relay-vm "docker exec relay-caddy-1 caddy reload --config /etc/caddy/Caddyfile"
  ```
  Then verify from **outside** the container — `caddy reload`'s own success message is not proof
  the running config changed (e.g. a single-file bind mount can retain a stale inode after some
  edit methods; confirm `stat -c %i` matches between host and `docker exec ... stat` before
  trusting `reload`, and always curl the actual external behavior afterward, not just the exit
  code).

---

## References

- `.github/workflows/deploy.yml` — reference CI implementation of the automated deploy
  (control-plane only)
- `scripts/deploy.sh` — rsync (driver mode) → build → migrate-gate (control-plane only) → restart,
  for both control-plane and web-publish
- `infra/docker-compose.yml` — dev/local compose template (build-context variant of same gate)
- `apps/control-plane/app/db/migrations/versions/` — Alembic migration files
- `apps/control-plane/alembic.ini` — Alembic configuration
