# Contributing to EVC Team Relay

Thank you for your interest in contributing! This document provides guidelines for contributing to the project.

## Code of Conduct

Be respectful and constructive. We're building something together.

## How to Contribute

### Reporting Bugs

1. Check existing [issues](https://github.com/entire-vc/evc-team-relay/issues) first
2. Create a new issue with:
   - Clear title and description
   - Steps to reproduce
   - Expected vs actual behavior
   - Environment details (OS, Docker version, etc.)

### Suggesting Features

1. Check existing issues and discussions
2. Create an issue with:
   - Use case description
   - Proposed solution
   - Alternatives considered

### Pull Requests

1. Fork the repository
2. Create a feature branch: `git checkout -b feature/your-feature`
3. Make your changes
4. Run tests: `make test`
5. Run linters: `make lint`
6. Commit with clear messages
7. Push and create a Pull Request

## Development Setup

### Control Plane (Python)

```bash
cd apps/control-plane
python -m venv .venv
source .venv/bin/activate
make install
make test
```

### Web Publish (SvelteKit)

```bash
cd apps/web-publish
npm install
npm run dev
```

### Full Stack (Docker)

```bash
cd infra
cp env.example .env
docker compose up -d
```

## Code Style

### Python

- Python 3.12+
- Format with Ruff: `make fmt`
- Lint with Ruff: `make lint`
- Type hints required
- Line length: 100 characters

### TypeScript/JavaScript

- ESLint + Prettier
- TypeScript strict mode
- Run: `npm run lint`

### Commits

- Use clear, descriptive commit messages
- Reference issues: "Fix #123: description"
- Keep commits focused and atomic

## Testing

### Python Tests

```bash
cd apps/control-plane
make test                    # Run all tests
make test ARGS="-k test_auth"  # Run specific tests
```

### Coverage

Aim for meaningful test coverage. Focus on:
- API endpoints
- Business logic
- Edge cases

## Project Structure

```
apps/
├── control-plane/    # FastAPI backend
│   ├── app/
│   │   ├── api/      # API routes
│   │   ├── core/     # Config, security
│   │   ├── db/       # Models, migrations
│   │   └── services/ # Business logic
│   └── tests/
└── web-publish/      # SvelteKit frontend
    └── src/

infra/                # Docker Compose stack
forks/                # Vendored dependencies
docs/                 # Documentation
```

## License

By contributing, you agree that your contributions will be licensed under the Apache 2.0 License.

## Questions?

Open an issue or discussion. We're happy to help!
