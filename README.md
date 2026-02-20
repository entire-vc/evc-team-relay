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

```bash
git clone https://github.com/entire-vc/evc-team-relay.git
cd evc-team-relay/infra
cp env.example .env
# Edit .env with your settings
docker compose up -d
```

**Services:**
- Control Plane API: `https://cp.yourdomain.com`
- Relay Server: `wss://relay.yourdomain.com`
- Web Publish: `https://docs.yourdomain.com`
- Grafana: `http://localhost:3001`

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
- [Configuration](./docs/configuration.md)
- [API Reference](./docs/api.md)
- [Web Publishing](./docs/web-publish.md)
- [Monitoring](./docs/monitoring.md)

---

## Part of the Entire VC Toolbox

| Product | What it does | Link |
|---------|-------------|------|
| **Local Sync** | Vault ↔ AI dev tools sync | [repo](https://github.com/entire-vc/evc-local-sync-plugin) |
| **Team Relay** ← you are here | Self-hosted collaboration server | this repo |
| **Team Relay Plugin** | Obsidian plugin for Team Relay | [repo](https://github.com/entire-vc/evc-team-relay-obsidian-plugin) |
| **Spark MCP** | MCP server for AI workflow catalog | [repo](https://github.com/entire-vc/evc-spark-mcp) |
| **OpenClaw Skill** | AI agent ↔ vault access | [repo](https://github.com/entire-vc/evc-team-relay-openclaw-skill) |

---


## Community

- 🌐 [entire.vc](https://entire.vc)
- 💬 [Discussions](https://github.com/entire-vc/.github/discussions)
- 📧 in@entire.vc

## License

Apache 2.0 — Copyright (c) 2026 Entire VC
