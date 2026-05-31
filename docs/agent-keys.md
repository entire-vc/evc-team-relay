# Agent Keys — Quickstart

Give your AI agent write access to a share without sharing your personal login credentials.

---

## What are agent keys?

An agent key is a per-share API token you create inside the Obsidian plugin. Agents use it to upload files directly to your share — no password required, no admin access needed.

**Key facts:**

| Property | Value |
|----------|-------|
| Format | `tr_agent_` + 48 hex characters |
| Scope | `write` — upload files to one specific share |
| Bound to | A single share (not your whole account) |
| Revealed | Once on creation — copy it and store it securely |
| Max per share | 20 active keys |
| Revocable | Instantly, from the plugin |

Agent key uploads appear in your Obsidian vault on the next sync cycle (typically within a few seconds while the plugin is open).

> **When to use agent keys vs email/password**
>
> Use an agent key when:
> - You signed in with SSO (Casdoor / Google / GitHub) and have no local password
> - You want to grant an agent write access to one share without exposing your full account
> - You're setting up a CI pipeline or background task that only needs to publish files
>
> Use email/password credentials when:
> - The agent also needs to read files or list shares
> - You want full bidirectional real-time sync via CRDT

---

## Prerequisites

1. **Obsidian plugin installed** — [Team Relay plugin](https://github.com/entire-vc/evc-team-relay-obsidian-plugin)
2. **Logged in to a Team Relay server** — via the plugin settings
3. **A folder share** with web publishing enabled — you must be the share owner

> **Note:** You must be the **owner** of the share, not just a member. Viewer and editor members cannot create agent keys.

---

## Create an agent key

### Step 1 — Open Agent Keys in plugin settings

1. Open Obsidian
2. Click the Team Relay ribbon icon (or run **Team Relay: Open settings** from the command palette)
3. In the server settings sidebar, click **Agent Keys**

If you are not authenticated, the login modal appears first. Complete login and return to settings.

### Step 2 — Create a key

1. Click **+ Create agent key** (top right)
2. If your session is older than 30 minutes, you'll be prompted to confirm your identity — follow the sign-in flow and you'll return to the create modal
3. Fill in the form:
   - **Label** (required) — a name for this key, e.g. `local-agent-v1`
   - **Share** — select the share this key grants access to (only your owned shares appear)
   - **Expires** — optional; default is no expiry

4. Click **Create key**

### Step 3 — Copy the key

A one-time reveal modal shows your new key:

```
tr_agent_4a7f2c1e9b3d6082a5f1c8e4b9d7...
```

- Click **Copy to clipboard** — the button confirms "Copied" for 2 seconds
- Or click **Download .txt** to save a file with the key and metadata

**The key is shown exactly once. It cannot be retrieved again.** If you lose it, revoke it and create a new one.

Click **I've copied this key — close** to dismiss.

---

## Scope and permissions reference

| Scope | What it allows |
|-------|---------------|
| `write` | Upload files to the share via `POST /v1/web/shares/{slug}/upload` |

The `write` scope is the only scope currently available. An agent key grants no read access — it cannot list files, read content, or access any other share.

The share it is bound to must have **web publishing enabled**. If you turn off web publishing, all agent keys for that share stop working immediately.

---

## Configure relay_mcp.py

Set `RELAY_AGENT_KEY` instead of `RELAY_EMAIL` + `RELAY_PASSWORD`. You also need the share's **web slug** — the short URL-friendly name visible in the plugin (e.g. `research-vault`).

### Claude Code (`.mcp.json`)

```json
{
  "mcpServers": {
    "evc-relay": {
      "command": "uvx",
      "args": ["evc-team-relay-mcp"],
      "env": {
        "RELAY_CP_URL": "https://cp.yourdomain.com",
        "RELAY_AGENT_KEY": "tr_agent_4a7f2c1e9b3d6082a5f1c8e4b9d7..."
      }
    }
  }
}
```

### Codex CLI (`codex.json`)

```json
{
  "mcp_servers": {
    "evc-relay": {
      "type": "stdio",
      "command": "uvx",
      "args": ["evc-team-relay-mcp"],
      "env": {
        "RELAY_CP_URL": "https://cp.yourdomain.com",
        "RELAY_AGENT_KEY": "tr_agent_4a7f2c1e9b3d6082a5f1c8e4b9d7..."
      }
    }
  }
}
```

### OpenCode (`opencode.json`)

```json
{
  "mcpServers": {
    "evc-relay": {
      "command": "uvx",
      "args": ["evc-team-relay-mcp"],
      "env": {
        "RELAY_CP_URL": "https://cp.yourdomain.com",
        "RELAY_AGENT_KEY": "tr_agent_4a7f2c1e9b3d6082a5f1c8e4b9d7..."
      }
    }
  }
}
```

### Environment variables reference

| Variable | Required | Description |
|----------|----------|-------------|
| `RELAY_CP_URL` | Yes | Control plane base URL, e.g. `https://cp.tr.entire.vc` |
| `RELAY_AGENT_KEY` | Yes (key mode) | Your `tr_agent_` token |
| `RELAY_EMAIL` | Yes (password mode) | Email — not needed when using an agent key |
| `RELAY_PASSWORD` | Yes (password mode) | Password — not needed when using an agent key |

Set either (`RELAY_AGENT_KEY`) or (`RELAY_EMAIL` + `RELAY_PASSWORD`) — not both.

---

## Use the key in agent code

When `RELAY_AGENT_KEY` is set, `upsert_file` writes to the share via the upload endpoint. Pass the share's **web slug** as `share_id`:

```
upsert_file(
    share_id="research-vault",   # ← web slug, not UUID
    file_path="agent-output/report.md",
    content="# Report\n\nGenerated by agent..."
)
```

The file appears in your Obsidian vault under `agent-output/report.md` on the next sync cycle.

**Agent key mode limitations:**

| Tool | With agent key | With email/password |
|------|---------------|---------------------|
| `upsert_file` | ✅ works (slug required) | ✅ works (UUID required) |
| `list_shares` | ❌ requires credentials | ✅ works |
| `list_files` | ❌ requires credentials | ✅ works |
| `read_file` | ❌ requires credentials | ✅ works |
| `delete_file` | ❌ requires credentials | ✅ works |

For agents that only write (e.g. automated report publishing), agent keys are sufficient. For agents that also read or navigate shares, use email/password credentials.

---

## Test the key

```bash
curl -sf -X POST "https://cp.yourdomain.com/v1/web/shares/research-vault/upload?path=test.md" \
  -H "X-Agent-Key: tr_agent_4a7f2c1e..." \
  -H "Content-Type: text/plain" \
  --data-binary "# Test\nAgent key is working."
```

Expected response:

```json
{
  "ok": true,
  "share_id": "...",
  "path": "test.md",
  "size_bytes": 28,
  "modified_at": "2026-05-31T19:00:00.000000",
  "public_url": null
}
```

If `public_url` is null, web publishing domain is not configured — the file still uploads fine and appears in Obsidian.

---

## Revoke a key

1. Open Team Relay plugin settings → **Agent Keys**
2. Find the key by label
3. Click **Revoke** → confirm in the modal

The key is invalidated immediately. Any agent using it will receive `403 Forbidden` on the next upload attempt. Create a new key and update the agent's config to restore access.

---

## Troubleshooting

| Error | Cause | Fix |
|-------|-------|-----|
| `401 Unauthorized` | Key not found or malformed | Check that you copied the full `tr_agent_...` value |
| `403 Forbidden: Agent key has been revoked` | Key was revoked | Create a new key in the plugin |
| `403 Forbidden: Agent key has expired` | Key TTL elapsed | Create a new key in the plugin |
| `403 Forbidden: Agent key not valid for this share` | Key bound to different share | Use the correct key for this share slug |
| `403 Forbidden: does not have write scope` | Internal state error | Revoke key and create a new one |
| `404 Not Found` | Share slug wrong, or web publishing disabled | Verify the slug in plugin settings; enable web publishing |
| `409 Conflict` | 20-key limit reached | Revoke unused keys first |
| `413 Request Entity Too Large` | File exceeds 25 MB | Split the file or use a smaller payload |

---

## Further reading

- [AI Agent Integration Guide](ai-agent-integration.md)
- [Agent Keys design doc](design-agent-keys-plugin-ui.md) (internal)
- [MCP Server repo](https://github.com/entire-vc/evc-team-relay-mcp)
- [Team Relay plugin repo](https://github.com/entire-vc/evc-team-relay-obsidian-plugin)
