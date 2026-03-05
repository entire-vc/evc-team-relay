# AI Agent Integration

Connect your AI agent to Obsidian notes via EVC Team Relay. Your agent reads, creates, and updates notes — changes sync to Obsidian in real-time.

Two integration methods:

| Method | Best for | Protocol |
|--------|----------|----------|
| **MCP Server** (recommended) | Claude Code, Codex CLI, OpenCode | MCP (stdio / HTTP) |
| **OpenClaw Skill** | OpenClaw agents | Bash scripts |

---

## Prerequisites

1. **A running Team Relay instance** — self-hosted or [hosted](https://entire.vc)
2. **A user account** with access to shared folders
3. **Shared folders** created in Obsidian via the Team Relay plugin

You'll need three values:

| Value | Example | Where to get |
|-------|---------|-------------|
| **Control Plane URL** | `https://cp.tr.entire.vc` | Your admin provides this. It's the URL of the control plane API. |
| **Email** | `agent@yourteam.com` | Your account email. Ask your admin to create a dedicated agent account. |
| **Password** | `your-password` | Your account password. |

> **Tip:** Create a dedicated user account for your AI agent (e.g. `agent@yourteam.com`) rather than using your personal account. This keeps audit logs clean and lets you manage agent permissions separately.

### Getting an account

**If you're the admin:**
1. Open the Obsidian plugin settings → Team Relay → your server
2. Go to the admin panel or use the API: `POST /v1/admin/users`
3. Create a user with email and password
4. Add the user as an **editor** on the shared folders the agent should access

**If you're a team member:**
Ask your admin to create an agent account and grant it access to the relevant shared folders.

---

## Option 1: MCP Server (Recommended)

The MCP server wraps the Relay API into standard [MCP](https://modelcontextprotocol.io) tools. It handles authentication, token refresh, and typed input validation automatically.

**Repo:** [entire-vc/evc-team-relay-mcp](https://github.com/entire-vc/evc-team-relay-mcp)

### Claude Code

Add to `.mcp.json` in your project root (or `~/.claude/.mcp.json` for global):

```json
{
  "mcpServers": {
    "evc-relay": {
      "command": "uvx",
      "args": ["evc-team-relay-mcp"],
      "env": {
        "RELAY_CP_URL": "https://cp.yourdomain.com",
        "RELAY_EMAIL": "agent@yourteam.com",
        "RELAY_PASSWORD": "your-password"
      }
    }
  }
}
```

Requires [uv](https://docs.astral.sh/uv/) installed. The `uvx` command downloads and runs the server automatically — no manual installation needed.

### Codex CLI

Add to your `codex.json`:

```json
{
  "mcp_servers": {
    "evc-relay": {
      "type": "stdio",
      "command": "uvx",
      "args": ["evc-team-relay-mcp"],
      "env": {
        "RELAY_CP_URL": "https://cp.yourdomain.com",
        "RELAY_EMAIL": "agent@yourteam.com",
        "RELAY_PASSWORD": "your-password"
      }
    }
  }
}
```

### OpenCode

Add to `opencode.json`:

```json
{
  "mcpServers": {
    "evc-relay": {
      "command": "uvx",
      "args": ["evc-team-relay-mcp"],
      "env": {
        "RELAY_CP_URL": "https://cp.yourdomain.com",
        "RELAY_EMAIL": "agent@yourteam.com",
        "RELAY_PASSWORD": "your-password"
      }
    }
  }
}
```

### Remote / Shared Deployment (HTTP)

For team-wide or server-side setups, run the MCP server as an HTTP service:

```bash
RELAY_CP_URL=https://cp.yourdomain.com \
RELAY_EMAIL=agent@yourteam.com \
RELAY_PASSWORD=your-password \
uvx evc-team-relay-mcp --transport http --port 8888
```

Or with Docker:

```bash
cd evc-team-relay-mcp
docker compose up -d
```

Then point your MCP client at the HTTP endpoint:

```json
{
  "mcpServers": {
    "evc-relay": {
      "type": "streamable-http",
      "url": "http://your-server:8888/mcp"
    }
  }
}
```

### Available MCP Tools

Once connected, your agent has these tools:

| Tool | Description |
|------|-------------|
| `authenticate` | Login (auto-managed, rarely needed manually) |
| `list_shares` | List accessible shared folders and documents |
| `list_files` | List files in a folder share |
| **`read_file`** | Read a file by path from a folder share |
| **`upsert_file`** | Create or update a file by path |
| `read_document` | Read by doc_id (low-level) |
| `write_document` | Write by doc_id (doc shares only) |
| `delete_file` | Delete a file from a folder share |

**Typical workflow:** `list_shares` → `list_files` → `read_file` / `upsert_file`

Authentication is automatic — the server logs in and refreshes tokens internally.

---

## Option 2: OpenClaw Skill

For [OpenClaw](https://github.com/openclaw/openclaw) agents, the skill provides bash-script-based tools.

**Repo:** [entire-vc/evc-team-relay-openclaw-skill](https://github.com/entire-vc/evc-team-relay-openclaw-skill)

### Install

```bash
cp -r evc-team-relay-openclaw-skill ~/.openclaw/skills/evc-team-relay/
chmod +x ~/.openclaw/skills/evc-team-relay/scripts/*.sh
```

### Configure

In `~/.openclaw/openclaw.json`:

```json
{
  "skills": {
    "entries": {
      "evc-team-relay": {
        "env": {
          "RELAY_CP_URL": "https://cp.yourdomain.com",
          "RELAY_EMAIL": "agent@yourteam.com",
          "RELAY_PASSWORD": "your-password"
        }
      }
    }
  }
}
```

Add to your agent:

```json
{
  "agents": {
    "list": [
      {
        "id": "main",
        "skills": ["evc-team-relay"]
      }
    ]
  }
}
```

### Available Scripts

| Script | Purpose |
|--------|---------|
| `auth.sh` | Authenticate and get JWT token |
| `list-shares.sh` | List shared folders |
| `list-files.sh` | List files in a folder |
| **`read-file.sh`** | Read file by path (recommended) |
| **`upsert-file.sh`** | Create/update file by path |
| `read.sh` | Read by doc_id (low-level) |
| `write.sh` | Write to doc shares |
| `delete-file.sh` | Delete a file |

---

## Testing the Connection

### Quick test with curl

```bash
# Set your credentials
export RELAY_CP_URL="https://cp.yourdomain.com"
export RELAY_EMAIL="agent@yourteam.com"
export RELAY_PASSWORD="your-password"

# 1. Login — get a token
TOKEN=$(curl -sf -X POST "$RELAY_CP_URL/v1/auth/login" \
  -H "Content-Type: application/json" \
  -d "{\"email\": \"$RELAY_EMAIL\", \"password\": \"$RELAY_PASSWORD\"}" \
  | jq -r '.access_token')

echo "Token: ${TOKEN:0:20}..."

# 2. List shares
curl -sf "$RELAY_CP_URL/v1/shares" \
  -H "Authorization: Bearer $TOKEN" | jq '.[].path'

# 3. Read a file (replace SHARE_ID and path)
curl -sf "$RELAY_CP_URL/v1/documents/SHARE_ID/files?share_id=SHARE_ID" \
  -H "Authorization: Bearer $TOKEN" | jq '.files | keys'
```

### Test MCP server locally

```bash
RELAY_CP_URL=https://cp.yourdomain.com \
RELAY_EMAIL=agent@yourteam.com \
RELAY_PASSWORD=your-password \
uvx evc-team-relay-mcp
```

The server starts in stdio mode. Type `{"jsonrpc":"2.0","method":"tools/list","id":1}` to verify tools are available.

---

## How It Works

```
┌─────────────┐                 ┌──────────────┐                ┌──────────────┐
│  AI Agent   │  MCP / scripts  │  Team Relay  │   Yjs CRDT     │   Obsidian   │
│             │ ◄─────────────► │   Server     │ ◄────────────► │    Client    │
└─────────────┘   read / write  └──────────────┘   real-time    └──────────────┘
```

1. Your agent calls the MCP server (or skill scripts)
2. The MCP server calls Team Relay's REST API with a JWT token
3. Team Relay reads/writes Yjs CRDT documents
4. Obsidian clients connected to the same share see changes in real-time

Changes are bidirectional — edits made in Obsidian are immediately visible to the agent, and vice versa.

---

## Security Notes

- **Credentials are environment variables** — never passed as CLI arguments, invisible in `ps` output
- **JWT tokens expire in 1 hour** — the MCP server refreshes automatically; for OpenClaw, re-run `auth.sh`
- **Use a dedicated agent account** — don't share personal credentials
- **Editor role** grants read + write; **Viewer role** grants read-only
- The MCP server is the more secure option: no shell execution, typed inputs, single persistent process

---

## Troubleshooting

| Problem | Cause | Fix |
|---------|-------|-----|
| `401 Unauthorized` | Token expired or wrong credentials | Check email/password, re-authenticate |
| `403 Forbidden` | No access to this share | Ask admin to add agent as member |
| `404 Not Found` | Share or file doesn't exist | Use `list_shares` / `list_files` to verify |
| `502 Bad Gateway` | Relay server down | Check server health: `curl $RELAY_CP_URL/health` |
| Connection timeout | Firewall or wrong URL | Verify `RELAY_CP_URL` is reachable |
| `uvx` not found | uv not installed | Install: `curl -LsSf https://astral.sh/uv/install.sh \| sh` |

---

## Further Reading

- [Team Relay Installation Guide](installation.md)
- [API Reference](api.md)
- [MCP Server repo](https://github.com/entire-vc/evc-team-relay-mcp)
- [OpenClaw Skill repo](https://github.com/entire-vc/evc-team-relay-openclaw-skill)
- [Obsidian Plugin repo](https://github.com/entire-vc/evc-team-relay-obsidian-plugin)
