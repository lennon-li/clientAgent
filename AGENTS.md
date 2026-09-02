# clientAgent — maintainer instructions

This repository contains the general framework for governed,
project-specific AI agents. It is not a client project checkout and must not
contain client-specific deployments or data.

Never add runtime `.env` files, credentials, TLS private keys, provider state,
SQLite databases, client requests, worktrees, logs, rendered previews, source
data, or client-identifying values.

Keep project identity, repository and branch selection, workspace isolation,
request schemas, execution jails, verification, artifact lineage, review
authority, and release gates server-owned. A prompt, chat message, project
file, tool output, or agent-authored file must never widen a contract or
promote a client to maintainer authority.

Use portable framework tests from the repository root. Host-reality
containment tests must run on the intended host rather than inside another
sandbox.
