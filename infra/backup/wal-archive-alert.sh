#!/bin/bash
#
# wal-archive-alert.sh — alert via Telegram if PostgreSQL WAL archiving is
# failing without making progress (failed_count growing, archived_count not).
#
# `archive_mode=on` + a correctly-looking `archive_command` in postgresql.conf
# do NOT prove archiving works — the archiver can fail silently every attempt
# (e.g. the wal_archive bind-mount getting dropped on a container recreate
# that didn't pick up docker-compose.override.yml). This checks the actual
# pg_stat_archiver counters, not the config.
#
# Run from the Docker host via cron:
#   */15 * * * * /opt/relay/wal-archive-alert.sh >> /var/log/wal-archive-alert.log 2>&1
#
# Required env vars (sourced from backup-alert.env, same as backup-age-alert.sh):
#   POSTGRES_CONTAINER   - postgres container name (default: relay-postgres-1)
#   POSTGRES_USER        - db user (default: relay)
#   POSTGRES_DB          - db name (default: relay)
#   STATE_FILE           - where to persist the previous counters (default: /opt/relay/data/.wal-archive-alert-state)
#   TG_BOT_TOKEN / TG_CHAT_ID / TG_ENABLED - same Telegram config as other alerts

set -euo pipefail

POSTGRES_CONTAINER="${POSTGRES_CONTAINER:-relay-postgres-1}"
POSTGRES_USER="${POSTGRES_USER:-relay}"
POSTGRES_DB="${POSTGRES_DB:-relay}"
STATE_FILE="${STATE_FILE:-/opt/relay/data/.wal-archive-alert-state}"
TG_ENABLED="${TG_ENABLED:-false}"

log() { echo "[$(date -Iseconds)] $*"; }

STATS=$(docker exec "${POSTGRES_CONTAINER}" psql -U "${POSTGRES_USER}" -d "${POSTGRES_DB}" -tAc \
    "SELECT archived_count, failed_count FROM pg_stat_archiver;")
ARCHIVED=$(echo "${STATS}" | cut -d'|' -f1)
FAILED=$(echo "${STATS}" | cut -d'|' -f2)

log "Current: archived_count=${ARCHIVED} failed_count=${FAILED}"

if [ ! -f "${STATE_FILE}" ]; then
    log "No prior state — writing baseline, nothing to compare yet"
    echo "${ARCHIVED} ${FAILED}" > "${STATE_FILE}"
    exit 0
fi

read -r PREV_ARCHIVED PREV_FAILED < "${STATE_FILE}"
echo "${ARCHIVED} ${FAILED}" > "${STATE_FILE}"

DELTA_ARCHIVED=$(( ARCHIVED - PREV_ARCHIVED ))
DELTA_FAILED=$(( FAILED - PREV_FAILED ))

log "Delta since last check: archived+${DELTA_ARCHIVED} failed+${DELTA_FAILED}"

if [ "${DELTA_FAILED}" -gt 0 ] && [ "${DELTA_ARCHIVED}" -le 0 ]; then
    MSG="🚨 *Team Relay WAL archiving broken* — \`${DELTA_FAILED}\` new archive failures since last check, \`0\` successes.
archived_count=${ARCHIVED} (prev ${PREV_ARCHIVED}), failed_count=${FAILED} (prev ${PREV_FAILED})
Likely cause: wal_archive bind-mount missing from the running container after a recreate that didn't pick up docker-compose.override.yml.
Fix: \`cd /opt/relay && docker compose up -d postgres\`, then verify \`docker inspect ${POSTGRES_CONTAINER}\` shows the wal_archive mount.
Host: \`$(hostname -f 2>/dev/null || hostname)\`"

    log "ALERT: WAL archiving failing without progress"

    if [ "${TG_ENABLED}" = "true" ]; then
        [ -z "${TG_BOT_TOKEN:-}" ] && { log "ERROR: TG_BOT_TOKEN not set"; exit 1; }
        [ -z "${TG_CHAT_ID:-}" ]   && { log "ERROR: TG_CHAT_ID not set"; exit 1; }
        curl -fsSL -X POST \
            "https://api.telegram.org/bot${TG_BOT_TOKEN}/sendMessage" \
            -d "chat_id=${TG_CHAT_ID}" \
            -d "parse_mode=Markdown" \
            --data-urlencode "text=${MSG}" \
            >/dev/null
        log "TG alert sent to chat ${TG_CHAT_ID}"
    else
        log "TG_ENABLED != true — alert printed to log only"
        echo "ALERT: ${MSG}"
    fi
    exit 2
fi

log "OK — WAL archiving making progress or idle (no failures)"
exit 0
