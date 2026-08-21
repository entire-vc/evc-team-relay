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

### Architecture

**The published images are `linux/amd64` only** — `control-plane`, `web-publish`
and `relay-server` alike. There is no `arm64` manifest, so on an arm64 host
(Apple Silicon, AWS Graviton, Ampere at Hetzner/OVH, Raspberry Pi) the install
below stops at the pull step unless you run them under emulation:

```bash
export DOCKER_DEFAULT_PLATFORM=linux/amd64
```

Export it for the whole session rather than prefixing one command: `relay-server`
is pulled later by `docker compose up`, not by the pull script, and it needs the
same setting. Emulated amd64 on arm64 is noticeably slower — fine for a local
trial or demo, not what we'd recommend for a production install.

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
# DATABASE_URL is a separate literal value — if you randomize POSTGRES_PASSWORD,
# also update the password embedded in DATABASE_URL to match, or the app
# will fail to connect (postgres and control-plane will disagree on it).
MINIO_ROOT_PASSWORD=$(openssl rand -hex 16)
BOOTSTRAP_ADMIN_EMAIL=admin@yourdomain.com
BOOTSTRAP_ADMIN_PASSWORD=your-secure-password

# REQUIRED — Ed25519 keypair for relay auth. Generate the private half:
openssl genpkey -algorithm ed25519 -out relay_private.pem
# RELAY_PRIVATE_KEY= the base64 output of:
openssl base64 -A -in relay_private.pem
# You'll need the matching public key in step 3 below.
```

See [Configuration Reference](configuration.md) for all options.

### 3. Configure Relay Server

```bash
cp relay/relay.toml.example relay/relay.toml
```

Edit `relay/relay.toml`:
- `[server].url` — **must equal `RELAY_AUDIENCE` in `.env` exactly** (or, if you left
  `RELAY_AUDIENCE` unset, must equal `https://${DOMAIN_BASE}` — the default control-plane
  derives from `RELAY_PUBLIC_URL`'s host). A mismatch here fails silently: tokens are issued
  and signed correctly but every WebSocket connection is rejected with no useful log line.
  Remember there is no separate `relay.` subdomain (step 4 below) — this is
  `https://yourdomain.com`, not `https://relay.yourdomain.com`.
- `[store]` — MinIO credentials from `.env` (`MINIO_ROOT_USER`/`MINIO_ROOT_PASSWORD`).
- `[[auth]]` — `key_id` matches `RELAY_KEY_ID` in `.env` if you set one (defaults to `relay_cp_dev`
  if omitted); `public_key` is the Ed25519 public key derived from the private key you generated
  in step 2:
  ```bash
  python3 -c "
  from cryptography.hazmat.primitives import serialization
  import base64
  with open('relay_private.pem', 'rb') as f:
      priv = serialization.load_pem_private_key(f.read(), password=None)
  pub = priv.public_key().public_bytes(encoding=serialization.Encoding.Raw, format=serialization.PublicFormat.Raw)
  print(base64.b64encode(pub).decode())
  "
  ```

### 4. Configure DNS

`DOMAIN_BASE` itself is the relay server's domain — there is no separate `relay.` subdomain.
Point these DNS records to your server IP:

| Record | Type | Value |
|--------|------|-------|
| `yourdomain.com` (i.e. your `DOMAIN_BASE`) | A | Your server IP |
| `cp.yourdomain.com` | A | Your server IP |
| `docs.yourdomain.com` | A | Your server IP (optional, for web publishing) |

### 5. Review Caddy Configuration

The default `Caddyfile` handles TLS termination and routing. For plain HTTP endpoints it also
does **WebSocket token proxying**: since the browser WebSocket API cannot set custom headers,
Caddy extracts `?token=` from the query string and sets it as an `Authorization: Bearer` header
— but the relay server's WebSocket-upgrade handlers only ever read the token from the query
string, never from a header, so that rewrite must skip the WS paths or it strips the one thing
the handler checks:

```caddy
{$DOMAIN_BASE} {
  @token_in_query {
    query token=*
    not path /doc/ws/* /d/*/ws/*
  }
  handle @token_in_query {
    route {
      request_header Authorization "Bearer {query.token}"
      uri query -token
      reverse_proxy relay-server:8080
    }
  }
  handle {
    reverse_proxy relay-server:8080
  }
}
```

This allows the Obsidian plugin (and other browser-based clients) to authenticate using `wss://yourdomain.com/doc/ws/{docId}?token=CWT_TOKEN` — the WS upgrade carries the token straight through to relay-server's own query-param verifier, unmodified.

> **Important**: Do not add the `Authorization` rewrite back to the `/doc/ws/*` and `/d/*/ws/*` paths — it was there originally and caused every WebSocket connection to fail auth with `missing_token` (see [#137](https://github.com/entire-vc/evc-team-relay/pull/137) for the incident this config now avoids).

### 6. Pull the Published Images

`web-publish` builds from source only with a GitHub token scoped to our private
`@entire-vc/*` packages — not available outside the org. Pull the images the release
workflow already publishes publicly instead (run from the repo root, one level up from
`infra/`):

```bash
bash scripts/pull-published-images.sh
```

This tags them locally as `infra-control-plane:latest` / `infra-web-publish:latest`, which
`docker compose up` picks up without attempting to build. Pass a version to pin one
(`bash scripts/pull-published-images.sh 1.10.0`) instead of the default `latest`.

> **arm64 hosts:** these images are linux/amd64 only, and so is `relay-server` in step 7 —
> `export DOCKER_DEFAULT_PLATFORM=linux/amd64` for the session before running this (see
> [Architecture](#architecture) above). The script refuses with an explanation rather than
> pulling something that can't run.

If you do have org access and want to build from source instead (e.g. active development),
skip this step and run `docker compose up -d --build` in step 7.

### 7. Start Services

```bash
docker compose up -d
```

Wait for all services to become healthy:

```bash
docker compose ps
```

### 8. Verify Installation

```bash
# Check health
curl https://cp.yourdomain.com/health
# Expected: {"ok":true}

# Check version
curl https://cp.yourdomain.com/version
# Expected: {"version":"1.x.x"}
```

### 9. Access Admin Panel

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

This is the default path (step 6 above) — `bash scripts/pull-published-images.sh [version]`
pulls both `control-plane` and `web-publish` from GitHub Container Registry and tags them
locally so `docker compose up -d` uses them without building. Re-run it with a new version
to update.

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

`docker compose pull` won't work here — `control-plane`/`web-publish` are tagged locally
(`infra-control-plane:latest`/`infra-web-publish:latest`, not a registry reference), by design
so the same compose file works whether you build from source or pull. Re-run the pull script
instead:

```bash
bash scripts/pull-published-images.sh [version]
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
