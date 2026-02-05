#!/bin/bash
#
# PostgreSQL backup script for Relay Control Plane
#
# Usage: ./backup.sh
#
# Environment variables:
#   POSTGRES_HOST     - PostgreSQL host (default: postgres)
#   POSTGRES_PORT     - PostgreSQL port (default: 5432)
#   POSTGRES_USER     - PostgreSQL user (required)
#   POSTGRES_PASSWORD - PostgreSQL password (required)
#   POSTGRES_DB       - PostgreSQL database (required)
#   BACKUP_DIR        - Backup directory (default: /backups)
#   BACKUP_RETENTION  - Days to keep backups (default: 7)
#   BACKUP_S3_ENABLED - Upload to S3 (default: false)
#   BACKUP_S3_BUCKET  - S3 bucket name
#   BACKUP_S3_PREFIX  - S3 prefix (default: backups/)

set -euo pipefail

# Configuration with defaults
POSTGRES_HOST="${POSTGRES_HOST:-postgres}"
POSTGRES_PORT="${POSTGRES_PORT:-5432}"
BACKUP_DIR="${BACKUP_DIR:-/backups}"
BACKUP_RETENTION="${BACKUP_RETENTION:-7}"
BACKUP_S3_ENABLED="${BACKUP_S3_ENABLED:-false}"
BACKUP_S3_PREFIX="${BACKUP_S3_PREFIX:-backups/}"

# Timestamp for backup file
TIMESTAMP=$(date +%Y-%m-%d_%H-%M-%S)
BACKUP_FILE="${BACKUP_DIR}/${POSTGRES_DB}_${TIMESTAMP}.sql.gz"
BACKUP_LATEST="${BACKUP_DIR}/${POSTGRES_DB}_latest.sql.gz"

# Logging functions
log_info() {
    echo "{\"timestamp\":\"$(date -Iseconds)\",\"level\":\"INFO\",\"message\":\"$1\"}"
}

log_error() {
    echo "{\"timestamp\":\"$(date -Iseconds)\",\"level\":\"ERROR\",\"message\":\"$1\"}" >&2
}

log_success() {
    echo "{\"timestamp\":\"$(date -Iseconds)\",\"level\":\"INFO\",\"message\":\"$1\",\"status\":\"success\"}"
}

# Check required environment variables
check_requirements() {
    local missing=""
    [ -z "${POSTGRES_USER:-}" ] && missing="${missing} POSTGRES_USER"
    [ -z "${POSTGRES_PASSWORD:-}" ] && missing="${missing} POSTGRES_PASSWORD"
    [ -z "${POSTGRES_DB:-}" ] && missing="${missing} POSTGRES_DB"

    if [ -n "$missing" ]; then
        log_error "Missing required environment variables:${missing}"
        exit 1
    fi
}

# Check disk space (require at least 1GB free)
check_disk_space() {
    local available
    available=$(df -m "$BACKUP_DIR" | awk 'NR==2 {print $4}')

    if [ "$available" -lt 1024 ]; then
        log_error "Insufficient disk space: ${available}MB available, 1024MB required"
        exit 1
    fi
    log_info "Disk space check passed: ${available}MB available"
}

# Perform backup
perform_backup() {
    log_info "Starting backup of ${POSTGRES_DB} to ${BACKUP_FILE}"

    local start_time
    start_time=$(date +%s)

    # Create backup directory if it doesn't exist
    mkdir -p "$BACKUP_DIR"

    # Run pg_dump with compression
    export PGPASSWORD="${POSTGRES_PASSWORD}"
    if pg_dump \
        -h "$POSTGRES_HOST" \
        -p "$POSTGRES_PORT" \
        -U "$POSTGRES_USER" \
        -d "$POSTGRES_DB" \
        --no-owner \
        --no-acl \
        --clean \
        --if-exists \
        | gzip > "$BACKUP_FILE"; then

        local end_time
        end_time=$(date +%s)
        local duration=$((end_time - start_time))
        local size
        size=$(stat -c%s "$BACKUP_FILE" 2>/dev/null || stat -f%z "$BACKUP_FILE" 2>/dev/null)

        # Update latest symlink
        ln -sf "$(basename "$BACKUP_FILE")" "$BACKUP_LATEST"

        log_success "Backup completed: ${BACKUP_FILE}, size: ${size} bytes, duration: ${duration}s"
        echo "${size}" > "${BACKUP_DIR}/.last_backup_size"
        date +%s > "${BACKUP_DIR}/.last_backup_timestamp"
    else
        log_error "Backup failed"
        rm -f "$BACKUP_FILE"
        exit 1
    fi
}

# Clean old backups based on retention policy
cleanup_old_backups() {
    log_info "Cleaning backups older than ${BACKUP_RETENTION} days"

    local count=0
    while IFS= read -r file; do
        if [ -f "$file" ]; then
            log_info "Deleting old backup: $file"
            rm -f "$file"
            ((count++)) || true
        fi
    done < <(find "$BACKUP_DIR" -name "${POSTGRES_DB}_*.sql.gz" -type f -mtime +"$BACKUP_RETENTION" 2>/dev/null)

    log_info "Deleted ${count} old backups"
}

# Upload to S3 (optional)
upload_to_s3() {
    if [ "${BACKUP_S3_ENABLED}" != "true" ]; then
        return 0
    fi

    if [ -z "${BACKUP_S3_BUCKET:-}" ]; then
        log_error "BACKUP_S3_BUCKET not set but S3 upload is enabled"
        return 1
    fi

    log_info "Uploading backup to S3: s3://${BACKUP_S3_BUCKET}/${BACKUP_S3_PREFIX}"

    local s3_path="s3://${BACKUP_S3_BUCKET}/${BACKUP_S3_PREFIX}$(basename "$BACKUP_FILE")"

    # Use MinIO client (mc) if available, otherwise aws cli
    if command -v mc &> /dev/null; then
        if mc cp "$BACKUP_FILE" "minio/${BACKUP_S3_BUCKET}/${BACKUP_S3_PREFIX}"; then
            log_success "Uploaded to MinIO: ${s3_path}"
        else
            log_error "Failed to upload to MinIO"
            return 1
        fi
    elif command -v aws &> /dev/null; then
        if aws s3 cp "$BACKUP_FILE" "$s3_path"; then
            log_success "Uploaded to S3: ${s3_path}"
        else
            log_error "Failed to upload to S3"
            return 1
        fi
    else
        log_error "No S3 client available (mc or aws cli required)"
        return 1
    fi
}

# Verify backup (optional restore test)
verify_backup() {
    if [ "${BACKUP_VERIFY:-false}" != "true" ]; then
        return 0
    fi

    log_info "Verifying backup integrity..."

    # Just verify gzip integrity
    if gzip -t "$BACKUP_FILE" 2>/dev/null; then
        log_success "Backup verification passed (gzip integrity OK)"
    else
        log_error "Backup verification failed (gzip integrity check)"
        return 1
    fi
}

# Main execution
main() {
    log_info "PostgreSQL backup starting"

    check_requirements
    check_disk_space
    perform_backup
    verify_backup
    cleanup_old_backups
    upload_to_s3

    log_success "Backup process completed successfully"
}

main "$@"
