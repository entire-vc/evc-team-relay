# Team Relay — Deployment Runbook

> **Rule §1b** (CLAUDE-workflow.md §1b Deploy Discipline): migrations MUST run and succeed
> BEFORE the app image starts. Failed migration = blocked deploy, never the reverse.

## Overview

Team Relay is deployed on `tw-relay` via Docker Compose. Production images are built locally
on the server from synced source (`/opt/relay/control-plane-src/`), not pulled from a registry.

The `control-plane-migrate` service in `docker-compose.yml` is the **fail-closed gate**: it runs
`alembic upgrade head` and must exit 0 before `control-plane` (or any worker) starts.

---

## Standard upgrade (migration + app restart)

```bash
# 1. SSH to server
ssh tw-relay
cd /opt/relay

# 2. Tag the current image BEFORE overwriting (enables fast rollback)
docker tag infra-control-plane:latest infra-control-plane:prev

# 3. Build new image from updated source
docker build -t infra-control-plane:latest control-plane-src/

# 4. Run the migration gate
#    — exits 0: migrations applied (or already at head) → proceed
#    — exits non-zero: STOP, do NOT restart the app, investigate
docker compose run --rm control-plane-migrate

# 5. Restart app services ONLY after migrate exits 0
docker compose up -d control-plane webhook-worker email-worker
```

Using `docker compose up -d` (whole stack) is also safe: the `depends_on:
condition: service_completed_successfully` on `control-plane` enforces the gate automatically —
migration failure prevents the app from starting.

> **Never** run `docker run infra-control-plane:latest` directly — it bypasses the compose
> dependency graph and the migration gate.

---

## Rollback

```bash
# 1. Stop app services (keep postgres and minio running — do NOT stop the DB)
docker compose stop control-plane webhook-worker email-worker

# 2. Restore previous image
docker tag infra-control-plane:prev infra-control-plane:latest

# 3. If a migration was partially applied, revert it first
docker compose run --rm control-plane-migrate python -m alembic current
docker compose run --rm control-plane-migrate python -m alembic downgrade <safe-revision>

# 4. Restart with restored image
docker compose up -d control-plane webhook-worker email-worker
```

---

## Checking migration state

```bash
# Current applied revision
docker compose run --rm control-plane-migrate python -m alembic current

# Full history
docker compose run --rm control-plane-migrate python -m alembic history
```

---

## docker-compose.yml gate

The production `docker-compose.yml` must include:

```yaml
control-plane-migrate:
  image: infra-control-plane:latest
  env_file:
    - ./.env
  environment:
    DATABASE_URL: ${DATABASE_URL}
  depends_on:
    postgres:
      condition: service_healthy
  command: ["python", "-m", "alembic", "upgrade", "head"]
  restart: "no"

control-plane:
  depends_on:
    postgres:
      condition: service_healthy
    control-plane-migrate:
      condition: service_completed_successfully   # <-- fail-closed gate
    minio-init:
      condition: service_completed_successfully
```

The same `service_completed_successfully` guard applies to `webhook-worker` and `email-worker`
(they depend on `control-plane` which transitively requires migrate to succeed).

---

## Current limitations

No automated CI/CD deploy pipeline exists. Deploy is manual (SSH + docker build + compose up).
A GitHub Actions workflow that SSHes to `tw-relay` and runs the compose procedure would close
this gap — tracked as a separate infra task.

---

## References

- `CLAUDE-workflow.md §1b` — shared deploy-discipline rule (migration before code, always)
- `infra/docker-compose.yml` — dev/local compose template (build-context variant of same gate)
- `apps/control-plane/app/db/migrations/versions/` — Alembic migration files
- `apps/control-plane/alembic.ini` — Alembic configuration
