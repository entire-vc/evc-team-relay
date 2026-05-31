# Agent Keys UX Research Report

_Date: 2026-05-31 | Author: Daedalus_

---

## Executive Summary

- A Casdoor-only user (e.g. `rj@entire.vc`) **can** create an agent key today — no local password is required. The control plane completes the Casdoor OAuth callback and mints a local HS256 JWT, which is the credential used for all subsequent API calls including agent-key creation. The Casdoor-issued token itself is never accepted; the locally-minted token is.
- `Authorization: Bearer` for the agent-keys API comes from the control plane's own `security.create_access_token()` (HS256, signed with `settings.jwt_secret`). Every auth path — local password, Casdoor OAuth, or any future OIDC provider — converges to the same locally-minted JWT before any share or key operation is performed.
- Any authenticated user who is a share owner, admin, or **any** share member (including viewer-role) can currently create, list, and revoke agent keys on shares they belong to. There is no further role restriction within the share for the key CRUD endpoints.
- Agent key management should live in the **Obsidian plugin settings** (primary surface, under a dedicated "Agent Keys" tab per server), with a contextual shortcut inline in the share detail view. A web UI is not a blocker since the plugin already holds the Bearer token and the API is ready; building a separate web UI should be deferred until there is explicit demand.

---

## Current State: Backend Auth Architecture

### Auth Layers

#### 1. Local password session (primary user auth)

Endpoints: `POST /v1/auth/login`, `POST /v1/auth/login/2fa`

After login, all requests use `Authorization: Bearer <access_token>`. The validator lives in `app/api/deps.py` — `get_current_user` calls `security.decode_access_token(token)`, which performs HS256 validation against `settings.jwt_secret`. Token payload: `{"sub": "<user_uuid>", "exp": ..., "session_id": "..."}`. The resulting access token plus a refresh token are persisted in the `user_sessions` table.

#### 2. Casdoor SSO (OAuth/OIDC)

Endpoints: `GET /v1/auth/oauth/{provider}/authorize` → `GET /v1/auth/oauth/{provider}/callback`

Flow: standard PKCE code exchange. `oauth.py:131–233` calls Casdoor's `/api/login/oauth/access_token` then `/api/userinfo`. The Casdoor-issued access token is used only to retrieve userinfo from Casdoor — it is never stored or accepted as a Bearer token by the control plane itself.

After the callback, the control plane mints its own local HS256 JWT via the same `security.create_access_token()` used by password login, and creates a `user_sessions` row. From this point the Casdoor user is indistinguishable from a local-password user at the API level.

There is no JWKS endpoint, no Casdoor JWT middleware, and no code path anywhere in the control plane that accepts a Casdoor-issued JWT as a Bearer credential. `security.py:48–50` confirms `decode_access_token` validates with the local `jwt_secret` using HS256 only.

#### 3. Agent keys (per-share write tokens)

Header format: `X-Agent-Key: tr_agent_<48 hex chars>`

Validator in `web.py:1137–1175`: SHA-256 hashes the header value, looks up the `share_agent_keys` table by `key_hash`, verifies: matching `share_id`, `revoked_at IS NULL`, `expires_at` not passed, `scopes` contains `"write"`. No JWT involved.

Scope: only valid for `POST /v1/web/shares/{slug}/upload`. No other endpoint accepts agent keys.

### Agent Keys CRUD — Current State

**Create** (`POST /v1/web/shares/{share_id}/agent-keys`, `agent_keys.py:19`):
- Body: `{"label": "optional string", "expires_at": "optional ISO datetime"}`
- Returns the raw key **once**: `{"id": "...", "key": "tr_agent_...", "label": ..., "expires_at": ..., "created_at": ...}`
- Scope is hardcoded to `"write"` at `agent_keys.py:110`.

**List** (`GET /v1/web/shares/{share_id}/agent-keys`):
- Returns `AgentKeyListItem` objects — never includes `key_hash` or the raw key value.

**Revoke** (`DELETE /v1/web/shares/{share_id}/agent-keys/{key_id}`):
- Soft-revoke: sets `revoked_at` timestamp. Row remains in DB.

**Auth for CRUD endpoints**: `_require_share_owner_or_admin` in `agent_keys.py:22–68` requires `Authorization: Bearer <local_cp_jwt>`. Caller must be global admin, share owner, or any `ShareMember` of the share. There is no further role restriction — a viewer-role member can create and revoke keys.

**Owner tracking gap**: `ShareAgentKey` model (`models.py:566`) has a nullable `created_by` FK to `users`, but `agent_keys.py:106–112` does not populate it at creation time. Keys have no recorded creator.

### Key Files

| File | Role |
|------|------|
| `apps/control-plane/app/api/routers/agent_keys.py` | Full CRUD, `_require_share_owner_or_admin` |
| `apps/control-plane/app/api/routers/oauth.py` | Casdoor OAuth callback flow |
| `apps/control-plane/app/api/routers/auth.py` | Local password auth |
| `apps/control-plane/app/api/deps.py` | `get_current_user`, `_get_user_from_token` |
| `apps/control-plane/app/core/security.py` | `decode_access_token` (HS256 only) |
| `apps/control-plane/app/api/routers/web.py:1093–1276` | `X-Agent-Key` validation on upload |
| `apps/control-plane/app/db/models.py:566–588` | `ShareAgentKey` model |
| `apps/control-plane/app/db/migrations/versions/202605220001_add_share_agent_keys.py` | Migration (landed 2026-05-22) |

### Full Endpoint Inventory

#### Auth (`/v1/auth/*`)

| Method | Path | Auth required |
|--------|------|---------------|
| POST | `/v1/auth/register` | admin JWT |
| POST | `/v1/auth/login` | none |
| POST | `/v1/auth/login/2fa` | none |
| POST | `/v1/auth/logout` | user JWT |
| GET | `/v1/auth/me` | user JWT |
| POST | `/v1/auth/refresh` | none (refresh token in body) |
| GET | `/v1/auth/sessions` | user JWT |
| DELETE | `/v1/auth/sessions/{session_id}` | user JWT |
| DELETE | `/v1/auth/sessions` | user JWT |
| POST | `/v1/auth/password-reset/request` | none |
| GET | `/v1/auth/password-reset/{token}` | none |
| POST | `/v1/auth/password-reset/confirm` | none |
| GET | `/v1/auth/email/verify/status` | user JWT |
| POST | `/v1/auth/email/verify/request` | user JWT |
| GET | `/v1/auth/email/verify/{token}` | none |
| GET | `/v1/auth/2fa/status` | user JWT |
| POST | `/v1/auth/2fa/enable` | user JWT |
| POST | `/v1/auth/2fa/verify` | user JWT |
| POST | `/v1/auth/2fa/disable` | user JWT |

#### OAuth (`/v1/auth/oauth/*`)

| Method | Path | Auth required |
|--------|------|---------------|
| GET | `/v1/auth/oauth/providers` | none |
| GET | `/v1/auth/oauth/{provider}/authorize` | none |
| GET | `/v1/auth/oauth/{provider}/callback` | none (OAuth code) |

#### Agent Keys

| Method | Path | Auth required |
|--------|------|---------------|
| POST | `/v1/web/shares/{share_id}/agent-keys` | local CP JWT (owner/member/admin) |
| GET | `/v1/web/shares/{share_id}/agent-keys` | local CP JWT (owner/member/admin) |
| DELETE | `/v1/web/shares/{share_id}/agent-keys/{key_id}` | local CP JWT (owner/member/admin) |

#### Upload (agent key consumption)

| Method | Path | Auth required |
|--------|------|---------------|
| POST | `/v1/web/shares/{slug}/upload` | `X-Agent-Key` header |

#### Shares (`/v1/shares/*`)

| Method | Path | Auth required |
|--------|------|---------------|
| GET | `/v1/shares` | user JWT |
| POST | `/v1/shares` | user JWT |
| GET | `/v1/shares/{share_id}` | optional JWT or share password |
| PATCH | `/v1/shares/{share_id}` | user JWT (owner/admin) |
| DELETE | `/v1/shares/{share_id}` | user JWT (owner/admin) |
| GET | `/v1/shares/{share_id}/members` | user JWT |
| POST | `/v1/shares/{share_id}/members` | user JWT (owner/admin) |
| PATCH | `/v1/shares/{share_id}/members/{user_id}` | user JWT (owner/admin) |
| DELETE | `/v1/shares/{share_id}/members/{user_id}` | user JWT (owner/admin) |

---

## Current State: Plugin Auth Model

### Two Operating Modes

#### Mode A: System 3 / PocketBase (cloud-hosted EVC relay)

Login is via OAuth2 redirect (Google, GitHub, Discord, Microsoft, OIDC). The plugin opens Obsidian's built-in webview, intercepts the OAuth redirect URI, and calls `pb.collection("users").authWithOAuth2Code(...)` (`LoginManager.ts:604–619`). No email/password form exists in this mode.

Token storage: `LocalAuthStore` (`src/pocketbase/LocalAuthStore.ts`), localStorage key `evc-team-relay_pocketbase_auth_<vaultName>`. Stored as `{token: "<PocketBase JWT>", model: {...}}`. Refreshed daily via `setInterval(..., 86400000)`.

#### Mode B: relay-onprem (self-hosted control plane)

Login is via email/password form (`RelayOnPremLoginModal.ts:147–201`) or OAuth2 browser redirect (`lines 221–258`), depending on what providers the control plane advertises at `GET /v1/auth/oauth/providers`.

Token storage: `RelayOnPremAuthStore` (`src/auth/RelayOnPremAuthStore.ts`), localStorage key `evc-team-relay_onprem_auth_<vaultName>_<serverId>`. Stored as `{user: AuthUser, token: "<access_token>", expiresAt: <ms>, refreshToken?: "<refresh_token>"}`.

Token lifecycle: access token expiry decoded from JWT `exp` claim or `expires_in` from login response. Background refresh via `ensureTokenRefreshed()`, triggered lazily from `getToken()` when token is within a 5-minute expiry buffer. Calls `POST /v1/auth/refresh` with `{refresh_token}` — 3 attempts with 0/1s/3s delays.

### Bearer JWT Availability in Plugin Memory

After login in relay-onprem mode, `RelayOnPremAuthProvider.token` holds the raw access token string in memory:

- `authProvider.getToken()` — synchronous, may return a stale token
- `authProvider.getValidToken()` — async, triggers refresh if needed; the correct method for any outbound API call

`LoginManager` exposes the active provider via `loginManager.getAuthProvider()` or `loginManager.getAuthProviderForServer(serverId)`.

The existing `RelayOnPremTokenProvider` already uses this pattern at line 137:

```typescript
const token = await this.config.authProvider.getValidToken();
// then: Authorization: `Bearer ${token}`
```

The JWT is already in plugin memory and already being sent as `Authorization: Bearer` to the control plane. No auth plumbing changes are required on the plugin side to support agent-key creation.

### All Current API Calls from Plugin

| Endpoint | Method | Auth |
|----------|--------|------|
| `<cp>/auth/login` | POST | none |
| `<cp>/auth/me` | GET | Bearer |
| `<cp>/auth/logout` | POST | Bearer |
| `<cp>/v1/auth/refresh` | POST | none (body) |
| `<cp>/v1/auth/oauth/<provider>/authorize` | GET | none |
| `<cp>/v1/auth/oauth/<provider>/callback` | GET | none |
| `<cp>/v1/auth/oauth/providers` | GET | none |
| `<cp>/shares` | GET/POST | Bearer |
| `<cp>/shares/<id>` | GET/PATCH/DELETE | Bearer |
| `<cp>/shares/<id>/members` | GET/POST | Bearer |
| `<cp>/shares/<id>/members/<uid>` | DELETE/PATCH | Bearer |
| `<cp>/shares/<id>/invites` | GET/POST | Bearer |
| `<cp>/shares/<id>/invites/<id>` | DELETE | Bearer |
| `<cp>/users/search?email=<email>` | GET | Bearer |
| `<cp>/server/info` | GET | none |
| `<cp>/tokens/relay` | POST | Bearer |
| `<cp>/v1/web/shares/<slug>/files` | POST | Bearer |
| `<cp>/v1/billing/plan` | GET | Bearer |
| `<cp>/v1/billing/plans` | GET | none |
| `<cp>/v1/billing/checkout` | POST | Bearer |
| `<cp>/v1/billing/change-plan` | POST | Bearer |
| `<cp>/v1/billing/cancel` | POST | Bearer |
| `<cp>/v1/billing/portal` | POST | Bearer |

### Key Plugin Files

| File | Role |
|------|------|
| `src/auth/RelayOnPremAuthProvider.ts` | Auth logic, token lifecycle, `getValidToken()` |
| `src/auth/RelayOnPremAuthStore.ts` | localStorage persistence |
| `src/auth/IAuthProvider.ts` | Interface contract |
| `src/RelayOnPremShareClient.ts` | All CP API calls — add new `createAgentKey()` method here |
| `src/components/RelayOnPremSettings.svelte` | Nav shell — add new view entry here |
| `src/components/RelayOnPremServerList.svelte` | Server list — add "Agent Keys" button here |
| `src/ui/RelayOnPremLoginModal.ts` | Login modal (reference for modal patterns) |

---

## UX Gap Analysis

### Q1: Can a Casdoor-only user (e.g. rj@entire.vc) currently create an agent key without a local password?

**Yes.** No local password is required. The sequence:

1. `GET /v1/auth/oauth/casdoor/authorize?redirect_uri=...` — redirect to Casdoor
2. User authenticates in Casdoor — callback `GET /v1/auth/oauth/casdoor/callback?code=...&state=...`
3. Control plane exchanges the code, fetches userinfo, creates or finds the user record, then calls `security.create_access_token()` to mint a local HS256 JWT and a refresh token stored in `user_sessions`.
4. That local JWT can be used immediately as `Authorization: Bearer <token>` to call `POST /v1/web/shares/{share_id}/agent-keys`.

**What would block them** (both are non-issues for a known account):
- Using the Casdoor-issued JWT directly as a Bearer header fails — `security.decode_access_token` uses HS256 + local `jwt_secret`; a Casdoor JWT uses RS256/Casdoor's private key and will produce `InvalidTokenError` → 401.
- If `oauth_auto_register=false` and no existing account — 403 at `oauth.py:200`. For `rj@entire.vc` this does not apply.

Programmatic use (without a browser) is also possible: the OAuth callback returns a JSON `OAuthCallbackResponse` when `Accept: application/json` is set or when no `return_url` is present in state.

### Q2: Where does `Authorization: Bearer` come from for the agent-keys API right now?

The Bearer token accepted by `POST/GET/DELETE /v1/web/shares/{share_id}/agent-keys` is a **locally-minted HS256 JWT** issued by `apps/control-plane/app/core/security.py` — specifically `security.create_access_token()`. Every auth path converges here before any agent-key operation:

- Local password login → `auth.py` → `security.create_access_token()` → Bearer JWT
- Casdoor OAuth callback → `oauth.py:200–210` → same `security.create_access_token()` → Bearer JWT
- Any future OIDC provider → same convergence point

The validator is `_require_share_owner_or_admin` in `agent_keys.py:22–68`, which calls `security.decode_access_token(token)` — HS256, local `jwt_secret`, no JWKS, no Casdoor middleware.

On the plugin side, `RelayOnPremAuthProvider.getValidToken()` returns this same locally-minted token. All authenticated calls in `RelayOnPremShareClient` attach it via `getHeaders()` (`RelayOnPremShareClient.ts:291–300`). Adding a `createAgentKey()` method requires only a new `customFetch` call using the existing `getHeaders()` helper — no auth changes.

### Q3: What can a regular user (non-admin) currently see/do regarding agent keys?

Any authenticated user who is a **share member at any role level** (including viewer) can:
- `POST /v1/web/shares/{share_id}/agent-keys` — create a new agent key, receiving the raw key value once
- `GET /v1/web/shares/{share_id}/agent-keys` — list all keys on the share (metadata only, no raw values)
- `DELETE /v1/web/shares/{share_id}/agent-keys/{key_id}` — soft-revoke any key on the share

There is no role hierarchy enforcement within the share for key management. `_require_share_owner_or_admin` is misleadingly named — it actually accepts any `ShareMember`, not only owners or admins. A viewer-role member can create and revoke keys with the same permissions as the share owner.

**Implication**: this is a permissions gap that should be addressed in a follow-up. Key creation and revocation should likely be restricted to share owners and global admins, not all members.

**What a regular user cannot do**:
- Retrieve the raw key value after creation (list endpoint omits it)
- Use agent keys against any endpoint other than `POST /v1/web/shares/{slug}/upload`
- Create account-scoped keys (no such concept exists yet — all keys are per-share)

### Q4: Recommended placement — where should "create agent key" live — plugin settings vs web UI?

**Plugin settings is the correct primary surface.** The web UI is not a blocker and should be deferred.

Rationale:
- The Bearer token is already in plugin memory — zero auth plumbing needed.
- Agent keys are consumed by agent configurations, not by web users. The plugin is where an agent-integrating user already is when they configure shares and connections.
- Obsidian plugins conventionally put credential management in Settings. This is where technically-minded users expect to find it and where documentation points.
- A dedicated web UI adds a second place to manage the same resource, creating a split-brain problem if the two surfaces show different state.

Implementation path: new `createAgentKey(label: string, expiresAt?: string)` method on `RelayOnPremShareClient` calling `POST /v1/web/shares/{share_id}/agent-keys` with `await this.getHeaders()`. New `AgentKeysView.svelte` component added to `RelayOnPremSettings.svelte`'s view union. Entry point button in `RelayOnPremServerList.svelte` alongside the existing "Open Shares" and "Billing" buttons.

---

## Prior Art: API Key UX Patterns

### Comparison Table

| Aspect | Notion | Linear | Anthropic Console | GitHub |
|--------|--------|--------|-------------------|--------|
| **Location in UI** | Settings → Connections → "Develop or manage integrations" (workspace-level); also notion.so/my-integrations | Settings → Account → API (personal settings, not workspace admin) | Console dashboard → API Keys (top-level nav item, prominent) | Settings → Developer settings → Personal access tokens (buried 3 levels deep) |
| **Scope model** | Per-workspace integration, then per-page/database "connection" grant required | Per-account; one key works across all teams the user belongs to | Per-workspace (org-scoped); keys belong to org, not individual | Per-account (classic PAT); or per-repository / fine-grained (owner + resource + permission matrix) |
| **Key naming** | Human name + optional description + logo; name mandatory | User-provided label; no description | User-provided label; optional description; shows creation date | Classic: user-provided note (required). Fine-grained: name + expiry + resource scope — structured wizard |
| **One-time reveal** | Shown once in a modal immediately after creation, with a copy button | Shown once on creation page with copy button; navigating away loses it | Shown once after creation with "Copy" button and warning banner; not re-shown | Shown once on confirmation page; green "Copy" button; banner says it won't be shown again |
| **Revocation** | List with "Delete" per integration; no bulk; permanent and disconnects all pages | List with trash icon per key; no bulk | List with "Revoke" action per key; shows last-used date | Classic: "Delete". Fine-grained: "Revoke"; can regenerate; no native bulk; expiry dates visible |
| **Re-auth for create** | No re-auth; must be workspace admin or developer role | No re-auth; any member can create their own key | No re-auth at creation; workspace admin can restrict creation | Classic: no re-auth. Fine-grained: password confirmation if session is old (soft re-auth) |
| **Rate limits / quotas shown** | Not shown in key UI | Not shown in key UI | Usage dashboard on a separate page; tier shown in billing, not per-key | Rate limit tier in API response headers, not in PAT UI; no quota meter in settings |

### Product-by-Product Notes

**Notion**: Splits authorization into two layers — create the integration key, then separately grant it access to specific pages. The key alone is inert until a page "connects" it. Good for data hygiene; creates friction for non-technical users. The management page (notion.so/my-integrations) lives outside the main Settings flow, causing discoverability problems.

**Linear**: The cleanest minimal implementation. Single settings screen, no wizard, no scope configuration. The assumption is that the key inherits the account holder's permissions — no per-resource scoping. Fast to create, low cognitive overhead. Downside: no granularity for read-only keys.

**Anthropic Console**: The most prominent placement — API Keys is a first-class top-level nav item, not buried in settings. This reflects that key creation is the primary reason users visit the console. Workspace-level scoping is appropriate for a developer tool where the "account" is typically a team billing entity. The last-used timestamp per key is genuinely useful for audit and cleanup. No per-key rate limit display — usage is aggregated at workspace level on a separate page.

**GitHub**: The fine-grained PAT is the most sophisticated scope model: owner → specific repositories → permission matrix per capability. Powerful but creates significant UI complexity. Classic PATs are the opposite: broad scopes, simple checklist, fast. GitHub is alone in offering token regeneration (fine-grained), which allows key rotation without disrupting integrations that have received the value. Password re-confirmation for fine-grained tokens on stale sessions is a lightweight soft-2FA substitute.

---

## Recommendations

### 1. Where to expose key management

**Primary surface: Obsidian plugin settings, dedicated "Agent Keys" section per server.**

This matches Linear and Anthropic Console — a dedicated section easy to explain, document, and link to from integration guides. Settings is where credential-holding users expect to find credentials in Obsidian.

Implementation in plugin:
- New view `"agentKeys"` added to the `ViewType` union in `RelayOnPremSettings.svelte`.
- New `AgentKeysView.svelte` component: lists keys (name, scope, created, last-used, revoke button); "Create key" button opens a modal.
- Entry point button in `RelayOnPremServerList.svelte` alongside the existing "Open Shares" and "Billing" buttons.

**Secondary surface: contextual shortcut in the share detail view.**

When a user is configuring a specific share, surface a "Create agent key for this share" button inline. Clicking it opens the same creation modal, pre-scoped to that share. This mirrors the Notion pattern (connecting integrations at the resource level) but in reverse — creation from context, not from a global list.

This dual-surface design eliminates the Notion discoverability problem while keeping a canonical list in Settings.

**Deferred: web UI.** A web management page for agent keys adds maintenance surface and creates a split state problem. Build it only when there is explicit demand from users who do not use the Obsidian plugin (e.g. API-only consumers of the self-hosted relay).

### 2. Scope model

**Per-share scope as the default, with account-scope as a deliberate advanced option.**

Per-account keys that grant access to everything are a footgun in agent contexts. A single leaked key or misbehaving agent touches the entire vault history. Per-share scope limits blast radius.

Recommended hierarchy:
- **Share-scoped key** (default, encouraged): grants `write` access only to the specified share. Scope is set at creation and is immutable.
- **Account-scoped key** (advanced): grants access to all current and future shares. Gated behind an explicit confirmation step ("This key will access all your shares — are you sure?"). Displayed with a warning badge in the key list.

This maps to GitHub's fine-grained PAT philosophy: specific-by-default, broad by explicit choice.

Note: scope is enforced server-side by the relay/MCP ACL layer, not solely by the key. The key is a credential; the share ACL is the policy. Keep these concerns separate — do not try to encode policy inside the key itself.

**Immediate follow-up**: restrict key creation and revocation to share owners and global admins. The current `_require_share_owner_or_admin` validator accepts any `ShareMember`, including viewer-role. This should be tightened on the backend before shipping a UI that implies ownership semantics.

### 3. One-time reveal

Follow the Anthropic Console and GitHub pattern, adapted for Obsidian modal constraints:

1. On key creation, open a dedicated `Modal` (not a toast, not inline text). Display the key value in a monospace `<input>` or `<code>` element.
2. Show a prominent one-time warning: "This key will not be shown again. Copy it now."
3. Single "Copy to clipboard" button with a visual confirmation state (changes to "Copied ✓" for 2 seconds).
4. Optionally offer "Download as .txt" — useful for users who want to store it in a password manager without clipboard exposure.
5. Dismiss requires an explicit "I've copied this key" button, not a generic close. This forces acknowledgment.

Do not re-reveal the key anywhere after dismissal. The key list shows only: name, share scope, created date, last-used date (if the backend tracks it — currently it does not; worth adding a `last_used_at` column to `share_agent_keys`), and revoke action.

**Never store the raw key value in plugin settings storage.** Store only the key ID, name, and share scope. The user is responsible for storing the secret in their password manager or agent config file.

### 4. Security constraints and re-auth

**Soft re-auth for key creation; no re-auth for revocation.**

Key creation is a higher-stakes action: a stolen session that creates a key and passes it to an external agent creates a persistent threat that outlasts the stolen session. A stolen session that revokes keys is disruptive but self-limiting and immediately visible.

Implementation with Casdoor SSO:
- On "Create Agent Key": check the Casdoor session age. If the session token was issued more than 30 minutes ago, require the user to re-authenticate via Casdoor before the creation form appears. Use OIDC `prompt=login` or `max_age` parameter — not a full logout/login cycle.
- Surface this as: "Creating agent keys requires confirming your identity. You'll be redirected to sign in." — neutral phrasing, not a security warning.
- On "Revoke": no re-auth. Revocation is a protective action; adding friction here discourages timely cleanup.

If Casdoor is unavailable (offline vault, local-only mode): skip re-auth silently. The threat model for an offline Obsidian vault is different, and blocking key creation when SSO is unreachable degrades a legitimate use case.

This mirrors GitHub's fine-grained PAT behavior (soft password prompt on stale session) without requiring a separate credential-confirmation UI.

### 5. Open follow-up items

| Item | Priority | Owner |
|------|----------|-------|
| Restrict agent-key CRUD to share owners/admins (fix `_require_share_owner_or_admin`) | High | Gandalf |
| Populate `created_by` FK on key creation (`agent_keys.py:106–112`) | Medium | Gandalf |
| Add `last_used_at` column to `share_agent_keys` table and update on upload | Medium | Gandalf |
| Add `createAgentKey()` method to `RelayOnPremShareClient.ts` | High | Gandalf |
| Build `AgentKeysView.svelte` + entry point in `RelayOnPremServerList.svelte` | High | Gandalf |
| Soft re-auth gate via OIDC `max_age` on key creation | Medium | Gandalf |
| Account-scoped key support (backend + UI) | Low | Backlog |
| Web UI for agent key management | Low | Backlog |
