# Support

Thanks for using EVC Team Relay. This page is the whole map: where to go, and what happens after you write.

## Where to go

| What you have | Where to put it | First response |
|---|---|---|
| A bug | [Open an issue](https://github.com/entire-vc/evc-team-relay/issues/new/choose) | 2 business days |
| A feature idea | [Open an issue](https://github.com/entire-vc/evc-team-relay/issues/new/choose) | 2 business days |
| A setup or usage question | [Open an issue](https://github.com/entire-vc/evc-team-relay/issues/new/choose), or email <support@entire.vc> | 2 business days |
| Anything about your account, your data, or a problem you would rather not describe in public | Email <support@entire.vc> | 2 business days |
| A security vulnerability | **Not a public issue.** [Report it privately](https://github.com/entire-vc/evc-team-relay/security/advisories/new) | 48 hours |
| An open-ended discussion | [Discussions](https://github.com/entire-vc/evc-team-relay/discussions) | 2 business days |

Public issues are the fastest route for anything that is not private — other people hit the same
problems, and a fix in the open helps them too.

## Reporting a bug

The issue templates ask for the list below. If you email instead, please include the same things:

- **Versions** — the plugin, server, or package version, and the Obsidian version if a plugin is involved.
- **Deployment** — hosted by us, or self-hosted.
- **What you expected, and what happened instead.**
- **Steps to reproduce**, numbered.
- **Logs**, if you can get them. In Obsidian: `Ctrl`/`Cmd` + `Shift` + `I` opens Developer Tools; the
  Console tab holds the plugin log. For a self-hosted server: `docker logs <container>`.

Please redact tokens, keys, and document contents before pasting. We do not need them to diagnose
a problem, and an issue is public forever.

A report that arrives with versions and reproduction steps is often fixed in the same exchange.
Without them, our first reply is just a request for them, which costs you a round trip.

## What these response times mean

They are commitments for a **first human response** — someone reading your report and telling you
what happens next. They are not fix deadlines; how long a fix takes depends on what broke.

Business days are Monday to Friday. If we are going to miss one of these, we would rather say so in
the thread than go quiet.

## What is not handled here

This repository holds the control plane, web publishing, and deployment infrastructure. Some things
live elsewhere:

- **The Obsidian plugin** — [evc-team-relay-plugin](https://github.com/entire-vc/evc-team-relay-plugin).
- **The relay server (the Rust sync core)** — [evc-relay-server](https://github.com/entire-vc/evc-relay-server).
- **The MCP server** — [evc-team-relay-mcp](https://github.com/entire-vc/evc-team-relay-mcp).

If you are not sure which one broke, open the issue here. Routing it is our job, not yours.
