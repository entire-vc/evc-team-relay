#!/bin/bash
#
# PostgreSQL restore script for Relay Control Plane
#
# Usage: ./restore.sh <backup-file>
#        ./restore.sh --latest
#
# Environment variables:
#   POSTGRES_HOST     - PostgreSQL host (default: postgres)
#   POSTGRES_PORT     - PostgreSQL port (default: 5432)
#   POSTGRES_USER     - PostgreSQL user (required)
#   POSTGRES_PASSWORD - PostgreSQL password (required)
#   POSTGRES_DB       - PostgreSQL database (required)
#   BACKUP_DIR        - Backup directory (default: /backups)
#   FORCE             - Skip confirmation prompt (default: false)

set -euo pipefail

# Configuration with defaults
POSTGRES_HOST="${POSTGRES_HOST:-postgres}"
POSTGRES_PORT="${POSTGRES_PORT:-5432}"
BACKUP_DIR="${BACKUP_DIR:-/backups}"
FORCE="${FORCE:-false}"

# Colors for output (if terminal supports it)
if [ -t 1 ]; then
    RED='\033[0;31m'
    GREEN='\033[0;32m'
    YELLOW='\033[1;33m'
    NC='\033[0m' # No Color
else
    RED=''
    GREEN=''
    YELLOW=''
    NC=''
fi

# Logging functions
log_info() {
    echo -e "${GREEN}[INFO]${NC} $1"
}

log_warn() {
    echo -e "${YELLOW}[WARN]${NC} $1"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1" >&2
}

# Show usage
usage() {
    cat << EOF
Usage: $0 [OPTIONS] <backup-file>
       $0 --latest

Restore PostgreSQL database from backup.

Options:
  --latest          Restore from latest backup
  --force           Skip confirmation prompt
  --list            List available backups
  -h, --help        Show this help message

Arguments:
  backup-file       Path to backup file (.sql.gz)

Environment variables:
  POSTGRES_HOST     PostgreSQL host (default: postgres)
  POSTGRES_PORT     PostgreSQL port (default: 5432)
  POSTGRES_USER     PostgreSQL user (required)
  POSTGRES_PASSWORD PostgreSQL password (required)
  POSTGRES_DB       PostgreSQL database (required)
  BACKUP_DIR        Backup directory (default: /backups)

Examples:
  $0 --latest                           # Restore from latest backup
  $0 /backups/relaycp_2024-01-01.sql.gz # Restore specific backup
  $0 --force --latest                   # Restore without confirmation
  $0 --list                             # List available backups
EOF
    exit 0
}

# List available backups
list_backups() {
    log_info "Available backups in ${BACKUP_DIR}:"
    echo ""

    if [ -d "$BACKUP_DIR" ]; then
        local count=0
        while IFS= read -r file; do
            if [ -f "$file" ]; then
                local size
                size=$(stat -c%s "$file" 2>/dev/null || stat -f%z "$file" 2>/dev/null)
                local size_mb=$((size / 1024 / 1024))
                local mtime
                mtime=$(stat -c%y "$file" 2>/dev/null | cut -d' ' -f1 || stat -f%Sm -t "%Y-%m-%d" "$file" 2>/dev/null)
                printf "  %-50s  %6s MB  %s\n" "$(basename "$file")" "$size_mb" "$mtime"
                ((count++)) || true
            fi
        done < <(find "$BACKUP_DIR" -name "*.sql.gz" -type f | sort -r)

        echo ""
        log_info "Total: ${count} backups found"
    else
        log_warn "Backup directory does not exist: ${BACKUP_DIR}"
    fi
    exit 0
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

# Find latest backup
find_latest_backup() {
    local latest="${BACKUP_DIR}/${POSTGRES_DB}_latest.sql.gz"

    if [ -L "$latest" ] && [ -e "$latest" ]; then
        echo "$latest"
        return 0
    fi

    # Fallback: find most recent backup file
    local found
    found=$(find "$BACKUP_DIR" -name "${POSTGRES_DB}_*.sql.gz" -type f -printf '%T@ %p\n' 2>/dev/null | sort -rn | head -1 | cut -d' ' -f2-)

    if [ -n "$found" ] && [ -f "$found" ]; then
        echo "$found"
        return 0
    fi

    log_error "No backup found in ${BACKUP_DIR}"
    exit 1
}

# Confirm restore action
confirm_restore() {
    local backup_file="$1"

    if [ "$FORCE" == "true" ]; then
        return 0
    fi

    echo ""
    log_warn "This will REPLACE the contents of database '${POSTGRES_DB}'"
    log_warn "Backup file: ${backup_file}"
    echo ""

    read -p "Are you sure you want to continue? (yes/no): " confirm
    if [ "$confirm" != "yes" ]; then
        log_info "Restore cancelled"
        exit 0
    fi
}

# Perform restore
perform_restore() {
    local backup_file="$1"

    # Verify backup file exists
    if [ ! -f "$backup_file" ]; then
        log_error "Backup file not found: ${backup_file}"
        exit 1
    fi

    # Verify gzip integrity
    log_info "Verifying backup integrity..."
    if ! gzip -t "$backup_file" 2>/dev/null; then
        log_error "Backup file is corrupted or not a valid gzip file"
        exit 1
    fi
    log_info "Backup integrity verified"

    log_info "Restoring ${POSTGRES_DB} from ${backup_file}..."

    local start_time
    start_time=$(date +%s)

    # Export password for psql
    export PGPASSWORD="${POSTGRES_PASSWORD}"

    # Restore from backup
    if gunzip -c "$backup_file" | psql \
        -h "$POSTGRES_HOST" \
        -p "$POSTGRES_PORT" \
        -U "$POSTGRES_USER" \
        -d "$POSTGRES_DB" \
        --quiet \
        --no-password; then

        local end_time
        end_time=$(date +%s)
        local duration=$((end_time - start_time))

        log_info "Restore completed successfully in ${duration}s"
    else
        log_error "Restore failed"
        exit 1
    fi
}

# Main execution
main() {
    local backup_file=""
    local use_latest=false

    # Parse arguments
    while [[ $# -gt 0 ]]; do
        case $1 in
            --latest)
                use_latest=true
                shift
                ;;
            --force)
                FORCE=true
                shift
                ;;
            --list)
                list_backups
                ;;
            -h|--help)
                usage
                ;;
            -*)
                log_error "Unknown option: $1"
                usage
                ;;
            *)
                backup_file="$1"
                shift
                ;;
        esac
    done

    # Check requirements
    check_requirements

    # Determine backup file
    if [ "$use_latest" == "true" ]; then
        backup_file=$(find_latest_backup)
        log_info "Using latest backup: ${backup_file}"
    elif [ -z "$backup_file" ]; then
        log_error "No backup file specified. Use --latest or provide a file path."
        echo ""
        usage
    fi

    # Confirm and restore
    confirm_restore "$backup_file"
    perform_restore "$backup_file"

    log_info "Database restore completed!"
}

main "$@"
