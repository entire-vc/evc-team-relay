# Configuration Reference

All configuration is done through environment variables in the `.env` file.

## Core Settings

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `DOMAIN_BASE` | Yes | — | Base domain (e.g., `example.com`). Used to construct `cp.example.com`, `relay.example.com` |
| `ACME_EMAIL` | Yes | — | Email for Let's Encrypt certificate notifications |
| `JWT_SECRET` | Yes | — | Secret key for JWT signing. Generate with `openssl rand -hex 32` |

## Database

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `POSTGRES_USER` | No | `relaycp` | PostgreSQL username |
| `POSTGRES_PASSWORD` | Yes | — | PostgreSQL password |
| `POSTGRES_DB` | No | `relaycp` | PostgreSQL database name |
| `DATABASE_URL` | Auto | — | Full connection URL (auto-generated from above) |

## Object Storage (MinIO)

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `MINIO_ROOT_USER` | No | `relay` | MinIO root username |
| `MINIO_ROOT_PASSWORD` | Yes | — | MinIO root password |

## Bootstrap Admin

These create the first admin user when the database is empty.

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `BOOTSTRAP_ADMIN_EMAIL` | Yes | — | Email for initial admin account |
| `BOOTSTRAP_ADMIN_PASSWORD` | Yes | — | Password for initial admin (min 8 chars) |

## Server Identity

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `SERVER_NAME` | No | `EVC Team Relay` | Display name shown in plugin |
| `SERVER_ID` | No | (auto) | Unique identifier, defaults to relay key ID |
| `RELAY_PUBLIC_URL` | No | `wss://relay.${DOMAIN_BASE}` | Public WebSocket URL for relay server |

## Logging

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `LOG_LEVEL` | No | `INFO` | Log level: `DEBUG`, `INFO`, `WARNING`, `ERROR` |
| `LOG_FORMAT` | No | `json` | Log format: `json` or `text` |

## Email (Notifications)

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `EMAIL_ENABLED` | No | `false` | Enable email sending |
| `SMTP_HOST` | If email | — | SMTP server hostname |
| `SMTP_PORT` | No | `587` | SMTP port (587 for TLS, 465 for SSL) |
| `SMTP_USER` | If email | — | SMTP username |
| `SMTP_PASSWORD` | If email | — | SMTP password |
| `SMTP_USE_TLS` | No | `true` | Use STARTTLS |
| `EMAIL_FROM` | If email | — | From address for emails |
| `EMAIL_REPLY_TO` | No | — | Reply-to address |

## Web Publishing

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `WEB_PUBLISH_DOMAIN` | No | (disabled) | Domain for web publishing (e.g., `docs.example.com`) |

## Monitoring

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `GRAFANA_ADMIN_USER` | No | `admin` | Grafana admin username |
| `GRAFANA_ADMIN_PASSWORD` | No | `admin` | Grafana admin password |

## Backup

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `BACKUP_RETENTION` | No | `7` | Days to keep local backups |
| `BACKUP_HOUR` | No | `2` | Hour (UTC) to run daily backup |
| `BACKUP_RUN_ON_STARTUP` | No | `true` | Run backup when container starts |
| `BACKUP_S3_ENABLED` | No | `false` | Upload backups to S3 |
| `BACKUP_S3_BUCKET` | If S3 | — | S3 bucket name for backups |

## Background Workers

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `WEBHOOK_WORKER_INTERVAL` | No | `30` | Webhook worker poll interval (seconds) |
| `WEBHOOK_WORKER_BATCH_SIZE` | No | `50` | Max webhooks per cycle |
| `EMAIL_WORKER_INTERVAL` | No | `60` | Email worker poll interval (seconds) |
| `EMAIL_WORKER_BATCH_SIZE` | No | `100` | Max emails per cycle |

## Authentication

### OAuth/OIDC

Configure in Admin Panel → Settings → OAuth, or via API:

```bash
curl -X POST https://cp.example.com/v1/admin/settings/oauth \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "enabled": true,
    "provider": "generic",
    "client_id": "your-client-id",
    "client_secret": "your-client-secret",
    "authorize_url": "https://auth.example.com/authorize",
    "token_url": "https://auth.example.com/token",
    "userinfo_url": "https://auth.example.com/userinfo"
  }'
```

### Rate Limiting

Built-in rate limits (not configurable via env):

| Endpoint | Limit |
|----------|-------|
| Login | 10/minute |
| Share creation | 20/minute |
| Member operations | 30/minute |
| Token issuance | 30/minute |

## Example Configuration

### Minimal `.env`

```bash
DOMAIN_BASE=relay.example.com
ACME_EMAIL=admin@example.com
JWT_SECRET=your-very-long-random-secret-key-here
POSTGRES_PASSWORD=secure-postgres-password
MINIO_ROOT_PASSWORD=secure-minio-password
BOOTSTRAP_ADMIN_EMAIL=admin@example.com
BOOTSTRAP_ADMIN_PASSWORD=secure-admin-password
```

### Production `.env`

```bash
# Domain
DOMAIN_BASE=example.com
ACME_EMAIL=ops@example.com

# Security
JWT_SECRET=64-character-random-hex-string
POSTGRES_PASSWORD=strong-random-password
MINIO_ROOT_PASSWORD=strong-random-password

# Admin
BOOTSTRAP_ADMIN_EMAIL=admin@example.com
BOOTSTRAP_ADMIN_PASSWORD=strong-admin-password

# Branding
SERVER_NAME=Acme Corp Relay

# Logging
LOG_LEVEL=INFO
LOG_FORMAT=json

# Email
EMAIL_ENABLED=true
SMTP_HOST=smtp.sendgrid.net
SMTP_PORT=587
SMTP_USER=apikey
SMTP_PASSWORD=your-sendgrid-api-key
EMAIL_FROM=relay@example.com

# Web Publishing
WEB_PUBLISH_DOMAIN=docs.example.com

# Monitoring
GRAFANA_ADMIN_PASSWORD=secure-grafana-password

# Backups
BACKUP_RETENTION=30
BACKUP_S3_ENABLED=true
BACKUP_S3_BUCKET=relay-backups
```

## Relay Server Configuration

The relay server uses `relay/relay.toml` for configuration:

```toml
[store]
# S3-compatible storage (MinIO)
type = "s3"
bucket = "relay"
endpoint = "http://minio:9000"
region = "us-east-1"
access_key = "your-minio-user"
secret_key = "your-minio-password"

[auth]
# Control plane public key endpoint
public_key_url = "http://control-plane:8000/keys/public"
```
