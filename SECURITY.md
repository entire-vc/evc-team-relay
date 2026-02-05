# Security Policy

## Supported Versions

| Version | Supported          |
| ------- | ------------------ |
| 1.x.x   | :white_check_mark: |
| < 1.0   | :x:                |

## Reporting a Vulnerability

We take security seriously. If you discover a security vulnerability, please report it responsibly.

### How to Report

**Do NOT create a public GitHub issue for security vulnerabilities.**

Instead, please email: **security@entire.vc**

Include:
- Description of the vulnerability
- Steps to reproduce
- Potential impact
- Suggested fix (if any)

### What to Expect

1. **Acknowledgment** — We'll respond within 48 hours
2. **Assessment** — We'll investigate and assess severity
3. **Fix** — We'll develop and test a fix
4. **Disclosure** — We'll coordinate disclosure timing with you
5. **Credit** — We'll credit you in the release notes (if desired)

### Timeline

- Critical vulnerabilities: Fix within 7 days
- High severity: Fix within 14 days
- Medium/Low: Fix in next release cycle

## Security Best Practices

When deploying EVC Team Relay:

### Secrets Management

- Use strong, unique passwords for all services
- Generate JWT_SECRET with: `openssl rand -hex 32`
- Never commit `.env` files to version control
- Rotate secrets periodically

### Network Security

- Run behind a reverse proxy (Caddy included)
- Use HTTPS only (Caddy handles this)
- Restrict database access to internal network
- Consider firewall rules for management ports

### Updates

- Subscribe to release notifications
- Apply security updates promptly
- Review changelog for security fixes

### Monitoring

- Enable audit logging
- Monitor for unusual activity
- Set up alerts for failed logins
- Review logs regularly

## Known Security Features

- JWT authentication with Ed25519 signing
- Rate limiting on sensitive endpoints
- Password hashing with bcrypt
- Audit logging of all actions
- CORS protection
- Input validation

## Scope

In scope:
- Control Plane API
- Relay Server
- Web Publish service
- Official Docker images
- Official Obsidian plugin

Out of scope:
- Third-party integrations
- User-modified deployments
- Social engineering attacks
- Physical attacks

## Hall of Fame

We thank the following security researchers for responsible disclosure:

*No submissions yet*

---

Thank you for helping keep EVC Team Relay secure!
