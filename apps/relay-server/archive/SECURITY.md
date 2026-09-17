# Security Policy

## Supported Versions

This is a rolling fork tracking [No-Instructions/relay-server](https://github.com/No-Instructions/relay-server). Only the latest tagged release (`relay-x.y.z`) and the `main` branch are security-maintained; older tags are not backported.

## Reporting a Vulnerability

We take security seriously. If you discover a security vulnerability, please report it responsibly.

### How to Report

**Do NOT create a public GitHub issue for security vulnerabilities.**

Use GitHub's private vulnerability reporting:
**[Report a vulnerability](https://github.com/entire-vc/evc-relay-server/security/advisories/new)**

The report is visible only to the maintainers of this repository until we publish an advisory. If
you cannot use GitHub, email <support@entire.vc> with `SECURITY` in the subject and we will move the
conversation somewhere private; do not put vulnerability details in a public issue in the meantime.

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

## Scope

In scope:
- This fork's relay server code (`crates/`) and its published Docker image (`ghcr.io/entire-vc/evc-relay-server`)
- Deviations we introduced from upstream

Out of scope:
- Vulnerabilities that exist unmodified in [No-Instructions/relay-server](https://github.com/No-Instructions/relay-server) upstream — please report those upstream as well, since we will need their fix to sync it here
- Third-party integrations
- User-modified deployments
- Social engineering attacks
- Physical attacks

## Hall of Fame

We thank the following security researchers for responsible disclosure:

*No submissions yet*

---

Thank you for helping keep this project secure!
