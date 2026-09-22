# Running Team Relay behind an external TLS-terminating proxy or CDN

This page covers one general scenario: **something other than the bundled Caddy
already terminates TLS in front of this host** — a reverse proxy on another
machine (Traefik, nginx, HAProxy), a cloud load balancer, or a CDN in
flexible-SSL mode (e.g. Cloudflare). Traefik is used for the example commands
below only because it is a common choice; nothing here is Traefik-specific.

It also covers the layout most installs in this situation end up with:
**every service on its own subdomain, nothing served on the bare domain.**

## 1. Subdomains are the standard layout, not a workaround

| Public hostname      | Served by                      | Required?                        |
| --------------------- | ------------------------------- | --------------------------------- |
| `cp.{DOMAIN_BASE}`    | control plane (API + admin UI) | yes                                |
| your `RELAY_DOMAIN`   | relay server (sync, WebSocket) | yes                                |
| `WEB_PUBLISH_DOMAIN`  | web publishing                 | only if you use published docs    |
| bare `DOMAIN_BASE`    | `SITE_DOMAIN` / `BARE_REDIRECT_DOMAIN`, or nothing | no, see below |

Three things worth being explicit about, because none of them are obvious from
the `.env` file alone:

- **`cp.` is fixed and cannot be renamed.** The control plane is always served
  at `cp.` + whatever you set as `DOMAIN_BASE` — so `DOMAIN_BASE` has to be your
  base domain even if nothing is ever served on it directly.
- **`RELAY_DOMAIN` is free-form.** It has no default and does not have to share
  a suffix with anything else; put the relay on any subdomain you like (or, on
  a single-host default install, on the bare domain itself).
- **The bare domain binds nothing by default.** If you leave both `SITE_DOMAIN`
  and `BARE_REDIRECT_DOMAIN` unset, Caddy does not listen for the bare domain at
  all — you do not need a DNS record for it, and there is nothing to configure.
  Set `SITE_DOMAIN` if you want to serve a marketing/landing site there instead,
  or `BARE_REDIRECT_DOMAIN` to redirect it elsewhere.

## 2. Two ways to sit behind your proxy

**Recommended: keep the bundled Caddy, and put your proxy in front of it.**
Caddy's own `infra/Caddyfile` documents this exact mode at the top of the file
— prefix every site address with `http://`, add `auto_https off` as a guard,
and keep `import security_headers`. Do that and Caddy serves plain HTTP on
:80 while your proxy owns TLS; everything else in this guide still applies
unchanged, including all of §3, because Caddy is still the thing reproducing
that contract for you.

Skip that prefix and Caddy still tries to obtain a certificate for the site,
which a proxy forwarding to :80 doesn't match — the domain answers with an
**empty 200**, which reads as success and is not. This is the single most
common way a "behind a proxy" setup breaks, and it took one of our own
domains down for hours the first time we hit it.

**Alternative: remove Caddy and route directly to the containers.** This
works, but it means your own proxy has to reproduce the contract in §3 below
by hand — nothing enforces it for you, and the part that's easy to miss does
not fail loudly.

## 3. If you remove Caddy: the contract your proxy must reproduce

This is the part worth reading closely even if you skim the rest of this page.

The relay server authenticates WebSocket connections with a signed token.
Browser clients can't set a custom header on a WebSocket handshake, so the
token travels as a `?token=` query parameter instead — and Caddy's relay
block does one non-obvious rewrite on top of that:

- **For plain HTTP relay endpoints**, move `?token=` out of the query string
  and into an `Authorization: Bearer <token>` header.
- **For the WebSocket upgrade paths — `/doc/ws/*` and `/d/*/ws/*` —
  deliberately do NOT do that rewrite.** Those handlers read the token only
  from the query string, never from a header. Apply the same rewrite there
  and you turn a valid request into a missing-token failure; this is the
  single most common defect in a hand-rolled proxy config, because it's the
  same rule as the line above except for two paths, and it fails silently on
  a connection that otherwise looks fine.

The rest of the contract, all standard reverse-proxy behaviour but worth
checking explicitly:

- **Pass the original `Host` header through unchanged.** Routing on the
  backend depends on it; rewrite it and every request lands on no site,
  producing the same empty-200 symptom as §2.
- **Allow WebSocket upgrades** (forward `Upgrade` / `Connection`) on the relay
  hostname.
- **Do not touch the query string** on relay paths, beyond the one rewrite
  above.
- **Set generous idle/read timeouts.** Sync connections are long-lived;
  a proxy default tuned for short HTTP requests will cut them off.
- **Route each hostname to the right backend** — the control plane, the relay
  server, and (if enabled) web publishing are three different services.
- **Allow request bodies up to 25MB on the control-plane hostname.** Vault
  attachments (images, PDFs, anything embedded with `![[...]]`) upload
  through the control plane, not directly to storage — see §5 below for why —
  so a proxy default tuned for small JSON bodies (often 1MB) will reject
  anything past that with no explanation on the Obsidian side beyond "sync
  failed". The bundled Caddy already sets this; a proxy that replaces it
  needs the same ceiling.

## 4. Don't publish the container ports directly

In the shipped `docker-compose.yml`, only Caddy publishes a host port —
`control-plane` (internal port `8000`), `relay-server` (internal port `8080`)
and `web-publish` (internal port `3000`) publish none. Keep it that way even
if you replace Caddy: if those ports end up published on a host with a public
IP, the control plane and the relay are reachable straight from the internet
on `8000`/`8080`/`3000`, bypassing whatever proxy or auth you put in front of
them. Route everything through your proxy → your proxy's own entry point
(Caddy, or your own if you removed it) → the internal Docker network.

## 5. Values that must agree

|                                | Value |
| ------------------------------ | ----------------------------------------------------- |
| `.env` → `RELAY_DOMAIN`         | your relay's hostname |
| `.env` → `RELAY_PUBLIC_URL`     | `wss://` + that same hostname |
| `.env` → `RELAY_AUDIENCE`       | leave unset — it's then derived from `RELAY_PUBLIC_URL`'s host and can't drift from it |
| `relay/relay.toml` → `[server].url` | `https://` + that same hostname |
| your proxy's route for the relay | matches that same hostname |
| `.env` → `CONTROL_PLANE_PUBLIC_URL` | `https://` + your control-plane hostname (`cp.` + `DOMAIN_BASE`, §1) |

Same host in every one of these. A single mismatched character — a missing
dot, a trailing slash, `http` vs `https` — rejects every relay token, and
nothing about the failure is loud: tokens are issued and signed correctly,
the WebSocket handshake completes, and the rejection happens silently on the
first message. Leaving `RELAY_AUDIENCE` unset removes one of the five places
this can drift.

**`CONTROL_PLANE_PUBLIC_URL` is a row of its own, not just another instance
of "same host":** it doesn't need to match the relay's hostname (it's a
different service, on its own `cp.` subdomain per §1) — it needs to match
*itself*, i.e. be genuinely reachable at the address you set it to. It has a
placeholder default (`http://localhost:8000`) that only makes sense from
inside the compose network, and the control plane logs a startup warning if
it's still set to that placeholder — but a self-hoster who never reads
startup logs (most people run this detached) can go a long time without
noticing. The externally-visible symptom is specific and easy to
mis-diagnose as a proxy problem: sign-in and document sync both work fine
(they don't depend on this value), but vault attachments fail outright,
because file-token responses carry this URL as their `base_url` and the
Obsidian plugin's HEAD/upload-url/download-url calls all go straight to it. A
control plane serving vault attachments no longer routes those bytes through
raw storage (this used to be a presigned MinIO URL, unreachable unless you
also publish MinIO itself — see the code comments on `get_file_download_url`/
`get_file_upload_url` in `apps/control-plane/app/api/routers/shares.py` if
you're touching that code), so as long as this one value is set correctly,
nothing about your storage backend needs to be exposed at all.

## 6. Verify by outcome, not by health check

A `200` from every hostname proves your proxy is routing correctly. It proves
nothing about whether documents actually sync — that path can be entirely
broken while every health check stays green, because health checks don't
touch it. Two checks that do:

```bash
# 1. Auth rejections should stay flat while you use the app
docker compose exec relay-server \
  curl -s http://localhost:9090/metrics | grep -i auth_error

# 2. Objects should actually land in storage — count, edit a shared note, count again
docker compose run --rm --entrypoint sh minio-init -c \
  'mc alias set l http://minio:9000 "$MINIO_ROOT_USER" "$MINIO_ROOT_PASSWORD" \
   && mc ls --recursive l/relay | wc -l'
```

The counter staying flat and the object count growing after an edit is what
a working sync path looks like.

> **Note on `caddy validate`:** if you're editing the Caddyfile for §2, running
> `caddy validate --config Caddyfile --adapter caddyfile` against it confirms
> the file's syntax is correct. It does **not** confirm the absence of the
> empty-200 symptom in §2 — that's prevented by the `http://` prefix and
> `auto_https off`, not by anything a syntax validator checks. Don't treat a
> clean `validate` run as proof the proxy mode itself is configured correctly.

---

**See also:** [Configuration reference](configuration.md#reverse-proxy-caddy)
for the WebSocket token proxy in more detail, and
[Installation → Configure Relay Server](installation.md#3-configure-relay-server)
for the base install this page assumes.
