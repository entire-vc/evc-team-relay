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
# Both images publish linux/amd64 and linux/arm64 — the preflight below only
# fires for a platform outside that pair, with an explanation rather than
# letting the Docker daemon's own "no matching manifest for ..." surface with
# no cause and no way forward. relay-server (pulled later by `docker compose
# up`, not by this script) is still amd64-only third-party; it's pinned to
# `platform: linux/amd64` in infra/docker-compose.yml so it runs under
# emulation on an arm64 host without affecting these two images.
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
  images published:  ${have:-linux/amd64, linux/arm64}

The images built by our release pipeline currently target linux/amd64 and
linux/arm64 (Apple Silicon, AWS Graviton, Ampere, Raspberry Pi included) —
your platform isn't one of those. Two ways forward:

  1. Run the amd64 images under emulation for this pull only. Slower, but
     the whole documented install path works unchanged:

         DOCKER_DEFAULT_PLATFORM=linux/amd64 bash scripts/pull-published-images.sh ${VERSION}

  2. Build from source for your own architecture instead of pulling:

         docker build -t infra-control-plane:latest apps/control-plane
         docker build -t infra-web-publish:latest  apps/web-publish

     Note that web-publish needs a GitHub token with read access to our
     @entire-vc npm packages (apps/web-publish/.npmrc); if you don't have
     one, option 1 is your path.

Tracking additional platforms: https://github.com/entire-vc/evc-team-relay/issues

MSG
  exit 1
}

# Echoes the published platform list when it is READABLE and genuinely lacks
# our platform; returns non-zero in every other case, including "the manifest
# told us nothing". That distinction is the whole point: an empty list means
# we could not look, not that the platform is absent. Conflating the two is
# how an unrelated failure (a typo in the version argument, an expired login)
# ends up being reported as an architecture problem — the same wrong-diagnosis
# class this script exists to remove, reintroduced one level down.
platform_missing_from() {
  local image="$1" available
  [ -n "${PLATFORM_ARCH}" ] || return 1
  available="$(manifest_platforms "$image" | sort -u | paste -sd, -)"
  [ -n "$available" ] || return 1
  printf '%s' "$available" | tr ',' '\n' | grep -qx "${PLATFORM_ARCH}" && return 1
  printf 'linux/%s' "${available//,/, linux\/}"
}

PLATFORM="$(effective_platform)"
PLATFORM_ARCH="${PLATFORM##*/}"

if AVAILABLE="$(platform_missing_from "${REGISTRY}/control-plane:${VERSION}")"; then
  unsupported_platform_error "${PLATFORM}" "${AVAILABLE}"
fi

for component in control-plane web-publish; do
  echo "Pulling ${REGISTRY}/${component}:${VERSION}..."
  if ! docker pull "${REGISTRY}/${component}:${VERSION}"; then
    # Belt and braces: the preflight above is skipped whenever the manifest
    # could not be read, so a platform mismatch can still land here. Name the
    # cause only when the manifest actually proves it — otherwise the daemon's
    # own error is the honest last word, and a bad version argument stays a
    # bad version argument instead of being blamed on your CPU.
    if AVAILABLE="$(platform_missing_from "${REGISTRY}/${component}:${VERSION}")"; then
      unsupported_platform_error "${PLATFORM}" "${AVAILABLE}"
    fi
    exit 1
  fi
  docker tag "${REGISTRY}/${component}:${VERSION}" "infra-${component}:latest"
done

echo "Done. infra-control-plane:latest and infra-web-publish:latest are ready for docker compose up -d."
