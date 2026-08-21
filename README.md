# EVC Team Relay

[![License](https://img.shields.io/badge/License-Apache%202.0-blue.svg)](LICENSE)

**Self-hosted real-time collaboration and web publishing for Obsidian.**

> Edit together. Publish to the web. Keep your data on your server.

---

## The Problem

Obsidian is great for personal notes. But when your team needs to collaborate:
- **Obsidian Sync** has no real-time collab and costs $8/user/month
- **Notion/Confluence** means leaving Obsidian and losing your workflow
- **Git-based sync** means merge conflicts on every concurrent edit
- **Obsidian Publish** is $8/month with limited customization and no access control

You want your team in Obsidian, editing together, publishing docs — without giving up data control.

## The Solution

**EVC Team Relay** is self-hosted infrastructure that adds real-time collaboration and web publishing to Obsidian. CRDT-based, local-first, fully under your control.

---

## Features

### Real-time Collaboration
- **Live editing** — CRDT-based sync via y-sweet (Yjs), no merge conflicts
  <!-- relay-server runs our own y-sweet fork: ghcr.io/entire-vc/evc-relay-server (see docs/adr-relay-server-own-fork.md), not the System3 binary. -->
- **Offline-first** — edit without connection, sync seamlessly when back online
- **Folder sharing** — share entire folders with viewer/editor permissions

### Web Publishing
- **Publish notes to the web** — internal wiki, client portal, or public docs
- **Access control** — public, protected (link + token), or private (authenticated)
- **Custom domains** — `docs.yourdomain.com`
- **Live preview** — see your published site at [docs.entire.vc/team-relay/Demo](https://docs.entire.vc/team-relay/Demo) (example)

### Enterprise Ready
- **Authentication** — OAuth/OIDC, email/password, 2FA
- **Audit logs** — who did what and when
- **Webhooks** — integrate with your automation
- **Monitoring** — Prometheus metrics + Grafana dashboards
- **Docker Compose** — one command to deploy

---

## Quick Start

Full walkthrough — DNS, production hardening, systemd, troubleshooting — lives in the
**[Installation Guide](docs/installation.md)**. The short version:

> **arm64 hosts** (Apple Silicon, Graviton, Ampere, Raspberry Pi): `control-plane` and
> `web-publish` publish native `linux/arm64` images, no setup needed. `relay-server` is
> still `linux/amd64` only — Compose pulls it under emulation automatically (pinned in
> `infra/docker-compose.yml`), nothing to export. See
> [Installation → Architecture](docs/installation.md#architecture) for details.

```bash
git clone https://github.com/entire-vc/evc-team-relay.git
cd evc-team-relay/infra
cp env.example .env
# Edit .env — at minimum DOMAIN_BASE, ACME_EMAIL, and RELAY_PRIVATE_KEY
# (generate with: openssl genpkey -algorithm ed25519 -out relay_private.pem
#  && openssl base64 -A -in relay_private.pem)

cp relay/relay.toml.example relay/relay.toml
# Edit relay.toml: MinIO credentials from .env, and the [[auth]] public_key
# derived from the same key (see docs/installation.md step 3)

# Pull the published images instead of building from source — web-publish's
# build needs a GitHub token scoped to our private packages, which nobody
# outside the org has. Point releases only; run from evc-team-relay/ (one
# level up from infra/).
bash ../scripts/pull-published-images.sh

docker compose up -d
```

**Services** (once DNS points your bare `DOMAIN_BASE`, `cp.DOMAIN_BASE`, and `docs.DOMAIN_BASE` at your server and Caddy has issued certs):
- Relay Server: `wss://yourdomain.com` (your `DOMAIN_BASE` itself — no `relay.` subdomain)
- Control Plane API: `https://cp.yourdomain.com`
- Web Publish: `https://docs.yourdomain.com`

Monitoring (Prometheus + Grafana) is on the internal Docker network only by default — see
[Configuration → Monitoring](docs/configuration.md#monitoring) to reach it.

Then install the [Obsidian plugin](https://github.com/entire-vc/evc-team-relay-obsidian-plugin) and connect.

---

## Comparison

| | Obsidian Sync | Notion | Confluence | Git sync | **Team Relay** |
|---|---|---|---|---|---|
| Real-time collab | ✗ | ✅ | ✅ | ✗ | ✅ |
| Works in Obsidian | ✅ | ✗ | ✗ | ✅ | ✅ |
| Web publish | via Publish ($8/mo) | ✅ | ✅ | manual | ✅ |
| Self-hosted | ✗ | ✗ | ✅ ($$) | ✅ | ✅ |
| Offline-first | ✅ | ✗ | ✗ | ✅ | ✅ |
| Data sovereignty | ✗ | ✗ | partial | ✅ | ✅ |
| Pricing | $8/user/mo | $8/user/mo | $6/user/mo | free | **free (self-hosted)** |

---

## Don't Want to Self-Host?

→ [**Hosted Team Relay**](https://entire.vc) — zero ops, all features, flat pricing.

---

## Want Solo Sync Without a Server?

→ [**EVC Local Sync**](https://github.com/entire-vc/evc-local-sync-plugin) — bidirectional vault ↔ local folder sync, no server needed.

---

## Documentation

Technical documentation is available in [`docs/`](./docs/):
- [Installation](./docs/installation.md)
- [Configuration](./docs/configuration.md) — includes [Web Publishing](./docs/configuration.md#web-publishing) and [Monitoring](./docs/configuration.md#monitoring)
- [API Reference](./docs/api.md)

---

## Part of the Entire VC Toolbox

| Product | What it does | Link |
|---------|-------------|------|
| **Local Sync** | Vault ↔ AI dev tools sync | [repo](https://github.com/entire-vc/evc-local-sync-plugin) |
| **Team Relay** ← you are here | Self-hosted collaboration server | this repo |
| **Team Relay Plugin** | Obsidian plugin for Team Relay | [repo](https://github.com/entire-vc/evc-team-relay-obsidian-plugin) |
| **Team Relay MCP** | MCP server for AI agent access to vault | [repo](https://github.com/entire-vc/evc-team-relay-mcp) / [PyPI](https://pypi.org/project/evc-team-relay-mcp/) |
| **OpenClaw Skill** | AI agent skill for OpenClaw | [ClawHub](https://clawhub.ai/venturecrew/evc-team-relay) / [repo](https://github.com/entire-vc/evc-team-relay-openclaw-skill) |
| **Spark MCP** | MCP server for AI workflow catalog | [repo](https://github.com/entire-vc/evc-spark-mcp) |

---


## Community

- 🌐 [entire.vc](https://entire.vc)
- 💬 [Discussions](https://github.com/entire-vc/.github/discussions)
- 📧 <support@entire.vc>
- 🛟 [Where to get help and what to expect](https://github.com/entire-vc/evc-team-relay/blob/main/SUPPORT.md)

## License

Apache 2.0 — Copyright (c) 2026 Entire VC
