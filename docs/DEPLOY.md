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
`alembic upgrade head` and must exit 0 before `control-plane` (or any worker) starts. `web-publish`
has no migrations, so its deploy skips straight to build + restart.

Three ways to deploy:
- **[Automated (GitLab CI)](#automated-deploy-gitlab-ci)** — control-plane only. Since the
  2026-08-23 cutover this runs from the `entire-vc/deploy` project on `git.entire.host`, as the
  **manual** job `deploy:team-relay`. It is deliberately not push-triggered: a human starts it.
- **[`scripts/deploy.sh` from a local checkout](#scriptsdeploysh-local-driver)** — still the path
  for **web-publish** (which CD does not cover), and the fallback for control-plane when Actions
  is unavailable. Run it from your machine; it rsyncs the source and runs the same
  build/gate/restart logic on `tr-relay-vm` over SSH.
- **[Manual (SSH)](#manual-upgrade-fallback)** — the emergency fallback when you're already on the
  box, or want to run the steps by hand.

---

## Automated deploy (GitLab CI)

Pipeline: `entire-vc/deploy` → `.gitlab-ci.yml`, job `deploy:team-relay`.
On-server logic: [`scripts/deploy.sh`](../scripts/deploy.sh) — unchanged by the cutover, and still
the single source of the build/gate/restart sequence.

**Trigger** — manual only. Start a pipeline on `main` in `entire-vc/deploy` and play
`deploy:team-relay`; `verify:team-relay` follows automatically on success. `DRY_RUN` defaults to
`false` there. The job clones this repo at `${PRODUCT_REF:-main}` rather than keeping a copy of
the source in the deploy project.

> The old GitHub Actions workflow (`.github/workflows/deploy.yml`) was push-triggered on
> `apps/control-plane/**` and `scripts/deploy.sh`. It no longer deploys; GitHub is the public
> showcase mirror. The exclusion of `.github/**` from that filter carried over to the GitLab job
> for the same reason it existed: a CI-plumbing edit is not a reason to restart production, and a
> pipeline listing its own path would redeploy prod the instant it merged.

**What it does** (one job, `environment: production`):
1. Writes the deploy key + pinned host keys and builds an SSH config that reaches
   `tr-relay-vm` via `ProxyJump` through `ghdeploy@hel01`, then proves connectivity with a
   cheap `hostname` call before touching anything.
2. `rsync -az --delete apps/control-plane/ → tr-relay-vm:/opt/relay/control-plane-src/`
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

**Rehearsing the gate without restarting the app — currently broken, do not rely on it.**
Setting `DRY_RUN=true` is meant to build the image and run an offline migration check, then stop
before `compose up`. It cannot run today: the dry-run branch of `scripts/deploy.sh` starts the
candidate with `docker run --env-file /opt/relay/.env`, and `--env-file` cannot parse the
multi-line PEM that file contains — it fails with `variable '-----END PRIVATE KEY-----"' contains
whitespaces` before alembic is ever reached. The real path is unaffected: it goes through
`docker compose run --rm -T control-plane-migrate`, and compose reads the same file correctly.
Measured 2026-08-23; the rehearsal appears never to have been executed, since CI never set
`DRY_RUN=true`. Fix tracked separately — the rehearsal must start the candidate the same way the
real path does (compose), not via `--env-file`.

The **real** deploy keeps its own fail-closed gate regardless: migrations run through compose
before `up`, and their failure cancels the deploy.

### Required repository secrets (historical — the retired GitHub Actions path)

> Kept for the record and because the lessons below still apply to the GitHub workflows this
> repository *does* still run (`ci.yml`, `trivy.yml`, `semgrep.yml`). These secrets no longer
> deploy anything. The GitLab job authenticates with `DEPLOY_SSH_KEY`, a **file**-type CI
> variable, so the variable holds a path and `env`/`printenv` never prints the key; the runner
> sits inside the Helsinki network and reaches the relay host directly, with no `ghdeploy@hel01`
> ProxyJump hop at all.

Set these under **Settings → Secrets and variables → Actions** (or scoped to the `production`
environment, which also lets you add a manual-approval protection rule):

Both are set as of 2026-07-26. The workflow fails closed on its first step if either is missing
or empty.

| Secret | Required | Description |
|--------|----------|-------------|
| `TW_RELAY_SSH_KEY_B64` | ✅ | Private half of the dedicated `ghdeploy-team-relay@hel01-20260726` ed25519 deploy key, **base64-encoded**. Public half is in `tr-relay-vm:~/.ssh/authorized_keys` **and** in `hel01:/home/ghdeploy/.ssh/authorized_keys` (restricted, see network note). |
| `TW_RELAY_KNOWN_HOSTS_B64` | ✅ | Pinned host keys for **both** hops (hel01 and `10.10.10.40`), **base64-encoded**. There is no TOFU fallback — an unknown or changed host key fails the deploy. |

Only those two. Everything else the job needs — `TARGET_HOST`, `TARGET_USER`, `TARGET_PORT`,
`PROXY_HOST`, `PROXY_USER`, `RELAY_DIR` — lives in plain `env:` at the top of the job, matching
evc-spark's `DEPLOY_HOST`/`DEPLOY_USER`/`DEPLOY_PATH`.

> **Why they are not secrets.** None of them are sensitive: the target IP is named in this very
> document, in the workflow's own header comment, and in the fleet CLAUDE.md. Storing them as
> secrets was actively harmful — GitHub masks every secret as `***` in run logs, so when
> `TW_RELAY_HOST` turned out to hold two stray characters (13 bytes for an 11-byte address), the
> resulting `no pinned host key` failure was impossible to diagnose from the run output. Plain
> `env:` keeps the logs readable and puts the value under code review.

> ⚠️ **Why both are base64.** A raw multi-line value does not survive `gh secret set` intact.
> Measured on this repo during the 2026-07-26 bring-up: a 12-line `known_hosts` arrived at the
> runner as its **last line only** (94 bytes, 0 newlines), leaving the ProxyJump hop entirely
> unpinned; and the 432-byte / 8-line private key arrived as **418 bytes / 7 lines**, which
> presents as a bare `Permission denied (publickey)`. Base64 is a single line, so there is
> nothing to truncate — verified byte-identical on round-trip.
>
> Regenerate them with:
>
> ```bash
> # known_hosts (both hops)
> { ssh-keyscan -t rsa,ecdsa,ed25519 66.151.34.194
>   ssh hel01 'ssh-keyscan -t rsa,ecdsa,ed25519 10.10.10.40'
> } | base64 | tr -d '\n' | gh secret set TW_RELAY_KNOWN_HOSTS_B64 -R entire-vc/evc-team-relay
>
> # private key
> base64 < /path/to/deploy_key | tr -d '\n' \
>   | gh secret set TW_RELAY_SSH_KEY_B64 -R entire-vc/evc-team-relay
> ```
>
> **The workflow does not trust either value.** After decoding it asserts that both SSH hops are
> actually pinned (`ssh-keygen -F`) and that the private key's fingerprint matches
> `DEPLOY_KEY_FINGERPRINT`, pinned in plain `env:`. If you rotate the key, update that
> fingerprint in the same commit — otherwise the deploy fails closed, by design, naming the
> mismatch rather than dying as an anonymous auth error.

> **Network note.** `tr-relay-vm` (`10.10.10.40`) has no public address; it sits on the private
> Helsinki network (`vmbr1`, `10.10.10.0/24`) behind hel01 (`66.151.34.194`). The workflow reaches
> it from a stock `ubuntu-latest` runner via `ProxyJump` through hel01's `ghdeploy` account — the
> same pattern evc-mesh (`.10`), evc-spark (`.30`), evc-argus (`.60`), contenthub (`.70`), tgbot
> (`.80`) and sites (`.100`) already deploy with.
>
> `ghdeploy` is not a shell account (`/usr/sbin/nologin`), and this repo's key is pinned in its
> `authorized_keys` as:
>
> ```
> command="/bin/false",restrict,port-forwarding,permitopen="10.10.10.40:22" ssh-ed25519 AAAA... ghdeploy-team-relay@hel01-20260726
> ```
>
> so the credential can do exactly one thing: open a TCP forward to port 22 of this product's own
> VM. Verified 2026-07-26 — a shell attempt returns *"This account is currently not available"*,
> and forwarding to a sibling VM (`.20` billing, `.30` spark) is refused with
> *"administratively prohibited"*.
>
> ⚠️ **Do not move this job to a self-hosted runner.** This repository is public and forkable, and
> `ci.yml`/`trivy.yml` trigger on `pull_request`. GitHub runs a fork PR's workflow file *from the
> PR branch*, so a self-hosted runner registered here could be hijacked by a fork PR that
> re-points `runs-on` at it — onto a machine holding a production SSH key. Secrets are never
> exposed to fork-PR workflows, which is precisely why the GitHub-hosted + secret design is the
> safe one here.

---

## `scripts/deploy.sh` (local driver)

CD covers **control-plane** only, so this remains the way to ship **web-publish**, and the
fallback for control-plane when Actions is unavailable. Run it from a local checkout; it has
access to `tr-relay-vm` via the same
`ProxyJump hel01` alias your `~/.ssh/config` already uses for manual SSH.

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
`SSH_TARGET` overrides the remote host (default `tr-relay-vm`) if you're ever deploying elsewhere.

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
already moved to `50.0.0` (task `#148cdf9e`). Every `up -d --force-recreate` looked successful —
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
- **Worker image consolidation (2026-08-06, task `#148cdf9e`).** Before this date `webhook-worker`,
  `email-worker`, `listmonk-sync-worker`, and `lifecycle-worker` each had their own `image:` tag
  (`infra-webhook-worker:latest` etc.), built once by hand and never touched again by
  `scripts/deploy.sh` — 12 days of dependency drift went undetected because `--force-recreate`
  makes a stale container look freshly deployed. All four now share `infra-control-plane:latest`
  with `control-plane`; the old per-worker tags are left on the host (unused, not deleted) as a
  rollback path. The old tags will accumulate as dead weight — safe to `docker rmi` them after a
  few successful deploys confirm the new tag is stable.
- **web-publish** is covered by `scripts/deploy.sh web-publish` (see above) — merges to
  `apps/web-publish/` do NOT deploy themselves; someone has to run the script. This was a real gap
  (task `c3f38d9a`): three merged fixes sat undeployed for up to 3 days because nothing rebuilt the
  container. **relay-server** (the Rust Yjs relay, separate repo `evc-relay-server`) is still not
  covered by anything here — rebuild it manually if its source changes.
- **`infra/Caddyfile` is host-managed and NOT synced by the deploy pipeline** (same gap as the
  compose file above). A fix applied here must ALSO be applied live on `tr-relay-vm`
  (`/opt/relay/Caddyfile`, content-preserving write + `caddy validate` + `caddy reload` inside
  `relay-caddy-1` — it's a bind-mounted single file, don't `sed -i` it, write a fresh copy so the
  inode is preserved) or it silently only exists in git. Confirmed drifted at least once already
  (TR-47's `/metrics` block, TR-43's WS-token-stripping fix) — always diff live vs repo before
  assuming they match.
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
- `entire-vc/deploy` → `.gitlab-ci.yml`, jobs `deploy:team-relay` / `verify:team-relay` — the
  automated deploy pipeline (control-plane only, manual start)
- `scripts/deploy.sh` — rsync (driver mode) → build → migrate-gate (control-plane only) → restart,
  for both control-plane and web-publish
- `infra/docker-compose.yml` — dev/local compose template (build-context variant of same gate)
- `apps/control-plane/app/db/migrations/versions/` — Alembic migration files
- `apps/control-plane/alembic.ini` — Alembic configuration
