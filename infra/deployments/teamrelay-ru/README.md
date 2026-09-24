# teamrelay.ru deployment

A second Team Relay production instance, for the .ru market. It runs on its own
host, reached through the SSH alias `tr-ru-vm` — an alias, not a hostname: define
it in your own `~/.ssh/config`. Everything below is relative to that host.

## Layout on the host (`/opt/relay/`)

```
/opt/relay/
  repo/                      <- this repo, git clone (read-only checkout, `git pull` to update)
    infra/Caddyfile          <- shared, parameterized (see infra/env.example)
    infra/deployments/teamrelay-ru/
      docker-compose.yml
      docker-compose.override.yml
      .env.sops              <- encrypted secrets, tracked in git
  .env                       <- decrypted from .env.sops at deploy time, gitignored, NEVER committed
  Caddyfile -> repo/infra/Caddyfile   (symlink)
  control-plane-src/         <- outside the checkout; CI rsyncs here (see scripts/deploy.sh)
  web-publish-src/
  backup-build/
  data/, relay/, metrics-proxy/       <- host-local runtime state, not git-tracked
```

## Editing config

Caddyfile / compose changes: edit in a clone of this repo, open an MR, merge,
then `git -C /opt/relay/repo pull` on the host. **Do not hand-edit
`/opt/relay/repo/infra/Caddyfile` on the host** — that produces exactly the
untracked-drift class this deployment layout exists to avoid.

## Editing secrets

```bash
ssh tr-ru-vm
export SOPS_AGE_KEY_FILE=/root/.config/sops/age/keys.txt
cd /opt/relay/repo
sops infra/deployments/teamrelay-ru/.env.sops   # opens $EDITOR, re-encrypts on save
```

Then re-decrypt to the runtime `.env` and restart the affected service(s):

```bash
sops -d infra/deployments/teamrelay-ru/.env.sops > /opt/relay/.env
chmod 600 /opt/relay/.env
```

The age private key lives ONLY on this host (`/root/.config/sops/age/keys.txt`,
root-only, 600) — it is not copied anywhere else. Public key is in
`.sops.yaml` in this directory.

## Deploy invocation

```bash
docker compose --project-directory /opt/relay \
  --env-file /opt/relay/.env \
  -f /opt/relay/repo/infra/deployments/teamrelay-ru/docker-compose.yml \
  -f /opt/relay/repo/infra/deployments/teamrelay-ru/docker-compose.override.yml \
  <command>
```

`--project-directory /opt/relay` makes every relative path in the compose
files (`./Caddyfile`, `./data/*`, `./relay/relay.toml`, `./.env`, etc.)
resolve against the real runtime directory, not wherever the compose YAML
happens to sit inside the git checkout — the `Caddyfile` symlink above is
what makes `./Caddyfile` land on the git-tracked file.

## MinIO images

`minio` and `minio-init` use `git.entire.host:5050/entire-vc/evc-team-relay/minio`
and `.../mc`, pinned by digest in `docker-compose.yml`. They are not pulled
from Docker Hub or quay.io: both refuse anonymous pulls of MinIO now (401, same
answer from this host and from the EN one), and that will not come back on its
own. The mirrored images are the `RELEASE.2025-09-07T16-13-09Z` linux/amd64
build and `mc:latest` of the same date, the same bytes this host has been
running.

The registry is private, so the host needs a login once:

```bash
# fields TR_RU_REGISTRY_USER / TR_RU_REGISTRY_TOKEN: a read_registry-only deploy token
docker login git.entire.host:5050 -u "$TR_RU_REGISTRY_USER" --password-stdin
```

To move to a newer MinIO, mirror it from a machine that can still reach a
source, push it as a new tag, and change the digest in the compose file (the
registry keeps the old one, so a rollback is a one-line revert):

```bash
docker pull --platform linux/amd64 <source>/minio:<RELEASE>
docker tag  <source>/minio:<RELEASE> git.entire.host:5050/entire-vc/evc-team-relay/minio:<RELEASE>
docker push --platform linux/amd64 git.entire.host:5050/entire-vc/evc-team-relay/minio:<RELEASE>   # prints the digest to pin
```

## Email language

System emails on this deployment are Russian: the compose file sets
`EMAIL_LOCALE=ru` on `control-plane` (billing and account emails) and
`lifecycle-worker` (the data-deletion confirmation). `email-worker` only sends
what was already rendered, so it does not need it. Russian bodies live in
`apps/control-plane/app/templates/emails/ru/`; a template missing there falls
back to the English one. Do not set `EMAIL_LOCALE` in `.env.sops`: the compose
value would override it anyway.

## Drift guard

`check-drift.py` in this directory compares the live host config against
this repo. Run manually or via cron; see the script's own `--help`.
