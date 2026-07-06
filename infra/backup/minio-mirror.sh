#!/bin/bash
#
# MinIO off-site mirror: local MinIO relay bucket → external S3
#
# Required env vars:
#   MINIO_ENDPOINT       - local MinIO URL (e.g. http://minio:9000)
#   MINIO_ROOT_USER      - local MinIO access key
#   MINIO_ROOT_PASSWORD  - local MinIO secret key
#   MINIO_BUCKET         - source bucket (default: relay)
#   OFFSITE_S3_ENDPOINT  - external S3 endpoint URL (e.g. https://s3.amazonaws.com)
#   OFFSITE_S3_ACCESS    - external S3 access key
#   OFFSITE_S3_SECRET    - external S3 secret key
#   OFFSITE_S3_BUCKET    - destination bucket (e.g. entire-vc-tr-backup)
#   OFFSITE_S3_PREFIX    - destination prefix (default: minio/)
#   OFFSITE_S3_ENABLED   - set to "true" to run (default: false)

set -euo pipefail

OFFSITE_S3_ENABLED="${OFFSITE_S3_ENABLED:-false}"
MINIO_ENDPOINT="${MINIO_ENDPOINT:-http://minio:9000}"
MINIO_BUCKET="${MINIO_BUCKET:-relay}"
OFFSITE_S3_PREFIX="${OFFSITE_S3_PREFIX:-minio/}"

log_info()  { echo "{\"timestamp\":\"$(date -Iseconds)\",\"level\":\"INFO\",\"message\":\"$1\"}"; }
log_error() { echo "{\"timestamp\":\"$(date -Iseconds)\",\"level\":\"ERROR\",\"message\":\"$1\"}" >&2; }
log_ok()    { echo "{\"timestamp\":\"$(date -Iseconds)\",\"level\":\"INFO\",\"message\":\"$1\",\"status\":\"success\"}"; }

if [ "${OFFSITE_S3_ENABLED}" != "true" ]; then
    log_info "OFFSITE_S3_ENABLED != true — skipping mirror"
    exit 0
fi

for var in OFFSITE_S3_ENDPOINT OFFSITE_S3_ACCESS OFFSITE_S3_SECRET OFFSITE_S3_BUCKET; do
    [ -z "${!var:-}" ] && { log_error "Missing required var: $var"; exit 1; }
done

log_info "Configuring mc aliases"
mc alias set local "${MINIO_ENDPOINT}" "${MINIO_ROOT_USER}" "${MINIO_ROOT_PASSWORD}" --quiet
mc alias set offsite "${OFFSITE_S3_ENDPOINT}" "${OFFSITE_S3_ACCESS}" "${OFFSITE_S3_SECRET}" --quiet

SRC="local/${MINIO_BUCKET}"
DST="offsite/${OFFSITE_S3_BUCKET}/${OFFSITE_S3_PREFIX}"

log_info "Starting mirror: ${SRC} → ${DST}"
if mc mirror --overwrite --remove "${SRC}" "${DST}"; then
    log_ok "Mirror complete: ${SRC} → ${DST}"
else
    log_error "Mirror failed: ${SRC} → ${DST}"
    exit 1
fi
