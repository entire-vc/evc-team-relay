FROM rust:1.89-slim-trixie AS builder

WORKDIR /build

ARG RELAY_VERSION
ENV RELAY_VERSION=${RELAY_VERSION}

COPY . .
RUN cargo build --release

FROM debian:trixie-slim

RUN mkdir -p /app/data

WORKDIR /app

# Add tailscale
COPY --from=docker.io/tailscale/tailscale:stable /usr/local/bin/tailscaled /usr/local/bin/tailscaled
COPY --from=docker.io/tailscale/tailscale:stable /usr/local/bin/tailscale /usr/local/bin/tailscale
RUN mkdir -p /var/run/tailscale /var/cache/tailscale /var/lib/tailscale
RUN apt-get update && apt-get install -y ca-certificates curl && rm -rf /var/lib/apt/lists/*

# Add relay binary
COPY --from=builder /build/target/release/relay /app/relay
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