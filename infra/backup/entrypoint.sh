#!/bin/bash
#
# Entrypoint for postgres-backup container
# Uses a simple sleep-based scheduler (container-friendly, no crond)

set -e

# Configuration
BACKUP_HOUR="${BACKUP_HOUR:-2}"  # Hour to run backup (0-23, default: 2 AM UTC)
RUN_ON_STARTUP="${RUN_ON_STARTUP:-true}"

log_info() {
    echo "{\"timestamp\":\"$(date -Iseconds)\",\"level\":\"INFO\",\"message\":\"$1\"}"
}

log_warn() {
    echo "{\"timestamp\":\"$(date -Iseconds)\",\"level\":\"WARN\",\"message\":\"$1\"}"
}

log_info "Postgres backup service starting"
log_info "Scheduled backup hour: ${BACKUP_HOUR}:00 UTC daily"

# Run backup on startup (if enabled)
if [ "${RUN_ON_STARTUP}" = "true" ]; then
    log_info "Running initial backup on startup"
    /usr/local/bin/backup.sh || log_warn "Initial backup failed, will retry on schedule"
fi

# Simple scheduler loop
log_info "Starting backup scheduler (runs daily at ${BACKUP_HOUR}:00 UTC)"

while true; do
    # Calculate seconds until next backup time
    current_hour=$(date -u +%H | sed 's/^0//')
    current_min=$(date -u +%M | sed 's/^0//')
    current_sec=$(date -u +%S | sed 's/^0//')

    # Seconds since midnight
    now_secs=$((current_hour * 3600 + current_min * 60 + current_sec))

    # Target time in seconds since midnight
    target_secs=$((BACKUP_HOUR * 3600))

    # Calculate wait time
    if [ $now_secs -lt $target_secs ]; then
        # Target is later today
        wait_secs=$((target_secs - now_secs))
    else
        # Target is tomorrow
        wait_secs=$((86400 - now_secs + target_secs))
    fi

    # Log next backup time (calculate hours and minutes for readability)
    wait_hours=$((wait_secs / 3600))
    wait_mins=$(((wait_secs % 3600) / 60))
    log_info "Next backup in ${wait_hours}h ${wait_mins}m (${wait_secs} seconds)"

    # Sleep until backup time
    sleep $wait_secs

    # Run backup
    log_info "Running scheduled backup"
    /usr/local/bin/backup.sh || log_warn "Scheduled backup failed"

    # Small delay to avoid running twice if we're right at the boundary
    sleep 60
done
