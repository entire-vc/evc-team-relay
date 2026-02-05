# Backup & Restore Guide

This guide covers backing up and restoring EVC Team Relay data.

## Data Locations

All persistent data is stored in the `data/` directory:

```
infra/data/
├── postgres/     # PostgreSQL database (via named volume)
├── minio/        # Document storage (S3 objects)
├── uploads/      # User uploads (logos, etc.)
├── backups/      # Automatic PostgreSQL backups
├── caddy/        # SSL certificates
└── caddy_config/ # Caddy configuration state
```

## Automatic Backups

The `postgres-backup` service automatically backs up PostgreSQL daily.

### Configuration

```bash
# .env
BACKUP_RETENTION=7              # Keep backups for 7 days
BACKUP_HOUR=2                   # Run at 2:00 AM UTC
BACKUP_RUN_ON_STARTUP=true      # Also backup when container starts

# Optional: Upload to S3
BACKUP_S3_ENABLED=true
BACKUP_S3_BUCKET=relay-backups
```

### Backup Files

Backups are stored in `data/backups/`:

```
data/backups/
├── relaycp_2024-01-15_02-00.sql.gz
├── relaycp_2024-01-16_02-00.sql.gz
└── relaycp_2024-01-17_02-00.sql.gz
```

### Manual Backup

Trigger a backup manually:

```bash
docker compose exec postgres-backup /backup.sh
```

## Full System Backup

### Recommended Approach

Stop services, backup everything, restart:

```bash
cd /opt/evc-team-relay/infra

# Stop services (brief downtime)
docker compose down

# Backup entire data directory
tar -czvf backup-$(date +%Y%m%d).tar.gz data/

# Also backup configuration
cp .env backup-env-$(date +%Y%m%d)
cp relay/relay.toml backup-relay-$(date +%Y%m%d).toml

# Restart services
docker compose up -d
```

### Online Backup (No Downtime)

For zero-downtime backup:

```bash
# PostgreSQL (consistent snapshot)
docker compose exec postgres pg_dump -U relaycp relaycp | gzip > backup-db-$(date +%Y%m%d).sql.gz

# MinIO (documents)
docker compose exec minio mc mirror local/relay /backup/minio-$(date +%Y%m%d)/

# Configuration
cp .env backup-env-$(date +%Y%m%d)
```

## Restore Procedures

### Restore PostgreSQL

```bash
cd /opt/evc-team-relay/infra

# Stop control plane (keep postgres running)
docker compose stop control-plane webhook-worker email-worker

# Restore from backup
gunzip -c data/backups/relaycp_2024-01-15_02-00.sql.gz | \
  docker compose exec -T postgres psql -U relaycp relaycp

# Restart services
docker compose up -d
```

### Restore MinIO Data

```bash
# Stop relay server
docker compose stop relay-server

# Restore data
tar -xzvf minio-backup.tar.gz -C data/minio/

# Restart
docker compose up -d
```

### Full System Restore

To restore on a new server:

```bash
# 1. Install Docker and clone repo
git clone https://github.com/entire-vc/evc-team-relay.git
cd evc-team-relay/infra

# 2. Restore configuration
cp /path/to/backup-env .env
cp /path/to/backup-relay.toml relay/relay.toml

# 3. Restore data
tar -xzvf /path/to/backup-data.tar.gz

# 4. Start services
docker compose up -d

# 5. Verify
curl https://cp.yourdomain.com/health
```

## Backup Verification

Always verify backups periodically:

```bash
# Test PostgreSQL backup integrity
gunzip -c data/backups/latest.sql.gz | head -100

# Verify backup can be restored (use test database)
docker compose exec -T postgres createdb -U relaycp test_restore
gunzip -c data/backups/latest.sql.gz | \
  docker compose exec -T postgres psql -U relaycp test_restore
docker compose exec -T postgres dropdb -U relaycp test_restore
```

## Disaster Recovery

### Scenario: Database Corruption

1. Stop affected services
2. Restore from latest backup
3. Verify data integrity
4. Restart services

```bash
docker compose stop control-plane
gunzip -c data/backups/relaycp_latest.sql.gz | \
  docker compose exec -T postgres psql -U relaycp relaycp
docker compose up -d
```

### Scenario: Complete Server Loss

1. Provision new server
2. Install Docker
3. Clone repository
4. Restore `.env` and `relay.toml` from backup
5. Restore `data/` directory from backup
6. Update DNS if IP changed
7. Start services

### Scenario: Corrupted MinIO Data

1. Stop relay server
2. Clear corrupted data: `rm -rf data/minio/*`
3. Restore from backup
4. Restart relay server

**Note**: If no MinIO backup exists, documents are lost but metadata (shares, users) remains in PostgreSQL.

## Backup Automation

### Cron Job Example

```bash
# /etc/cron.d/evc-relay-backup
0 3 * * * root cd /opt/evc-team-relay/infra && \
  tar -czvf /backup/relay-$(date +\%Y\%m\%d).tar.gz data/ && \
  find /backup -name "relay-*.tar.gz" -mtime +30 -delete
```

### S3 Sync Example

```bash
#!/bin/bash
# /opt/scripts/backup-to-s3.sh
cd /opt/evc-team-relay/infra
tar -czvf /tmp/relay-backup.tar.gz data/
aws s3 cp /tmp/relay-backup.tar.gz s3://your-bucket/relay/relay-$(date +%Y%m%d).tar.gz
rm /tmp/relay-backup.tar.gz
```

## Best Practices

1. **Test restores regularly** — A backup is only good if you can restore it
2. **Store backups off-site** — Use S3, another server, or cloud storage
3. **Encrypt sensitive backups** — `.env` and database contain secrets
4. **Monitor backup jobs** — Set up alerts for failed backups
5. **Document your process** — Keep restore procedures up to date
6. **Keep multiple generations** — Don't just keep the latest backup
