# Relay Web Publishing

Web frontend for viewing published Relay shares.

## Overview

This is a SvelteKit application that provides web access to Relay shared documents. It allows users to view documents without installing Obsidian.

## Features

- **SSR for SEO** - Server-side rendering for fast initial load and search engine optimization
- **Markdown Rendering** - Full CommonMark + GFM support with syntax highlighting
- **Access Control** - Support for public, protected, and private shares
- **Responsive Design** - Mobile-friendly layout
- **Dynamic robots.txt** - SEO control based on share settings

## Development

### Prerequisites

- Node.js 20+
- npm or pnpm

### Setup

```bash
# Install dependencies
npm install

# Start dev server
npm run dev
```

The app will be available at http://localhost:3000

### Environment Variables

```bash
CONTROL_PLANE_URL=http://control-plane:8000  # Internal URL to Control Plane API
PUBLIC_WEB_DOMAIN=docs.example.com           # Public domain for the web app
ORIGIN=https://docs.example.com              # For CSRF protection
```

## Production

### Docker Build

```bash
docker build -t relay-web-publish .
docker run -p 3000:3000 \
  -e CONTROL_PLANE_URL=http://control-plane:8000 \
  -e PUBLIC_WEB_DOMAIN=docs.example.com \
  relay-web-publish
```

### Docker Compose

See `infra/docker-compose.yml` for full stack deployment.

## Architecture

```
Client Browser
    ↓
SvelteKit (SSR)
    ↓
Control Plane API
    ↓
PostgreSQL / MinIO
```

## Routes

- `/` - Home page / landing
- `/[slug]` - View share by slug
- `/robots.txt` - Dynamic robots.txt (proxied from Control Plane)

## API Integration

The app communicates with the Control Plane API:

- `GET /v1/web/shares/{slug}` - Fetch share metadata
- `POST /v1/web/shares/{slug}/auth` - Authenticate for protected shares
- `GET /v1/web/robots.txt` - Get robots.txt content

## Phase 2 Status

✅ **Complete**:
- Project setup and structure
- Markdown rendering with syntax highlighting
- Public share viewing
- Basic UI with sidebar
- SSR data loading
- Docker packaging
- robots.txt proxy

🚧 **Planned for Phase 3**:
- Protected share password authentication (session management)
- Private share OAuth/login flow
- Actual document content from MinIO

## Code Structure

```
src/
├── routes/
│   ├── +layout.svelte          # Main layout
│   ├── +page.svelte            # Home page
│   ├── [slug]/
│   │   ├── +page.server.ts     # SSR data loading
│   │   └── +page.svelte        # Share view
│   └── robots.txt/
│       └── +server.ts          # robots.txt endpoint
├── lib/
│   ├── components/
│   │   ├── MarkdownViewer.svelte  # Markdown rendering
│   │   └── Sidebar.svelte         # Navigation sidebar
│   ├── api.ts                  # Control Plane API client
│   └── markdown.ts             # Markdown utilities
└── app.html                    # HTML template
```

## Testing

```bash
# Type checking
npm run check

# Lint
npm run lint

# Format
npm run format
```

## Contributing

See main repository README for contribution guidelines.

## License

Private - Part of Relay On-Prem
