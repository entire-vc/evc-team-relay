# Support

Thanks for using EVC Relay Server. This page is the whole map: where to go, and what happens after you write.

## Where to go

| What you have | Where to put it | First response |
|---|---|---|
| A bug | [Open an issue](https://github.com/entire-vc/evc-relay-server/issues/new/choose) | 2 business days |
| A feature idea | [Open an issue](https://github.com/entire-vc/evc-relay-server/issues/new/choose) | 2 business days |
| A setup or usage question | [Open an issue](https://github.com/entire-vc/evc-relay-server/issues/new/choose), or email <support@entire.vc> | 2 business days |
| Anything about your account, your data, or a problem you would rather not describe in public | Email <support@entire.vc> | 2 business days |
| A security vulnerability | **Not a public issue.** [Report it privately](https://github.com/entire-vc/evc-relay-server/security/advisories/new) | 48 hours |

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

This repository is a fork of [`No-Instructions/relay-server`](https://github.com/No-Instructions/relay-server),
and it builds exactly one thing: the Rust `relay` binary in [`crates/`](crates/).

- **A problem with Relay (relay.md), the upstream product** — that is a different team. Their
  channels are on their own repository; we cannot action their bugs here.
- **A problem with the server we build and ship** — right here. If you are running
  `ghcr.io/entire-vc/evc-relay-server`, this is your repository.
- **A problem with the Obsidian plugin** — [evc-team-relay-plugin](https://github.com/entire-vc/evc-team-relay-plugin).
- **A problem with the hosted control plane** — [evc-team-relay](https://github.com/entire-vc/evc-team-relay),
  or email <support@entire.vc>.

If you are not sure whether a bug is ours or upstream's, open it here anyway. Sorting that out is
our job, not yours.
