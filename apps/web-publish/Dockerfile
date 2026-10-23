# syntax=docker/dockerfile:1
# Build stage: runs on the build host's native platform. The vite/rolldown
# client build hangs under qemu-user when the image targets arm64, and the
# output (build/) is plain JS, so it is identical on every platform.
FROM --platform=$BUILDPLATFORM node:22-alpine AS builder

WORKDIR /app

# rolldown 1.2.14 (vite 8.3.4) deadlocks at 0% CPU on arm64 when its rayon/tokio
# pools are sized to the host core count. Pin them for the build stage only.
ENV RAYON_NUM_THREADS=2
ENV TOKIO_WORKER_THREADS=2
ENV UV_THREADPOOL_SIZE=2

# Copy package files and the npm config (min-release-age supply-chain delay)
COPY package.json package-lock.json* .npmrc ./

# Install dependencies. @entire-vc/tokens and @entire-vc/ui-svelte are public
# on npmjs.org, so this build needs no registry credential; the github_token
# BuildKit secret this used to mount is gone.
RUN npm ci

# Copy source code
COPY . .

# Build the app
RUN npm run build

# Production dependencies, installed for the target platform so any native
# addon matches the runtime image. Dev tooling (vite, rolldown) is not
# installed here, so nothing heavy runs under emulation.
FROM node:22-alpine AS deps

WORKDIR /app

COPY package.json package-lock.json* .npmrc ./
RUN npm ci --omit=dev

# Production stage
FROM node:22-alpine

WORKDIR /app

# Copy built app and production dependencies
COPY --from=builder --chown=node:node /app/build ./build
COPY --from=deps --chown=node:node /app/node_modules ./node_modules
COPY --from=builder --chown=node:node /app/package.json ./

# node:22-alpine ships a non-root "node" user (uid 1000) — use it instead of root
RUN chown node:node /app
USER node

# Set environment variables
ENV NODE_ENV=production
ENV PORT=3000

# Expose port
EXPOSE 3000

# Health check
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD node -e "require('http').get('http://localhost:3000/', (r) => {process.exit(r.statusCode === 200 ? 0 : 1)})"

# Start the app
CMD ["node", "build"]
