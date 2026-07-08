#!/bin/bash
#
# backup-age-alert.sh — alert via Telegram if latest backup is older than MAX_AGE_HOURS.
#
# Run from the Docker host via cron (not inside the backup container).
# Example crontab entry:
#   0 * * * * /opt/relay/backup-age-alert.sh >> /var/log/backup-age-alert.log 2>&1
#
# Required env vars (or provide as arguments / in a sourced .env):
#   BACKUP_DIR       - backup directory (default: /opt/relay/data/backups)
#   POSTGRES_DB      - DB name for latest symlink (default: relay)
#   MAX_AGE_HOURS    - alert threshold in hours (default: 26)
#   TG_BOT_TOKEN     - Telegram bot token
#   TG_CHAT_ID       - Telegram chat ID to send alert to
#   TG_ENABLED       - set to "true" to send alerts (default: false)

set -euo pipefail

BACKUP_DIR="${BACKUP_DIR:-/opt/relay/data/backups}"
POSTGRES_DB="${POSTGRES_DB:-relay}"
MAX_AGE_HOURS="${MAX_AGE_HOURS:-26}"
TG_ENABLED="${TG_ENABLED:-false}"

log() { echo "[$(date -Iseconds)] $*"; }

# Find latest backup — prefer symlink, fall back to newest file
BACKUP_FILE="${BACKUP_DIR}/${POSTGRES_DB}_latest.sql.gz"
if [ -L "${BACKUP_FILE}" ]; then
    REAL_FILE="$(readlink -f "${BACKUP_FILE}")"
    MTIME=$(stat -c%Y "${REAL_FILE}" 2>/dev/null || stat -f%m "${REAL_FILE}")
else
    REAL_FILE=$(find "${BACKUP_DIR}" -name "${POSTGRES_DB}_*.sql.gz" -type f \
        2>/dev/null | sort -r | head -1)
    [ -z "${REAL_FILE}" ] && { log "ERROR: no backup found in ${BACKUP_DIR}"; exit 1; }
    MTIME=$(stat -c%Y "${REAL_FILE}" 2>/dev/null || stat -f%m "${REAL_FILE}")
fi

NOW=$(date +%s)
AGE_SECS=$(( NOW - MTIME ))
AGE_HOURS=$(( AGE_SECS / 3600 ))

log "Latest backup: ${REAL_FILE}"
log "Age: ${AGE_HOURS}h (threshold: ${MAX_AGE_HOURS}h)"

if [ "${AGE_HOURS}" -le "${MAX_AGE_HOURS}" ]; then
    log "OK — backup is fresh"
    exit 0
fi

# Backup is stale
AGE_HUMAN="${AGE_HOURS}h $((( AGE_SECS % 3600 ) / 60))m"
MSG="🚨 *Team Relay backup stale* — last dump is \`${AGE_HUMAN}\` old (threshold: ${MAX_AGE_HOURS}h).
File: \`${REAL_FILE}\`
Host: \`$(hostname -f 2>/dev/null || hostname)\`"

log "ALERT: backup stale (${AGE_HUMAN} > ${MAX_AGE_HOURS}h)"

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

exit 2  # non-zero so cron mailer can catch it if configured
