#!/bin/bash
# Check that off-site MinIO mirror is fresh (< MAX_AGE_HOURS)
# Runs hourly from cron via: /opt/relay/backup-offsite-alert.sh >> /var/log/backup-offsite-alert.log 2>&1
# Source backup-alert.env for TG creds.
#
# Checks (in order):
#   1. pg-dump off-site freshness — object-based (pg-dump always creates a new file, so object age = run age)
#   2. mc mirror freshness — sentinel-based (/opt/relay/data/backups/.last-offsite-mirror written by
#      minio-mirror.sh on success). Incremental mirror has no new objects when docs are unchanged,
#      so object age is NOT a reliable indicator of whether mirror ran.

set -euo pipefail

MAX_AGE_HOURS=26
CONTAINER=relay-postgres-backup-1
BUCKET=relay-backup
PREFIX=minio/
SENTINEL=/opt/relay/data/backups/.last-offsite-mirror

. /opt/relay/backup-alert.env 2>/dev/null || true
TG_ENABLED="${TG_ENABLED:-false}"

log() { echo "[$(date -Iseconds)] $*"; }

tg_send() {
    local msg="$1"
    if [ "${TG_ENABLED}" = "true" ]; then
        curl -fsSL -X POST "https://api.telegram.org/bot${TG_BOT_TOKEN}/sendMessage" \
            -d "chat_id=${TG_CHAT_ID}" -d "parse_mode=Markdown" \
            --data-urlencode "text=${msg}" >/dev/null
    fi
}

NOW=$(date +%s)

# ── 1. pg-dump freshness (object-based — correct: each dump creates a new file) ──────────────────
PG_LINE=$(docker exec "$CONTAINER" mc ls "offsite/${BUCKET}/postgres/" 2>/dev/null | sort | tail -1)
if [ -z "$PG_LINE" ]; then
    log "ERROR: off-site postgres/ folder is empty — pg-dump never uploaded off-site"
    tg_send "🚨 *TR off-site pg-dump MISSING* — no pg-dump in offsite/${BUCKET}/postgres/. Check backup-pg-offsite.sh."
    exit 2
fi
PG_MTIME_STR=$(echo "$PG_LINE" | awk '{gsub(/^\[/,"",$1); print $1, $2}')
PG_MTIME=$(date -d "${PG_MTIME_STR} UTC" +%s 2>/dev/null || echo 0)
PG_AGE_HOURS=$(( (NOW - PG_MTIME) / 3600 ))
log "Off-site pg-dump age: ${PG_AGE_HOURS}h (threshold: ${MAX_AGE_HOURS}h, file: ${PG_MTIME_STR} UTC)"
if [ "${PG_AGE_HOURS}" -gt "${MAX_AGE_HOURS}" ]; then
    log "ALERT: pg-dump off-site stale (${PG_AGE_HOURS}h > ${MAX_AGE_HOURS}h)"
    tg_send "🚨 *TR off-site pg-dump STALE* — last dump ${PG_MTIME_STR} UTC (${PG_AGE_HOURS}h ago). Check backup-pg-offsite.sh cron."
    exit 2
fi

# ── 2. mc mirror freshness (sentinel-based — NOT object-age) ─────────────────────────────────────
# minio-mirror.sh writes date +%s to SENTINEL after every successful mirror run.
# If no documents changed, mc mirror has nothing new to upload (incremental), so
# newest-object timestamp is stale even though mirror worked fine.
if [ -f "${SENTINEL}" ]; then
    MIRROR_MTIME=$(cat "${SENTINEL}" 2>/dev/null || echo 0)
    MIRROR_AGE_HOURS=$(( (NOW - MIRROR_MTIME) / 3600 ))
    log "Last successful mirror run: ${MIRROR_AGE_HOURS}h ago (sentinel: ${SENTINEL})"
    if [ "${MIRROR_AGE_HOURS}" -le "${MAX_AGE_HOURS}" ]; then
        log "OK — off-site mirror is fresh (sentinel ${MIRROR_AGE_HOURS}h old)"
        exit 0
    fi
    log "ALERT: off-site mirror stale — last run ${MIRROR_AGE_HOURS}h ago (> ${MAX_AGE_HOURS}h)"
    tg_send "🚨 *TR off-site mirror STALE* — last successful run ${MIRROR_AGE_HOURS}h ago (threshold ${MAX_AGE_HOURS}h). Check minio-mirror.sh / backup container logs."
    exit 2
else
    # Sentinel doesn't exist yet (first run after deploy, or container recreated).
    # Fall back to object-age check with a warning. This transition path is temporary —
    # next successful mirror run will create the sentinel and we'll use that going forward.
    log "WARN: sentinel ${SENTINEL} not found — falling back to object-age check (one-time, post-deploy)"
    LAST_LINE=$(docker exec "$CONTAINER" mc ls "offsite/${BUCKET}/${PREFIX}" --recursive 2>/dev/null | sort | tail -1)
    if [ -z "$LAST_LINE" ]; then
        log "ERROR: off-site bucket ${BUCKET}/${PREFIX} is empty or unreachable"
        tg_send "🚨 *TR off-site backup EMPTY* — bucket ${BUCKET}/${PREFIX} returned no objects. Check mc mirror."
        exit 2
    fi
    MTIME_STR=$(echo "$LAST_LINE" | awk '{gsub(/^\[/,"",$1); print $1, $2}')
    MTIME=$(date -d "${MTIME_STR} UTC" +%s 2>/dev/null || echo 0)
    AGE_HOURS=$(( (NOW - MTIME) / 3600 ))
    log "Off-site last object (fallback): ${MTIME_STR} UTC — age: ${AGE_HOURS}h (threshold: ${MAX_AGE_HOURS}h)"
    if [ "${AGE_HOURS}" -le "${MAX_AGE_HOURS}" ]; then
        log "OK — off-site mirror appears fresh (object-age fallback)"
        exit 0
    fi
    log "ALERT: off-site mirror stale by object-age fallback (${AGE_HOURS}h > ${MAX_AGE_HOURS}h)"
    tg_send "🚨 *TR off-site mirror STALE* — last object ${MTIME_STR} UTC (${AGE_HOURS}h ago, threshold ${MAX_AGE_HOURS}h). Mirror may have failed silently."
    exit 2
fi
