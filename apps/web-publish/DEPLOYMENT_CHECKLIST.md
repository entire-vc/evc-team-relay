# Web Publishing Deployment Checklist

## Prerequisites

- [x] Phase 1 complete (DB migration, Control Plane API endpoints)
- [x] Phase 2 complete (SvelteKit app)
- [ ] Docker and Docker Compose installed
- [ ] DNS configured for web publish domain

## Environment Configuration

### 1. Update `.env` in `infra/`

```bash
# Add this line
WEB_PUBLISH_DOMAIN=docs.5evofarm.entire.vc
```

### 2. Verify Control Plane Settings

Ensure these are already set (from Phase 1):
```bash
# Already configured in control-plane
WEB_PUBLISH_DOMAIN=docs.5evofarm.entire.vc  # Same as above
```

## Build & Deploy

### Local Testing

```bash
# 1. Build the app
cd apps/web-publish
npm install
npm run build

# 2. Test locally
npm run preview
# Visit http://localhost:4173

# 3. Test Docker build
docker build -t relay-web-publish .

# 4. Run container
docker run -p 3000:3000 \
  -e CONTROL_PLANE_URL=http://localhost:8000 \
  -e PUBLIC_WEB_DOMAIN=localhost:3000 \
  -e ORIGIN=http://localhost:3000 \
  relay-web-publish

# Visit http://localhost:3000
```

### Full Stack with Docker Compose

```bash
cd infra

# 1. Update .env with WEB_PUBLISH_DOMAIN

# 2. Build and start all services
docker compose up -d --build

# 3. Check web-publish service
docker compose ps
docker compose logs web-publish

# 4. Verify health
curl http://localhost:3000/
```

## DNS Configuration

### For staging (5evofarm.entire.vc)

Add A record:
```
docs.5evofarm.entire.vc -> 164.92.198.130
```

### For production

Add A record for your domain:
```
docs.example.com -> <your-server-ip>
```

## Verification Steps

### 1. Basic Health Check

```bash
# Landing page
curl https://docs.5evofarm.entire.vc/

# Should return HTML with "Welcome to Relay"
```

### 2. robots.txt

```bash
curl https://docs.5evofarm.entire.vc/robots.txt

# Should return:
# User-agent: *
# Disallow: /
```

### 3. Control Plane Integration

```bash
# Check if web publishing is enabled
curl https://cp.5evofarm.entire.vc/server/info

# Should include:
# {
#   "web_publish_enabled": true,
#   "web_publish_domain": "docs.5evofarm.entire.vc"
# }
```

### 4. Share View (requires published share)

```bash
# First, create and publish a share via Control Plane API
# Then test:
curl https://docs.5evofarm.entire.vc/your-share-slug

# Should return HTML with markdown rendered
```

## Troubleshooting

### Service not starting

```bash
# Check logs
docker compose logs web-publish

# Common issues:
# - Missing CONTROL_PLANE_URL
# - Control plane not healthy
# - Port 3000 already in use
```

### 502 Bad Gateway from Caddy

```bash
# Check if web-publish is running
docker compose ps web-publish

# Check Caddy logs
docker compose logs caddy

# Restart web-publish
docker compose restart web-publish
```

### Share not found

```bash
# Verify share is published
curl -H "Authorization: Bearer <jwt>" \
  https://cp.5evofarm.entire.vc/shares/<id>

# Check web_published, web_slug fields
```

### robots.txt returns 404

```bash
# Check Control Plane web endpoint
curl https://cp.5evofarm.entire.vc/v1/web/robots.txt

# If CP works but web-publish doesn't, check logs
docker compose logs web-publish
```

## SSL Certificate

Caddy automatically obtains SSL certificates from Let's Encrypt:

1. Ensure port 80 and 443 are open
2. DNS must be pointing to the server
3. First request triggers certificate issuance
4. Check Caddy logs for certificate status

```bash
docker compose logs caddy | grep -i certificate
```

## Monitoring

### Health Checks

```bash
# Docker health status
docker compose ps web-publish

# Manual health check
docker exec web-publish node -e "require('http').get('http://localhost:3000/', (r) => {console.log(r.statusCode)})"
```

### Logs

```bash
# Follow logs
docker compose logs -f web-publish

# Last 100 lines
docker compose logs --tail=100 web-publish
```

### Metrics (future)

```bash
# Web publish will be added to Prometheus in future phase
curl http://localhost:9090/targets
```

## Rollback

If something goes wrong:

```bash
# 1. Stop web-publish
docker compose stop web-publish

# 2. Remove from Caddyfile (comment out WEB_PUBLISH_DOMAIN block)

# 3. Reload Caddy
docker compose exec caddy caddy reload --config /etc/caddy/Caddyfile

# 4. Optionally remove service
docker compose rm -f web-publish
```

## Phase 3 Prerequisites

Before Phase 3 (Access Control):

- [ ] Web-publish deployed and accessible
- [ ] At least one published share for testing
- [ ] Password-protected share for testing
- [ ] Private share for OAuth testing

## Security Checklist

- [x] HTTPS enforced (Caddy)
- [x] HTML sanitization (DOMPurify)
- [ ] Rate limiting (Phase 3)
- [ ] Session security (Phase 3)
- [ ] CSRF protection (Phase 3)

## Performance Targets

- [ ] First Contentful Paint < 2s
- [ ] Time to Interactive < 3s
- [ ] Lighthouse score > 90
- [ ] No console errors

---

**Last updated**: 2026-02-02
**Phase**: 2 (Web App Core)
**Status**: Ready for deployment testing
