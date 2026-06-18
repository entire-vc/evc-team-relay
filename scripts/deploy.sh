#!/usr/bin/env bash
#
# Team Relay — remote deploy script (runs ON tw-relay)
#
# Enforces CLAUDE-workflow.md §1b Deploy Discipline: the migration gate runs and
# must succeed BEFORE any app container is (re)started. A failed migration aborts
# the deploy — the running app is left untouched. Fail-closed, never the reverse.
#
# Invoked by .github/workflows/deploy.yml as:  ssh <host> 'bash -s' < scripts/deploy.sh
# after the workflow has rsynced apps/control-plane/ -> $RELAY_DIR/control-plane-src/.
#
# Env (all optional, sane prod defaults):
#   RELAY_DIR   deploy root on the server                (default /opt/relay)
#   IMAGE       local image tag built from the source    (default infra-control-plane:latest)
#   DRY_RUN     if "true", run the migrate gate but DO NOT restart the app (rehearsal)
#
set -euo pipefail

RELAY_DIR="${RELAY_DIR:-/opt/relay}"
IMAGE="${IMAGE:-infra-control-plane:latest}"
DRY_RUN="${DRY_RUN:-false}"

log() { printf '\n\033[1;36m==> %s\033[0m\n' "$*"; }
die() { printf '\n\033[1;31m!! %s\033[0m\n' "$*" >&2; exit 1; }

cd "$RELAY_DIR" || die "deploy root $RELAY_DIR not found on this host"
[ -d control-plane-src ] || die "control-plane-src/ missing in $RELAY_DIR — source sync did not run"
[ -f docker-compose.yml ] || die "docker-compose.yml missing in $RELAY_DIR"

# 1. Tag the current image for fast rollback (skip on first-ever deploy).
if docker image inspect "$IMAGE" >/dev/null 2>&1; then
  log "tagging current image -> infra-control-plane:prev (rollback point)"
  docker tag "$IMAGE" infra-control-plane:prev
else
  log "no existing $IMAGE — first deploy, nothing to tag for rollback"
fi

# 2. Build the new image from the freshly-synced source.
log "building $IMAGE from control-plane-src/"
docker build -t "$IMAGE" control-plane-src/

# 3. Migration gate — FAIL-CLOSED.
#    docker compose run returns the migrate container's exit code; alembic failure
#    => non-zero => we abort here and the app is NEVER restarted.
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
if [ "$DRY_RUN" = "true" ]; then
  log "DRY_RUN=true — gate passed, skipping 'compose up -d' (rehearsal only)"
  exit 0
fi

log "restarting app services"
docker compose up -d --force-recreate control-plane webhook-worker email-worker

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

log "deploy complete — image $IMAGE live, prev image retained as infra-control-plane:prev"
