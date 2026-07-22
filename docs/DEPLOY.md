# Team Relay — Deployment Runbook

> **Rule §1b** (CLAUDE-workflow.md §1b Deploy Discipline): migrations MUST run and succeed
> BEFORE the app image starts. Failed migration = blocked deploy, never the reverse.

## Overview

Team Relay is deployed on `tr-relay-vm` (`10.10.10.40`, Helsinki) via Docker Compose. Production
images are built locally on the server from synced source (`/opt/relay/control-plane-src/`), not
pulled from a registry.

> `tw-relay` (`64.188.59.168`) is **standby/rollback-only** since the 2026-07-09 Helsinki cutover —
> it is not a deploy target. Do not `ssh tw-relay` and run the deploy recipe there.

The `control-plane-migrate` service in `docker-compose.yml` is the **fail-closed gate**: it runs
`alembic upgrade head` and must exit 0 before `control-plane` (or any worker) starts.

Two ways to deploy:
- **[Automated (GitHub Actions)](#automated-deploy-github-actions)** — the default path. Push to
  `main` (or manual dispatch) runs the gated sequence on `tr-relay-vm`.
- **[Manual (SSH)](#manual-upgrade-fallback)** — the emergency fallback, also the underlying
  procedure the pipeline automates.

---

## Automated deploy (GitHub Actions)

Workflow: [`.github/workflows/deploy.yml`](../.github/workflows/deploy.yml).
On-server logic: [`scripts/deploy.sh`](../scripts/deploy.sh).

**Triggers**
- `push` to `main` touching `apps/control-plane/**`, `scripts/deploy.sh`, or the workflow itself.
- `workflow_dispatch` (manual), with a `dry_run` boolean input.

**What it does** (one job, `environment: production`):
1. Loads an SSH key and trusts the `tr-relay-vm` host key.
2. `rsync -az --delete apps/control-plane/ → tr-relay-vm:/opt/relay/control-plane-src/`
   (syncs only the app source; never touches the server-managed `docker-compose.yml` / `.env`).
3. Runs `scripts/deploy.sh` over SSH, which on the server: tags the current image as
   `:prev`, builds `infra-control-plane:latest`, runs the **migration gate**, and — only on
   gate success — `docker compose up -d control-plane webhook-worker email-worker`.

**Fail-closed guarantee.** `scripts/deploy.sh` runs under `set -euo pipefail` and the migration
gate is an explicit check:

```bash
if ! docker compose run --rm control-plane-migrate; then
  die "MIGRATION GATE FAILED — app NOT restarted ..."   # exits non-zero
fi
docker compose up -d control-plane webhook-worker email-worker   # unreachable on failure
```

A non-zero exit from `alembic upgrade head` ends the script (and fails the workflow step)
**before** `compose up` is ever reached. Production keeps running the previous image.

**Rehearsing the gate without restarting the app.** Trigger the workflow manually with
`dry_run: true` (Actions → Deploy → Run workflow). The pipeline builds the image and runs the
migration gate, then **stops** — it never issues `compose up`. Use this to confirm a migration
applies cleanly before a real deploy, or to verify the fail-closed behaviour: a deliberately
broken migration makes the `dry_run` run fail at the gate step with the app untouched.

### Required repository secrets

Set these under **Settings → Secrets and variables → Actions** (or scoped to the `production`
environment, which also lets you add a manual-approval protection rule):

| Secret | Required | Description |
|--------|----------|-------------|
| `TW_RELAY_SSH_KEY` | ✅ | Private SSH key (PEM) whose public half is in `tr-relay-vm:~/.ssh/authorized_keys`. Use a dedicated deploy key, not a personal one. |
| `TW_RELAY_HOST` | ✅ | Deploy host address (e.g. `10.10.10.40` for `tr-relay-vm`, current prod). Unset — this job is currently disabled (`if: ${{ false }}`, see below). |
| `TW_RELAY_USER` | ✅ | SSH user with permission to run `docker` (e.g. `root`). |
| `TW_RELAY_PORT` | — | SSH port. Defaults to `22`. |
| `TW_RELAY_PATH` | — | Deploy root on the server. Defaults to `/opt/relay`. |
| `TW_RELAY_KNOWN_HOSTS` | — | Pinned host key line(s) from `ssh-keyscan tr-relay-vm`. If unset, the workflow falls back to TOFU `ssh-keyscan` at run time. **Pinning is recommended.** |

> **Network note.** The workflow runs on GitHub-hosted runners and would SSH to `tr-relay-vm`
> (`10.10.10.40`), a private IP reachable only via `ProxyJump hel01` — GitHub-hosted runners cannot
> reach it directly. Re-enabling this job requires a `tailscale/github-action` step before the SSH
> steps (the fleet's standard), or moving the job to a self-hosted runner on the tailnet.

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
docker compose up -d control-plane webhook-worker email-worker
```

Using `docker compose up -d` (whole stack) is also safe: the `depends_on:
condition: service_completed_successfully` on `control-plane` enforces the gate automatically —
migration failure prevents the app from starting.

> **Never** run `docker run infra-control-plane:latest` directly — it bypasses the compose
> dependency graph and the migration gate.

---

## Rollback

```bash
# 1. Stop app services (keep postgres and minio running — do NOT stop the DB)
docker compose stop control-plane webhook-worker email-worker

# 2. Restore previous image
docker tag infra-control-plane:prev infra-control-plane:latest

# 3. If a migration was partially applied, revert it first
docker compose run --rm control-plane-migrate python -m alembic current
docker compose run --rm control-plane-migrate python -m alembic downgrade <safe-revision>

# 4. Restart with restored image
docker compose up -d control-plane webhook-worker email-worker
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
  depends_on:
    postgres:
      condition: service_healthy
    control-plane-migrate:
      condition: service_completed_successfully   # <-- fail-closed gate
    minio-init:
      condition: service_completed_successfully
```

The same `service_completed_successfully` guard applies to `webhook-worker` and `email-worker`
(they depend on `control-plane` which transitively requires migrate to succeed).

---

## Notes & limitations

- **Server-managed compose.** The production `docker-compose.yml` lives on `tr-relay-vm`
  (`/opt/relay/`) and uses `image: infra-control-plane:latest` (built locally), whereas the
  repo's `infra/docker-compose.yml` is the `build:`-context variant of the same gate. The deploy
  pipeline deliberately syncs **only** `apps/control-plane/` → `control-plane-src/` and leaves
  the server's compose file and `.env` alone.
- **web-publish / relay-server** are not (re)built by the deploy pipeline — it covers the
  control-plane and its workers only. Rebuild those manually if their source changes.
- **Firewall.** See the network note under [Automated deploy](#required-repository-secrets) if
  GitHub-hosted runners can't reach `tr-relay-vm`.
- **`infra/Caddyfile` is NOT deployed by CD or the manual steps above — sync it by hand, every
  time.** Neither the automated pipeline nor the manual upgrade recipe touches
  `/opt/relay/Caddyfile`; it's a `docker-compose.yml`-managed bind mount the deploy tooling
  deliberately leaves alone (same reasoning as the server-managed compose file, above), but
  unlike compose/`.env` there is no independent reason for it to diverge from git — it's meant to
  track `infra/Caddyfile` exactly. This has silently regressed the public `/metrics` block twice
  (task history: `c27b715a`, `77117bf7`) — most recently by surviving the 2026-07-09 Helsinki
  host migration, since a host migration copies data/config that was already on the box, not
  what's in git. **After editing `infra/Caddyfile`, or after any host migration, manually sync
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

- `CLAUDE-workflow.md §1b` — shared deploy-discipline rule (migration before code, always)
- `.github/workflows/deploy.yml` — automated deploy pipeline (gated)
- `scripts/deploy.sh` — on-server build → migrate-gate → restart logic
- `infra/docker-compose.yml` — dev/local compose template (build-context variant of same gate)
- `apps/control-plane/app/db/migrations/versions/` — Alembic migration files
- `apps/control-plane/alembic.ini` — Alembic configuration
