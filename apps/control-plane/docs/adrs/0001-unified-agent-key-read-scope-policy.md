# ADR-0001: one read-scope policy for agent keys — literal, not `write ⊃ read`

**Status:** accepted 2026-08-19. Phase 1 shipped; phase 2 (withdrawing the grace) pending the
migration below.
**Context:** Mesh task [#b69d73fb](http://mesh.entire.host/t/b69d73fb-d219-4fe2-b61a-01aeb6a8d6dd),
found by Daedalus while independently checking the API answer given in
[#f87681cb](http://mesh.entire.host/t/f87681cb-0a79-4eed-8477-eaa46ad85cad).

## Decision

**A scope is satisfied only by itself. `write` does not imply `read`.** This holds on every route
that serves share content to an agent key. The policy lives in one place —
`app/core/agent_key_scopes.py` — and all three auth helpers in `web.py` call it.

Two coupled changes ship with it:

1. **New keys default to `["read","write"]`**, not `["write"]`.
2. **`PATCH /v1/web/shares/{share_id}/agent-keys/{key_id}`** can change an existing key's scopes
   without rotating the secret.

Both are explained under *Consequences*; neither is cosmetic.

## The defect

Two functions answered the same question differently, on route families that return the **same
bytes**:

| helper | policy | routes |
|---|---|---|
| `_require_private_web_auth` | `write ⊃ read` | `GET /v1/web/shares/{slug}`, `/files`, `/assets` |
| `_auth_share_read_access` | literal `read` | `GET /v1/web/shares/{id\|slug}/download`, `/files-index` |

So one key, on one share, read a document through `/files` and got
`403 Agent key does not have read scope` from `/download`. From outside, that 403 reads as
"the endpoint doesn't exist" — which is exactly how it was reported to us.

Reproduced end-to-end before any fix (write-only key, published private share):

```
200  GET /v1/web/shares/{slug}
200  GET /v1/web/shares/{slug}/files      <-- returned the document body
403  GET /v1/web/shares/{id}/files-index  Agent key does not have read scope
403  GET /v1/web/shares/{id}/download     Agent key does not have read scope
```

The split was not visible in production data, because only `_auth_share_read_access` stamped
`last_used_at`. A key reading through `/files` looked permanently unused. Confirmed live on prod
against `cp.tr.entire.vc` with a `read,write` key: three lenient reads left `last_used_at` at
`14:57:43`; one `/download` moved it to `14:58:10`.

## Why literal, and not the cheaper direction

Loosening `_auth_share_read_access` to `write ⊃ read` was the cheap, one-line option. It is the
wrong one, and the reason is not taste — it is which shares each route family can reach.

The lenient routes resolve a share by `(web_slug, web_published == True)`. On a share that was
never web-published they **404 by construction**, whatever the key's scopes. The strict routes are
UUID-addressable and reach those shares.

That asymmetry splits the population in two:

- **Published shares** — a write-only key already reads everything through the lenient routes. The
  "write-only means it can't see my vault" guarantee is *already false* here, and the strict check
  on `/download` buys nothing, because the identical bytes are one lenient call away.
- **Unpublished shares** — a write-only key can read **nothing**: 404 from the lenient routes, 403
  from the strict ones. Here the guarantee is real and load-bearing today.

So loosening would grant write-only keys read access to unpublished private vault content they
cannot see today. Measured on prod: **5 of the 7 live write-only keys sit on unpublished shares,
and 4 of those belong to external customers.** Unifying downward would hand those customers'
drop-box integrations read access to vaults their owners never published. That is a security
regression, not a consistency fix.

Tightening has the opposite profile. It only bites keys that lack a literal `read` *and* read
through the lenient routes — which requires a published share:

```
label            | share    | published | last_used_at        | creator
nhbot            | neurohub | t         | (never)             | pavel@venture-crew.com
mesh-spark-sync  | spark    | t         | 2026-08-11 06:33    | pavel@venture-crew.com
```

**Blast radius: two keys, both ours, one never used.** No external customer key is affected.

(`last_used_at` on a write-only key necessarily records a *write*: the scope check in
`_auth_share_read_access` raises before the timestamp is written, and the lenient routes did not
write it at all. So `mesh-spark-sync`'s timestamp is an upload, not a read.)

## Why the creation default changes too

The Obsidian plugin is the **only** user-facing way to create an agent key, and it does not send
`scopes` at all — its `CreateAgentKeyRequest` carries `label` and `expires_at` and nothing else
(`RelayOnPremShareClient.ts`, and both the modal and the Svelte view). The server default is
therefore what every key created through the product gets.

With the old `["write"]` default, a user who created a key in the plugin so their agent could read
their vault got a key that **cannot** read: the MCP server's `read_file` goes to
`GET .../download` and was answered `Agent key does not have read scope`. That is the whole
provenance of the seven write-only keys — nobody chose write-only, it was never offered.

Leaving the default alone while tightening the policy would convert a partial failure into a total
one. So the default becomes `["read","write"]`, matching what 18 of 25 live keys already carry and
what the only UI's users actually want.

This is not "loosening as part of a tightening". A drop-box key remains available and now requires
`{"scopes": ["write"]}` — an explicit act, which is the right shape for a deliberate restriction.
The alternative, making `scopes` required, would break the plugin outright and was rejected for
that reason.

## Migration — expand → migrate → contract

Tightening is breaking for whoever relies on today's leniency, so it does not ship in one step
(`CLAUDE-workflow.md` §1b).

**Phase 1 (shipped).** Policy unified in one module; both helpers call it. The lenient routes keep
their current verdicts behind `AGENT_KEY_LENIENT_READ_GRACE=true` (default), and every call that
passes *only* because of that grace logs a WARNING naming the key, share and route
(`event=agent_key_read_scope_grace`). `last_used_at` is now stamped on the lenient routes too. **No
route changes its verdict in this phase.** The grace is deliberately *not* applied to the strict
routes — that would be the downward unification rejected above.

**Phase 2 (migrate).** The share owner grants `read` to the keys the WARNING names — today,
`mesh-spark-sync` and `nhbot`:

```bash
curl -X PATCH "https://cp.tr.entire.vc/v1/web/shares/<share_id>/agent-keys/<key_id>" \
  -H "Authorization: Bearer <owner JWT>" -H "Content-Type: application/json" \
  -d '{"scopes":["read","write"]}'
```

`PATCH` exists precisely so this needs no key rotation. The previous route — revoke and reissue —
changes the raw secret and so forces reconfiguring whatever integration holds it; that cost is high
enough that owners would rationally skip the migration and meet the breakage later instead.

**Phase 3 (contract).** Set `AGENT_KEY_LENIENT_READ_GRACE=false`. Gate: the WARNING has been silent
for a full observation window. Reverting is a config change, not a redeploy.

## Consequences

- One policy, one module. A fourth helper cannot quietly invent a fifth answer.
- Agent-key reads are now measurable. "Is anything reading with a write-only key?" became
  answerable at all — previously the data could not represent it.
- `PATCH .../agent-keys/{key_id}` is a new authorization surface. It is owner/admin-only, matching
  create and revoke, and refuses revoked keys so that granting scopes cannot undo a revocation.
- Known consumer of the leniency: `evc-mesh`'s `SearchDocs` and `ListShareFiles`
  (`internal/integration/teamrelay/`) call `GET /v1/web/shares/{slug}` with `X-Agent-Key`. They are
  safe once their configured key carries `read` — which is what phase 2 does.

## Verification

Tests live in `tests/test_agent_key_read_scope_policy.py` and drive **routes, not helpers** — a
helper-level test could not have caught this defect, since each helper was self-consistent and the
bug existed only in the gap between them. The central case asserts a property across the whole set
of read routes: for any given key they must all return the same verdict.

Each test was mutation-checked against the source: reverting the lenient helper, leaking grace into
the strict helper, dropping the `last_used_at` stamp, and restoring the old creation default each
turn the intended tests red.
