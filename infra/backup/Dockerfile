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

# Not switching to the image's built-in non-root "postgres" user (uid 70) here:
# /backups is a bind-mount from the prod host (/opt/relay/data/backups on
# tr-relay-vm), verified root:root 0755 — a non-root container user would
# silently lose write access and every nightly backup would fail quietly
# (entrypoint.sh only log_warns on backup.sh failure, it doesn't crash).
# Fixing this needs the host dir chowned to uid 70 by someone with prod
# write access (out of scope here — Team Relay prod access is read-only),
# then this can switch to `USER postgres`. Tracked: see PR description.
# nosemgrep: dockerfile.security.missing-user-entrypoint.missing-user-entrypoint
ENTRYPOINT ["/entrypoint.sh"]
