#!/usr/bin/env bash
#
# Team Relay — pull the published control-plane + web-publish images and tag
# them locally to match what infra/docker-compose.yml expects (infra-control-
# plane:latest, infra-web-publish:latest). This is the path for anyone who
# doesn't have a GitHub token with read access to our private @entire-vc/*
# packages — which building web-publish from source needs (see
# apps/web-publish/.npmrc) and every external OSS user is in that position.
#
# Both images are published publicly by .github/workflows/release.yml on
# every version tag; no login is required to pull them.
#
# linux/amd64 only for now (same for relay-server) — there is no arm64
# manifest, so this fails on Apple Silicon / arm64 hosts without emulation
# (e.g. `export DOCKER_DEFAULT_PLATFORM=linux/amd64` under Docker Desktop).
# Tracked separately; not fixed here.
#
# Usage:
#   bash scripts/pull-published-images.sh [version]   # default: latest
#
# Then, from infra/:
#   docker compose up -d
# picks up the locally-tagged images without attempting to build anything.

set -euo pipefail

VERSION="${1:-latest}"
REGISTRY="ghcr.io/entire-vc/evc-team-relay"

for component in control-plane web-publish; do
  echo "Pulling ${REGISTRY}/${component}:${VERSION}..."
  docker pull "${REGISTRY}/${component}:${VERSION}"
  docker tag "${REGISTRY}/${component}:${VERSION}" "infra-${component}:latest"
done

echo "Done. infra-control-plane:latest and infra-web-publish:latest are ready for docker compose up -d."
