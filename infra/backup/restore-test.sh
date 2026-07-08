#!/bin/bash
#
# restore-test.sh — spin up a temp postgres container, restore the latest dump,
# verify table count > 0, then destroy the container.
#
# Designed to run on the Docker host (not inside the backup container) so it
# can pull images and manage containers. Can also be invoked in CI.
#
# Required env vars (or inherit from .env):
#   BACKUP_DIR      - directory with *.sql.gz dumps (default: /opt/relay/data/backups)
#   POSTGRES_DB     - database name to restore into (default: relay)
#   POSTGRES_USER   - postgres superuser (default: postgres)
#   POSTGRES_PASSWORD - postgres password for the temp container

set -euo pipefail

BACKUP_DIR="${BACKUP_DIR:-/opt/relay/data/backups}"
POSTGRES_DB="${POSTGRES_DB:-relay}"
POSTGRES_USER="${POSTGRES_USER:-postgres}"
POSTGRES_PASSWORD="${POSTGRES_PASSWORD:-restore_test_$(date +%s)}"
CONTAINER_NAME="relay-restore-test-$$"
PG_IMAGE="postgres:16-alpine"

log()  { echo "[$(date -Iseconds)] $*"; }
pass() { echo "[$(date -Iseconds)] ✓ $*"; }
fail() { echo "[$(date -Iseconds)] ✗ $*" >&2; exit 1; }

cleanup() {
    log "Cleaning up test container ${CONTAINER_NAME}"
    docker rm -f "${CONTAINER_NAME}" >/dev/null 2>&1 || true
}
trap cleanup EXIT

# Find latest dump
BACKUP_FILE="${BACKUP_DIR}/${POSTGRES_DB}_latest.sql.gz"
if [ ! -e "${BACKUP_FILE}" ]; then
    # Fallback: newest matching file
    BACKUP_FILE=$(find "${BACKUP_DIR}" -name "${POSTGRES_DB}_*.sql.gz" -type f \
        | sort -r | head -1)
fi
[ -z "${BACKUP_FILE}" ] && fail "No backup found in ${BACKUP_DIR}"
[ -f "${BACKUP_FILE}" ] || BACKUP_FILE="$(readlink -f "${BACKUP_FILE}")"
log "Using backup: ${BACKUP_FILE}"

# Verify gzip integrity
gzip -t "${BACKUP_FILE}" || fail "Backup file is corrupt: ${BACKUP_FILE}"
pass "gzip integrity OK"

# Start temp postgres container
log "Starting temp container ${CONTAINER_NAME}"
docker run -d --name "${CONTAINER_NAME}" \
    -e POSTGRES_USER="${POSTGRES_USER}" \
    -e POSTGRES_PASSWORD="${POSTGRES_PASSWORD}" \
    -e POSTGRES_DB="${POSTGRES_DB}" \
    "${PG_IMAGE}" >/dev/null

# Wait for postgres to be ready
for i in $(seq 1 20); do
    if docker exec "${CONTAINER_NAME}" pg_isready -U "${POSTGRES_USER}" -q 2>/dev/null; then
        break
    fi
    sleep 2
    [ "$i" -eq 20 ] && fail "Postgres did not start in ${CONTAINER_NAME}"
done
pass "Temp postgres ready"

# Restore dump
log "Restoring ${BACKUP_FILE} into ${CONTAINER_NAME}/${POSTGRES_DB}"
gunzip -c "${BACKUP_FILE}" | docker exec -i "${CONTAINER_NAME}" \
    psql -U "${POSTGRES_USER}" -d "${POSTGRES_DB}" -q
pass "Restore complete"

# Verify: table count > 0
TABLE_COUNT=$(docker exec "${CONTAINER_NAME}" \
    psql -U "${POSTGRES_USER}" -d "${POSTGRES_DB}" -tAc \
    "SELECT count(*) FROM information_schema.tables WHERE table_schema='public'")
TABLE_COUNT="${TABLE_COUNT//[[:space:]]/}"
log "Public tables found: ${TABLE_COUNT}"

[ "${TABLE_COUNT:-0}" -gt 0 ] || fail "Restore produced 0 tables — dump may be empty or corrupt"
pass "Restore-test PASSED: ${TABLE_COUNT} tables in ${POSTGRES_DB}"

echo ""
echo "=== RESTORE-TEST RESULT ==="
echo "Backup: ${BACKUP_FILE}"
echo "Tables restored: ${TABLE_COUNT}"
echo "Status: PASS"
