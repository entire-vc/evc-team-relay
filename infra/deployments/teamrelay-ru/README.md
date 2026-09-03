# teamrelay.ru deployment

Second Team Relay prod instance, for the .ru market. Host: alyssa
(161.104.58.170), SSH alias `tr-ru-vm`.

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

## Drift guard

`check-drift.py` in this directory compares the live host config against
this repo. Run manually or via cron; see the script's own `--help`.
