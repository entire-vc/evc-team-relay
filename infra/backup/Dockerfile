# Postgres backup container with mc (MinIO Client) for off-site mirroring
FROM postgres:16-alpine

# Install bash and download mc (MinIO Client)
RUN apk add --no-cache bash curl && \
    curl -fsSL https://dl.min.io/client/mc/release/linux-amd64/mc -o /usr/local/bin/mc && \
    chmod +x /usr/local/bin/mc

# Copy backup scripts
COPY backup.sh /usr/local/bin/backup.sh
COPY restore.sh /usr/local/bin/restore.sh
COPY minio-mirror.sh /usr/local/bin/minio-mirror.sh
COPY restore-test.sh /usr/local/bin/restore-test.sh
RUN chmod +x /usr/local/bin/backup.sh /usr/local/bin/restore.sh \
              /usr/local/bin/minio-mirror.sh /usr/local/bin/restore-test.sh

# /backups is a bind-mount from the host; the host dir must be writable by the
# container user. The entrypoint handles graceful permission errors; this dir
# is created so the image works standalone for smoke tests.
RUN mkdir -p /backups

# Copy entrypoint script
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

ENTRYPOINT ["/entrypoint.sh"]
