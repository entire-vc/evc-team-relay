#!/usr/bin/env bash
#
# Team Relay — deploy script. Covers control-plane (+ its workers) and web-publish.
#
# Enforces CLAUDE-workflow.md §1b Deploy Discipline: control-plane's migration gate
# runs and must succeed BEFORE the app container is (re)started. A failed migration
# aborts the deploy — the running app is left untouched. Fail-closed, never the
# reverse. web-publish has no migrations, so it skips straight to build+restart.
#
# Two invocation shapes, auto-detected via whether $RELAY_DIR exists locally:
#
#   1. Driver mode (local checkout, e.g. `bash scripts/deploy.sh web-publish`):
#      rsyncs apps/<component>/ -> $SSH_TARGET:$RELAY_DIR/<component>-src/, then
#      re-invokes this same script over SSH on $SSH_TARGET (default tr-relay-vm).
#   2. Direct mode (running ON the deploy host, e.g. .github/workflows/deploy.yml's
#      `ssh <host> 'bash -s' < scripts/deploy.sh`, or a human already ssh'd in):
#      assumes the relevant *-src/ dir is already synced and runs build+deploy.
#
# Usage:
#   bash scripts/deploy.sh [control-plane|web-publish|all]   # default: control-plane
#
# Env (all optional, sane prod defaults):
#   RELAY_DIR          deploy root on the server                    (default /opt/relay)
#   SSH_TARGET          ssh alias/host used in driver mode            (default tr-relay-vm)
#   IMAGE               control-plane image tag                      (default infra-control-plane:latest)
#   WEB_PUBLISH_IMAGE   web-publish image tag                        (default infra-web-publish:latest)
#   GITHUB_TOKEN         BuildKit secret for web-publish's `npm ci`   (else read from $RELAY_DIR/.env)
#   DRY_RUN             if "true": rehearsal only — see below for exactly what that means
#
# DRY_RUN=true is a genuine no-side-effects rehearsal, not "build for real but skip the
# restart": the image is always built into a throwaway `:candidate` tag first, and only
# promoted to `:latest` (with the outgoing image captured as `:prev` for rollback) on a
# REAL run. A dry run never touches `:latest` or `:prev`. For control-plane specifically,
# the migration gate in dry-run mode runs `alembic upgrade head --sql` — Alembic's offline
# mode, which renders the SQL a real upgrade would execute and validates the revision graph
# (catches a broken down_revision / multiple heads) WITHOUT connecting to the database at
# all. It does not apply anything. This matters: earlier revisions of this script ran the
# REAL `alembic upgrade head` against prod even under DRY_RUN=true, and separately let a
# dry-run build silently overwrite `:latest`, corrupting the `:prev` rollback point on the
# very next real deploy (#507d6021, incident on #d94a6aa2). Neither can happen now — dry-run
# has no write path to the database or to the `:latest`/`:prev` tags.
#
set -euo pipefail

RELAY_DIR="${RELAY_DIR:-/opt/relay}"
IMAGE="${IMAGE:-infra-control-plane:latest}"
WEB_PUBLISH_IMAGE="${WEB_PUBLISH_IMAGE:-infra-web-publish:latest}"
DRY_RUN="${DRY_RUN:-false}"
COMPONENT="${1:-${COMPONENT:-control-plane}}"

log() { printf '\n\033[1;36m==> %s\033[0m\n' "$*"; }
die() { printf '\n\033[1;31m!! %s\033[0m\n' "$*" >&2; exit 1; }

# Resolve THIS HOST's public origin for the edition smoke gate (#08e44245).
# Kept as its own small, pure function — reads a file, touches no network or
# docker — precisely so it can be tested in isolation (extracted with sed and
# sourced, see scripts/test_deploy_smoke_url.sh) without sourcing the rest of
# this script, which would trigger a real deploy via the dispatch at the
# bottom. SMOKE_URL always wins when set. Otherwise reads CORS_ALLOWED_ORIGINS
# from $1/.env — the control-plane's own public origin, already required to
# be correct per host for CORS to work at all — and takes the first origin if
# it's a comma-separated list. Prints the resolved /server/info URL and
# returns 0, or prints nothing and returns 1 if no valid http(s) origin could
# be determined (the caller must fail closed on that, not fall back to a
# guessed domain).
resolve_smoke_url() {
  local relay_dir="$1"
  if [ -n "${SMOKE_URL:-}" ]; then
    printf '%s\n' "$SMOKE_URL"
    return 0
  fi
  local _origin
  _origin="$(grep -m1 '^CORS_ALLOWED_ORIGINS=' "$relay_dir/.env" 2>/dev/null | cut -d= -f2- | cut -d, -f1)"
  case "$_origin" in
    http://*|https://*) ;;
    *) return 1 ;;
  esac
  printf '%s/server/info\n' "${_origin%/}"
}

case "$COMPONENT" in
  control-plane|web-publish|all) ;;
  *) die "unknown component '$COMPONENT' (expected control-plane|web-publish|all)" ;;
esac

# --- Driver mode ------------------------------------------------------------
# $RELAY_DIR (default /opt/relay) only exists on the deploy host itself, so its
# absence here means we're running from a local checkout, not on the server.
if [ ! -d "$RELAY_DIR" ]; then
  SSH_TARGET="${SSH_TARGET:-tr-relay-vm}"
  SCRIPT_PATH="${BASH_SOURCE[0]}"
  REPO_ROOT="$(cd "$(dirname "$SCRIPT_PATH")/.." && pwd)"

  sync_component() {
    log "syncing apps/$1/ -> $SSH_TARGET:$RELAY_DIR/$1-src/"
    rsync -az --delete -e ssh "$REPO_ROOT/apps/$1/" "$SSH_TARGET:$RELAY_DIR/$1-src/"
  }

  case "$COMPONENT" in
    control-plane) sync_component control-plane ;;
    web-publish)   sync_component web-publish ;;
    all)           sync_component control-plane; sync_component web-publish ;;
  esac

  log "invoking remote deploy on $SSH_TARGET (component=$COMPONENT)"
  exec ssh "$SSH_TARGET" \
    "RELAY_DIR='$RELAY_DIR' DRY_RUN='$DRY_RUN' bash -s -- '$COMPONENT'" \
    < "$SCRIPT_PATH"
fi

# --- Direct mode (we ARE the deploy host) ------------------------------------
cd "$RELAY_DIR" || die "deploy root $RELAY_DIR not found on this host"
[ -f docker-compose.yml ] || die "docker-compose.yml missing in $RELAY_DIR"

deploy_control_plane() {
  local image="$IMAGE"
  local candidate="${image%:*}:candidate"

  [ -d control-plane-src ] || die "control-plane-src/ missing in $RELAY_DIR — source sync did not run"

  # 1. Always build into a throwaway candidate tag — NEVER directly into "$image".
  #    This is what keeps a dry run from touching :latest at all: promotion to
  #    :latest (and capturing the outgoing image as :prev) only happens below,
  #    after the DRY_RUN check, on a real run.
  log "building $candidate from control-plane-src/"
  docker build -t "$candidate" control-plane-src/

  if [ "$DRY_RUN" = "true" ]; then
    # Offline migration check: `alembic upgrade head --sql` renders the SQL a
    # real upgrade would execute and validates the revision graph (catches a
    # broken down_revision / multiple heads) WITHOUT opening a database
    # connection — env.py's run_migrations_offline() never calls .connect().
    # Verified empirically before wiring this in: a deliberately broken
    # down_revision fails this command (KeyError, exit 1); the real graph
    # renders full SQL base->head with an unreachable DB host, exit 0. Needs
    # a Postgres-dialect DATABASE_URL to render Postgres DDL (JSONB etc.) —
    # a real one, from .env, though nothing in it is ever dialed.
    #
    # Runs through `docker compose run` against the control-plane-migrate-dry-run
    # service (infra/docker-compose.yml), NOT `docker run --env-file`: the latter
    # cannot parse a multiline value (this env has one — an ED25519 PEM key) and
    # fails with "invalid env file ... contains whitespaces" before alembic ever
    # runs, which then read as a broken migration graph — it wasn't (#efcf85d4).
    # Compose's own env_file parser handles multiline values correctly; the real
    # (online) migration gate below already goes through compose for the same
    # reason, so this now matches it instead of diverging by transport.
    log "DRY_RUN=true — offline migration check against $candidate (no DB connection, no writes)"
    if ! docker compose run --rm -T control-plane-migrate-dry-run </dev/null; then
      die "DRY_RUN migration check FAILED — the migration graph itself is broken (or the offline check's own environment is), not a real-deploy-only issue. Investigate before attempting a real deploy. :latest and :prev are untouched."
    fi
    log "DRY_RUN migration check passed (offline SQL render, zero DB connections)"
    log "DRY_RUN complete — $candidate left tagged for inspection. :latest and :prev untouched."
    return 0
  fi

  # --- Real deploy from here: promote the candidate, THEN gate against the DB ---

  # 2. Tag the current image for fast rollback (skip on first-ever deploy).
  #    Done here, not before the build, so a dry run never overwrites this.
  if docker image inspect "$image" >/dev/null 2>&1; then
    log "tagging current image -> infra-control-plane:prev (rollback point)"
    docker tag "$image" infra-control-plane:prev
  else
    log "no existing $image — first deploy, nothing to tag for rollback"
  fi

  log "promoting $candidate -> $image"
  docker tag "$candidate" "$image"

  # 3. Migration gate — FAIL-CLOSED, against the real database.
  #    docker compose run returns the migrate container's exit code; alembic failure
  #    => non-zero => we abort here and the app is NEVER restarted. The migrate
  #    service is pinned to image: infra-control-plane:latest in docker-compose.yml,
  #    so it picks up the image we just promoted above, not a stale one.
  #
  #    -T:        disable pseudo-TTY (non-interactive CI run).
  #    </dev/null: prevent docker compose run from inheriting bash's stdin.
  #               Without this, when the script is fed via 'bash -s < deploy.sh'
  #               over SSH, docker compose run consumes the rest of the file from
  #               stdin — bash hits EOF and exits before 'compose up' ever runs.
  log "running migration gate (alembic upgrade head)"
  if ! docker compose run --rm -T control-plane-migrate </dev/null; then
    die "MIGRATION GATE FAILED — app NOT restarted, prod left on previous image. Investigate before retrying."
  fi
  log "migration gate passed (schema at head)"

  # 4. Restart app services — only reachable when migrate exited 0.
  log "restarting app services"
  docker compose up -d --force-recreate control-plane webhook-worker email-worker listmonk-sync-worker lifecycle-worker

  # 5. Post-deploy health check (best-effort, non-fatal — restart already issued).
  log "waiting for control-plane health"
  for i in $(seq 1 20); do
    status="$(docker compose ps --format '{{.Health}}' control-plane 2>/dev/null || true)"
    case "$status" in
      healthy) log "control-plane healthy"; break ;;
      *) sleep 3 ;;
    esac
    if [ "$i" -eq 20 ]; then
      printf '\n\033[1;33m## control-plane not reporting healthy after ~60s — check `docker compose logs control-plane`\033[0m\n' >&2
    fi
  done

  # 6. Edition smoke gate — FAIL-CLOSED.
  #    Hits /server/info after a successful health-check to verify the running image
  #    is the enterprise build (edition=enterprise + billing_enabled=true).
  #    If not, rolls back to the :prev image automatically.
  #    smoke_url is per-host, never hardcoded — see resolve_smoke_url() above
  #    (#08e44245: a hardcoded default meant a deploy on the newer tr-ru-vm
  #    host silently smoke-tested tr-relay-vm's prod instance instead).
  local smoke_url
  smoke_url="$(resolve_smoke_url "$RELAY_DIR")" || die "Cannot determine this host's public origin for the smoke gate — CORS_ALLOWED_ORIGINS not found (or not a URL) in $RELAY_DIR/.env. Refusing to smoke-test a guessed domain; set SMOKE_URL explicitly or fix .env."
  log "checking edition smoke gate ($smoke_url)"
  local _edition="" _billing="False" _resp
  for _i in $(seq 1 5); do
    _resp=$(curl -sf --max-time 10 "$smoke_url" 2>/dev/null || true)
    if [ -n "$_resp" ]; then
      _edition=$(printf '%s' "$_resp" | python3 -c "import json,sys; print(json.load(sys.stdin).get('edition',''))" 2>/dev/null || true)
      _billing=$(printf '%s' "$_resp" | python3 -c "import json,sys; d=json.load(sys.stdin); print(str(d.get('features',{}).get('billing_enabled','False')))" 2>/dev/null || true)
      break
    fi
    sleep 3
  done
  if [ "$_edition" != "enterprise" ] || [ "$_billing" != "True" ]; then
    printf '\n\033[1;31m!! EDITION SMOKE GATE FAILED: edition=%s billing_enabled=%s\033[0m\n' "$_edition" "$_billing" >&2
    printf '\033[1;31m!! Rolling back to infra-control-plane:prev\033[0m\n' >&2
    docker tag infra-control-plane:prev "$image" 2>/dev/null || true
    docker compose up -d --force-recreate control-plane webhook-worker email-worker listmonk-sync-worker lifecycle-worker 2>/dev/null || true
    die "Edition smoke gate failed — rolled back to prev image. Prod /server/info must return edition=enterprise + billing_enabled=true. This repo's main IS the enterprise source (settled in TR·edition/billing #f75f04bb, byte-diffed against /opt/relay/control-plane-src), so a community reading here means the built image or its runtime env drifted — check the build args and $RELAY_DIR/.env, not which repo the source came from."
  fi
  log "smoke gate PASSED: edition=enterprise billing_enabled=true"

  log "control-plane deploy complete — image $image live, prev image retained as infra-control-plane:prev"
}

deploy_web_publish() {
  local image="$WEB_PUBLISH_IMAGE"
  local candidate="${image%:*}:candidate"

  [ -d web-publish-src ] || die "web-publish-src/ missing in $RELAY_DIR — source sync did not run"

  # 1. Build into a throwaway candidate tag — NEVER directly into "$image". This
  #    is what keeps a dry run from touching :latest/:prev; see deploy_control_plane
  #    for why (#507d6021, an earlier dry-run-then-real-run sequence corrupted
  #    :prev because the build overwrote :latest regardless of DRY_RUN).
  #    The Dockerfile's `npm ci` reads GITHUB_TOKEN via a BuildKit secret mount
  #    (never baked into a layer) — pull it from the environment, falling back
  #    to the server's own $RELAY_DIR/.env so this works unattended too.
  local github_token="${GITHUB_TOKEN:-}"
  if [ -z "$github_token" ] && [ -f "$RELAY_DIR/.env" ]; then
    github_token="$(grep -m1 '^GITHUB_TOKEN=' "$RELAY_DIR/.env" | cut -d= -f2-)"
  fi
  [ -n "$github_token" ] || die "GITHUB_TOKEN not set and not found in $RELAY_DIR/.env — required as the npm-ci build secret"

  log "building $candidate from web-publish-src/"
  GITHUB_TOKEN="$github_token" docker build --secret id=github_token,env=GITHUB_TOKEN -t "$candidate" web-publish-src/

  # web-publish has no migrations — no fail-closed gate needed here. Do not add
  # one; control-plane's gate above is the only migration-bearing component.

  if [ "$DRY_RUN" = "true" ]; then
    log "DRY_RUN complete — $candidate built and left tagged for inspection. :latest and :prev untouched."
    return 0
  fi

  # --- Real deploy from here: promote the candidate, then restart ---

  # 2. Tag the current image for fast rollback (skip on first-ever deploy).
  #    Done here, not before the build, so a dry run never overwrites this.
  if docker image inspect "$image" >/dev/null 2>&1; then
    log "tagging current image -> infra-web-publish:prev (rollback point)"
    docker tag "$image" infra-web-publish:prev
  else
    log "no existing $image — first deploy, nothing to tag for rollback"
  fi

  log "promoting $candidate -> $image"
  docker tag "$candidate" "$image"

  # 3. Restart — no migration gate to wait on.
  log "restarting web-publish"
  docker compose up -d --force-recreate web-publish

  # 4. Post-deploy health check (best-effort, non-fatal — restart already issued).
  log "waiting for web-publish health"
  for i in $(seq 1 20); do
    status="$(docker compose ps --format '{{.Health}}' web-publish 2>/dev/null || true)"
    case "$status" in
      healthy) log "web-publish healthy"; break ;;
      *) sleep 3 ;;
    esac
    if [ "$i" -eq 20 ]; then
      printf '\n\033[1;33m## web-publish not reporting healthy after ~60s — check `docker compose logs web-publish`\033[0m\n' >&2
    fi
  done

  log "web-publish deploy complete — image $image live, prev image retained as infra-web-publish:prev"
}

case "$COMPONENT" in
  control-plane) deploy_control_plane ;;
  web-publish)   deploy_web_publish ;;
  all)           deploy_control_plane; deploy_web_publish ;;
esac
