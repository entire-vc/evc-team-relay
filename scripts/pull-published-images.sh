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
# Published images are linux/amd64 only — see the preflight below, which
# refuses with an explanation rather than letting the Docker daemon's own
# "no matching manifest for linux/arm64/v8" surface with no cause and no way
# forward. relay-server (pulled later by `docker compose up`, not by this
# script) is amd64-only for the same reason, so the workaround below has to
# be exported for the whole session, not just this command.
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

# The platform docker will actually ask the registry for: DOCKER_DEFAULT_PLATFORM
# wins if set, otherwise the daemon's own os/arch. Asking the daemon (not `uname`)
# is what matters — on Docker Desktop the daemon runs in a Linux VM whose arch is
# the thing the registry is queried with.
effective_platform() {
  if [ -n "${DOCKER_DEFAULT_PLATFORM:-}" ]; then
    printf '%s' "${DOCKER_DEFAULT_PLATFORM}"
    return
  fi
  docker version --format '{{.Server.Os}}/{{.Server.Arch}}' 2>/dev/null || printf ''
}

# Platforms present in a published manifest list, one per line. Empty output
# means "could not determine" — an older docker without `docker manifest`, no
# network, a moved tag. That is deliberately NOT treated as a failure: an
# unreadable manifest must not block a pull that might well succeed.
manifest_platforms() {
  docker manifest inspect "$1" 2>/dev/null \
    | sed -n 's/.*"architecture": *"\([^"]*\)".*/\1/p' \
    | grep -v '^unknown$' || true
}

unsupported_platform_error() {
  local want="$1" have="$2"
  cat >&2 <<MSG

ERROR: the published Team Relay images do not include your platform.

  your platform:     ${want}
  images published:  ${have:-linux/amd64 only}

The images built by our release pipeline are linux/amd64 only. This affects
every arm64 host: Apple Silicon, AWS Graviton, Ampere at Hetzner/OVH,
Raspberry Pi. Two ways forward:

  1. Run the amd64 images under emulation. Slower, but the whole documented
     install path works unchanged. Export it for the session, because
     'docker compose up' later pulls relay-server, which is amd64-only too:

         export DOCKER_DEFAULT_PLATFORM=linux/amd64
         bash scripts/pull-published-images.sh ${VERSION}

  2. Build from source for your own architecture instead of pulling:

         docker build -t infra-control-plane:latest apps/control-plane
         docker build -t infra-web-publish:latest  apps/web-publish

     Note that web-publish needs a GitHub token with read access to our
     @entire-vc npm packages (apps/web-publish/.npmrc); if you don't have
     one, option 1 is your path.

Tracking arm64 images: https://github.com/entire-vc/evc-team-relay/issues

MSG
  exit 1
}

PLATFORM="$(effective_platform)"
PLATFORM_ARCH="${PLATFORM##*/}"

if [ -n "${PLATFORM_ARCH}" ]; then
  AVAILABLE="$(manifest_platforms "${REGISTRY}/control-plane:${VERSION}" | sort -u | paste -sd, -)"
  if [ -n "${AVAILABLE}" ] && ! printf '%s' "${AVAILABLE}" | tr ',' '\n' | grep -qx "${PLATFORM_ARCH}"; then
    unsupported_platform_error "${PLATFORM}" "linux/${AVAILABLE//,/, linux\/}"
  fi
fi

for component in control-plane web-publish; do
  echo "Pulling ${REGISTRY}/${component}:${VERSION}..."
  if ! docker pull "${REGISTRY}/${component}:${VERSION}"; then
    # Belt and braces: the preflight above is skipped whenever the manifest
    # could not be read, so a platform mismatch can still land here. Name the
    # cause rather than leaving the daemon's raw error as the last word.
    if [ -n "${PLATFORM_ARCH}" ] \
       && ! manifest_platforms "${REGISTRY}/${component}:${VERSION}" | grep -qx "${PLATFORM_ARCH}"; then
      unsupported_platform_error "${PLATFORM}" ""
    fi
    exit 1
  fi
  docker tag "${REGISTRY}/${component}:${VERSION}" "infra-${component}:latest"
done

echo "Done. infra-control-plane:latest and infra-web-publish:latest are ready for docker compose up -d."
