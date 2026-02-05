# EVC Team Relay

Self-hosted collaborative editing infrastructure for Obsidian.

[![License](https://img.shields.io/badge/License-Apache%202.0-blue.svg)](LICENSE)

## Features

- **Real-time Sync** — CRDT-based collaboration via y-sweet (Yjs)
- **Document & Folder Sharing** — Share with viewer/editor permissions
- **Web Publishing** — Publish notes to the web with custom domains
- **Authentication** — OAuth/OIDC, email/password, 2FA, session management
- **Enterprise Ready** — Audit logs, webhooks, Prometheus metrics, Grafana dashboards

## Quick Start

```bash
git clone https://github.com/entire-vc/evc-team-relay.git
cd evc-team-relay/infra
cp env.example .env
# Edit .env with your settings (see docs/configuration.md)
docker compose up -d
```

**Services:**
- Control Plane API: `http://localhost:8000` (or `https://cp.yourdomain.com`)
- Relay Server: `ws://localhost:8080` (or `wss://relay.yourdomain.com`)
- Web Publish: `http://localhost:3000` (or `https://docs.yourdomain.com`)
- Grafana: `http://localhost:3001`

## Architecture

```
┌─────────────────┐     ┌─────────────────┐     ┌─────────────────┐
│  Obsidian       │     │  Control Plane  │     │  Relay Server   │
│  Plugin         │────▶│  (FastAPI)      │     │  (y-sweet)      │
│                 │     │                 │     │                 │
│  - Auth         │     │  - User mgmt    │     │  - CRDT sync    │
│  - Shares       │     │  - Shares       │     │  - WebSocket    │
│  - Sync         │────────────────────────────▶│  - S3 storage   │
└─────────────────┘     └────────┬────────┘     └────────┬────────┘
                                 │                       │
                        ┌────────▼────────┐     ┌────────▼────────┐
                        │  PostgreSQL     │     │  MinIO (S3)     │
                        │  - Users        │     │  - Documents    │
                        │  - Shares       │     │                 │
                        │  - Audit logs   │     │                 │
                        └─────────────────┘     └─────────────────┘
```

## Documentation

- [Installation Guide](docs/installation.md) — Requirements, setup, deployment
- [Configuration Reference](docs/configuration.md) — All environment variables
- [Backup & Restore](docs/backup-restore.md) — Data backup procedures
- [API Reference](docs/api.md) — REST API documentation

## Obsidian Plugin

Install the companion plugin to connect Obsidian to your relay server:

**[EVC Team Relay Plugin](https://github.com/entire-vc/evc-team-relay-obsidian-plugin)**

## Components

| Component | Technology | Purpose |
|-----------|------------|---------|
| Control Plane | Python, FastAPI, SQLAlchemy | User auth, share management, API |
| Relay Server | Rust, y-sweet | CRDT sync, WebSocket connections |
| Web Publish | SvelteKit | Public note publishing |
| Database | PostgreSQL | Users, shares, audit logs |
| Object Storage | MinIO (S3-compatible) | Document storage |
| Reverse Proxy | Caddy | TLS termination, routing |
| Monitoring | Prometheus + Grafana | Metrics and dashboards |

## Development

### Control Plane

```bash
cd apps/control-plane
make install    # Install dependencies
make fmt        # Format code
make lint       # Lint code
make test       # Run tests
```

### Docker Compose

```bash
cd infra
docker compose up -d              # Start all services
docker compose logs -f control-plane  # View logs
docker compose ps                 # Check status
```

## Security

- JWT authentication with Ed25519 signing
- Rate limiting on critical endpoints
- Comprehensive audit logging
- Password-protected shares support
- OAuth/OIDC integration

See [SECURITY.md](SECURITY.md) for reporting vulnerabilities.

## Contributing

We welcome contributions! Please see [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines.

## License

Apache 2.0 — See [LICENSE](LICENSE) for details.

---

Built with love by [Entire VC](https://github.com/entire-vc)
