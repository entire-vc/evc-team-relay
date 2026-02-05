# Installation Guide

This guide covers installing EVC Team Relay on a Linux server using Docker Compose.

## Requirements

### Hardware

- **CPU**: 2+ cores recommended
- **RAM**: 4GB minimum, 8GB recommended
- **Storage**: 20GB+ for base install, more for document storage

### Software

- Docker Engine 24.0+
- Docker Compose 2.20+
- Domain name with DNS access
- (Optional) SSL certificates or use Caddy's automatic HTTPS

### Ports

| Port | Service | Required |
|------|---------|----------|
| 80 | HTTP (redirect to HTTPS) | Yes |
| 443 | HTTPS (Caddy) | Yes |

## Quick Start

### 1. Clone Repository

```bash
git clone https://github.com/entire-vc/evc-team-relay.git
cd evc-team-relay
```

### 2. Configure Environment

```bash
cd infra
cp env.example .env
```

Edit `.env` with your settings:

```bash
# Required settings
DOMAIN_BASE=yourdomain.com
ACME_EMAIL=admin@yourdomain.com
JWT_SECRET=$(openssl rand -hex 32)
POSTGRES_PASSWORD=$(openssl rand -hex 16)
MINIO_ROOT_PASSWORD=$(openssl rand -hex 16)
BOOTSTRAP_ADMIN_EMAIL=admin@yourdomain.com
BOOTSTRAP_ADMIN_PASSWORD=your-secure-password
```

See [Configuration Reference](configuration.md) for all options.

### 3. Configure Relay Server

```bash
cp relay/relay.toml.example relay/relay.toml
```

Edit `relay/relay.toml` to match your MinIO credentials from `.env`.

### 4. Configure DNS

Point these DNS records to your server IP:

| Record | Type | Value |
|--------|------|-------|
| `cp.yourdomain.com` | A | Your server IP |
| `relay.yourdomain.com` | A | Your server IP |
| `docs.yourdomain.com` | A | Your server IP (optional, for web publishing) |

### 5. Start Services

```bash
docker compose up -d
```

Wait for all services to become healthy:

```bash
docker compose ps
```

### 6. Verify Installation

```bash
# Check health
curl https://cp.yourdomain.com/health
# Expected: {"ok":true}

# Check version
curl https://cp.yourdomain.com/version
# Expected: {"version":"1.x.x"}
```

### 7. Access Admin Panel

Open `https://cp.yourdomain.com/admin-ui/` in your browser and login with your bootstrap admin credentials.

## Production Deployment

### Recommended Directory Structure

```
/opt/evc-team-relay/
├── docker-compose.yml
├── .env
├── relay/
│   └── relay.toml
├── Caddyfile
└── data/                  # All persistent data (backup this!)
    ├── postgres/
    ├── minio/
    ├── uploads/
    ├── caddy/
    └── backups/
```

### Using Pre-built Images

For production, you can use pre-built images from GitHub Container Registry:

```yaml
services:
  control-plane:
    image: ghcr.io/entire-vc/evc-team-relay/control-plane:latest
    # ... rest of config
```

### Systemd Service (Optional)

Create `/etc/systemd/system/evc-relay.service`:

```ini
[Unit]
Description=EVC Team Relay
Requires=docker.service
After=docker.service

[Service]
Type=oneshot
RemainAfterExit=yes
WorkingDirectory=/opt/evc-team-relay
ExecStart=/usr/bin/docker compose up -d
ExecStop=/usr/bin/docker compose down
TimeoutStartSec=0

[Install]
WantedBy=multi-user.target
```

Enable and start:

```bash
sudo systemctl enable evc-relay
sudo systemctl start evc-relay
```

## Updating

### Standard Update

```bash
cd /opt/evc-team-relay

# Pull latest changes
git pull origin main

# Rebuild and restart
docker compose up -d --build
```

### With Pre-built Images

```bash
docker compose pull
docker compose up -d
```

Migrations run automatically on startup.

## Troubleshooting

### Service Won't Start

Check logs:

```bash
docker compose logs control-plane
docker compose logs relay-server
```

### Database Connection Issues

Verify PostgreSQL is healthy:

```bash
docker compose exec postgres pg_isready -U relaycp
```

### SSL Certificate Issues

Check Caddy logs:

```bash
docker compose logs caddy
```

Ensure DNS records are properly configured and ports 80/443 are accessible.

### Health Check Failures

```bash
# Check individual service health
docker compose exec control-plane curl -s http://localhost:8000/health
docker compose exec relay-server curl -s http://localhost:9090/metrics | head -5
```

## Next Steps

- [Configure authentication](configuration.md#authentication)
- [Set up backups](backup-restore.md)
- [Install the Obsidian plugin](https://github.com/entire-vc/evc-team-relay-obsidian-plugin)
