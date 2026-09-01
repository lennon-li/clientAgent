# compliance — Decisions

## 2026-08-18 — Client agent works on a service-owned clone, not the checkout

The compliance client-agent gateway creates client branches and worktrees from
a bare clone at `state/repo.git` (`git clone --no-hardlinks --bare`), not from
`/home/yeli/shiny/compliance`. Rationale: the maintainer's checkout stays free
of `client/compliance/*` refs and `.git/worktrees` entries, and no client
activity can touch it. Refreshing the clone is an explicit maintainer fetch.

## 2026-08-18 — Repository-level `AGENTS.md` added, left uncommitted

`/home/yeli/shiny/compliance/AGENTS.md` was written as an uncommitted
working-tree file for Lennon to review before it enters history.

## 2026-08-18 — Runs as `yeli` permanently; containment via bubblewrap

No `svc-compliance` service account will be created. The compliance client
agent gateway runs as `yeli`, which holds NOPASSWD root (`/etc/sudoers.d/
yeli-codex`) and docker group membership.

The "agent cannot change anything outside the project folder" guarantee is
therefore provided by **bubblewrap**, not by the runtime user. Every Codex
invocation runs in an unprivileged user namespace. Verified live: writes outside
the workspace fail, `/home/yeli` is invisible, `~/.ssh` and the maintainer's
checkout are absent, `/srv` is unreachable, and `sudo` fails on `no_new_privs`.

The privilege still exists for anything running outside the jail, including the
gateway process itself. Containment denies the agent its use; it does not
remove it.

## 2026-08-18 — Per-chat clone, and the gateway commits

Each chat gets a full clone under `state/worktrees/<chat>`, not a linked
`git worktree`. A linked worktree keeps index/refs/objects outside the worktree
directory, and Codex's inner sandbox would not grant write access there
(`--add-dir` and `sandbox_workspace_write.writable_roots` were both tried and
did not lift it in 0.147.0).

Codex also denies writes to `.git` even inside the workspace, so the **gateway**
stages and commits the agent's changes after each turn. The agent edits files
only. This makes commit authorship server-controlled and prevents the agent
rewriting history.

## 2026-08-18 — Shared login for the client UI

A single generic Open WebUI account, with nginx Basic Auth and self-signed TLS
in front (designed in `docs/INGRESS.md`, not applied). Known limitation:
attribution is lost, since every job and commit traces to one identity. The
gateway records a per-job `user_id` regardless, so per-user accounts can be
introduced later without a schema change.
