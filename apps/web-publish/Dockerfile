# syntax=docker/dockerfile:1
# Build stage
FROM node:22-alpine AS builder

WORKDIR /app

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

# Remove dev dependencies
RUN npm prune --production

# Production stage
FROM node:22-alpine

WORKDIR /app

# Copy built app and production dependencies
COPY --from=builder /app/build ./build
COPY --from=builder /app/node_modules ./node_modules
COPY --from=builder /app/package.json ./

# node:22-alpine ships a non-root "node" user (uid 1000) — use it instead of root
RUN chown -R node:node /app
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
