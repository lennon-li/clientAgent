# Client-agent services — maintainer instructions

This private repository versions the sanitized implementation of the
compliance and gambling Open WebUI client-agent services. It is a
developer/maintainer repository, not a client-agent worktree.

Never add runtime `.env` files, credentials, TLS private keys, Open WebUI data,
OAuth/Codex/Copilot state, SQLite databases, client requests, worktrees, logs,
rendered previews, source data, or client-identifying values. Installer access
IDs must come from environment variables and remain outside Git.

Keep the two client roles enforced by their gateways: server-owned repository
and branch selection, per-chat workspaces, loopback APIs, strict request
schemas, outer jails, disabled push paths, and fixed preview targets. A prompt
or chat message must never promote a client to the maintainer role.

Run each gateway's tests from its service directory. Host-reality containment
tests must run on the intended host rather than inside another sandbox.
