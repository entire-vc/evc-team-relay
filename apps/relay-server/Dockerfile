FROM --platform=$BUILDPLATFORM rust:1.89-slim-trixie AS builder

WORKDIR /build

ARG BUILDARCH
ARG TARGETOS
ARG TARGETARCH

# Run Rust and build scripts natively; cross-compile only the target artifact.
RUN set -eu; \
    case "$BUILDARCH/$TARGETOS/$TARGETARCH" in \
      amd64/linux/amd64|arm64/linux/arm64) ;; \
      amd64/linux/arm64) \
        apt-get update; \
        apt-get install -y --no-install-recommends gcc-aarch64-linux-gnu libc6-dev-arm64-cross; \
        rm -rf /var/lib/apt/lists/* ;; \
      arm64/linux/amd64) \
        apt-get update; \
        apt-get install -y --no-install-recommends gcc-x86-64-linux-gnu libc6-dev-amd64-cross; \
        rm -rf /var/lib/apt/lists/* ;; \
      *) echo "unsupported relay build platform" >&2; exit 1 ;; \
    esac

# ring uses cc-rs for target C/assembly; Cargo's linker alone is insufficient.
ENV CARGO_TARGET_AARCH64_UNKNOWN_LINUX_GNU_LINKER=aarch64-linux-gnu-gcc \
    CC_aarch64_unknown_linux_gnu=aarch64-linux-gnu-gcc \
    AR_aarch64_unknown_linux_gnu=aarch64-linux-gnu-ar \
    CARGO_TARGET_X86_64_UNKNOWN_LINUX_GNU_LINKER=x86_64-linux-gnu-gcc \
    CC_x86_64_unknown_linux_gnu=x86_64-linux-gnu-gcc \
    AR_x86_64_unknown_linux_gnu=x86_64-linux-gnu-ar

ARG RELAY_VERSION
ENV RELAY_VERSION=${RELAY_VERSION}

COPY . .
RUN set -eu; \
    case "$TARGETARCH" in \
      amd64) rust_target=x86_64-unknown-linux-gnu ;; \
      arm64) rust_target=aarch64-unknown-linux-gnu ;; \
      *) echo "unsupported relay target architecture" >&2; exit 1 ;; \
    esac; \
    rustup target add "$rust_target"; \
    cargo build --release --target "$rust_target"; \
    mkdir -p /build/output; \
    cp "target/$rust_target/release/relay" /build/output/relay

FROM debian:trixie-slim

RUN mkdir -p /app/data

WORKDIR /app

# Add tailscale
COPY --from=docker.io/tailscale/tailscale:stable /usr/local/bin/tailscaled /usr/local/bin/tailscaled
COPY --from=docker.io/tailscale/tailscale:stable /usr/local/bin/tailscale /usr/local/bin/tailscale
RUN mkdir -p /var/run/tailscale /var/cache/tailscale /var/lib/tailscale
RUN apt-get update && apt-get install -y ca-certificates curl && rm -rf /var/lib/apt/lists/*

# Add relay binary
COPY --from=builder /build/output/relay /app/relay
COPY ./run.sh /app/run.sh
COPY ./relay.toml /app/relay.toml
RUN chmod +x /app/run.sh

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
  CMD curl -f http://localhost:8080/ready || exit 1

# Accepted, not fixed: the last USER in this image is root. Dropping to an
# unprivileged user is a deployment-coupled change rather than a Dockerfile-only
# one, so it is deferred to a follow-up instead of being shipped unverified:
#   - run.sh starts tailscaled inside this container whenever TAILSCALE_AUTHKEY
#     is set. That needs CAP_NET_ADMIN and write access to /var/run/tailscale
#     and /var/lib/tailscale, both created root-owned above.
#   - Both compose files under infra/ mount a persistent /app/data that already
#     exists on the servers and was populated while this image ran as root (the
#     ./data/relay bind mount, and the relay-data named volume). Docker re-chowns
#     neither, so changing the uid requires a chown -R on each host as part of
#     the upgrade, plus a live check that the relay still starts and serves.
# Accepted 2026-09-18. Review by 2026-10-31: lift this exception together with
# the change that wires this Dockerfile into the release workflow, which is when
# it first produces the published image.
# nosemgrep: dockerfile.security.missing-user-entrypoint.missing-user-entrypoint
ENTRYPOINT ["./run.sh"]