# Compliance Client Agent — local-only MVP

A loopback-only gateway that lets a non-technical client ask, in chat, for
changes to the **compliance** R package. Each conversation gets its own git
branch and worktree; a sandboxed coding agent does the work there; the gateway
records what changed. Nothing is ever pushed, merged, or deployed to production;
an explicit `/preview` request lets the gateway update only the fixed test app.
The agent runs inside a bubblewrap jail that confines it to its own workspace.

Built 2026-08-18 on `asgard`. Local only. Not exposed to any network.

---

## Trusted role boundary

A direct interactive CLI in `/home/yeli/shiny/compliance` on `main` is a
developer/maintainer session. Jer is a different launch path: Open WebUI always
routes it through the gateway onto `client/compliance/*` in an isolated clone.
The request schema, jail, disabled push URL, denied tools, and fixed preview
target enforce the client role; chat text cannot promote it.

Jer can still update the test app by prefixing a request with `/preview`. The
Pipe sends only a boolean preview flag, and the gateway copies only
`inst/app/app.R` and `R/account2id.R` from the committed chat workspace to
`/srv/shiny-server/test/compliance`. Jer never receives direct `/srv` access.

## Containment and runtime identity — read this first

The service runs as `yeli`, permanently and by decision (2026-08-18). No
service account is being created. `yeli` holds NOPASSWD root via
`/etc/sudoers.d/yeli-codex` for `cp`, `install`, `mkdir`, `chown`, `chmod`,
`rsync`, `systemctl` and `nginx`, and is in the `docker` group, which is
root-equivalent on its own.

**Every agent invocation is wrapped in a bubblewrap jail**, so that privilege is
out of the agent's reach. Verified live — the agent ran these probes on itself
and reported the output verbatim:

```text
touch: cannot touch '/home/yeli/ESCAPED': Read-only file system
ls: cannot access '/home/yeli/.ssh': No such file or directory
sudo: The "no new privileges" flag is set, which prevents sudo from running as root.
```

In the same run it changed a file in its workspace and `devtools::test()`
reported **7 passed, 0 failures** — containment that broke the toolchain would
be no good either.

### What the jail guarantees

| | |
|---|---|
| Writes | Only the chat's workspace and `CODEX_HOME`. Everything else is read-only or absent. |
| `/home/yeli` | Not visible, except the specific read-only binds below. |
| `~/.ssh`, other repos, the maintainer's checkout | Not visible at all. |
| `/srv/shiny-server`, `/srv` | Not visible. Deployment is unreachable. |
| `sudo` | Fails. `no_new_privs` makes setuid inert; the grant is untouched but unusable. |
| Docker socket | Not bound, so docker group membership buys nothing. |

Read-only binds are the minimum needed to work: the R toolchain, the Codex
binaries, and the project memory tree.

### What the jail does NOT contain — be precise about this

1. **Network egress from the codex process.** `--unshare-net` breaks Codex,
   which must reach the API, so the jail deliberately keeps the network. The
   *shell commands the agent spawns* are denied network by the inner Codex
   sandbox (`network_access = false`), but the codex process itself has egress.
   A compromised Codex binary, or a model-side exfiltration path, is not
   contained by this design.
2. **Anything running outside the jailed runner.** The gateway process itself
   runs as plain `yeli` with full sudo rights. The jail denies the *agent* that
   privilege; it does not remove it from the account. A bug in the gateway, or
   a future code path that forgets to wrap, is unprotected. `jail.required:
   true` makes a missing bwrap a startup failure rather than a silent
   downgrade.
3. **The workspace itself.** The agent has full write access there by design.
   It can break the code, write nonsense, or delete files in its own clone.
   That is the job; that is why nothing is auto-merged.
4. **Prompt injection.** Unchanged — but its blast radius is now the workspace
   rather than the host.
5. **Kernel and bubblewrap bugs.** A user-namespace escape defeats all of this.
   Unprivileged userns is itself a historically busy source of CVEs.

### Layered design

Two sandboxes doing different jobs, neither replacing the other:

- **Outer (bubblewrap):** filesystem and privilege containment.
- **Inner (codex `workspace-write`):** denies network to agent-spawned commands.

The inner sandbox no longer excludes `/tmp`: inside the jail `/tmp` is a private
tmpfs that cannot touch the host, and excluding it broke R
(`creating temporary file for '-e' failed`).

## Architecture

```
client chat message
      |
      v
POST /v1/jobs  (127.0.0.1:8642, shared-secret header)
      |
      +-- rejects any client-supplied repo/branch/sandbox/credential -> 400
      |
      v
job row in SQLite  (status=queued)   <- returns job_id immediately
      |
      v
single asyncio worker, one job at a time
      |
      +-- first message in a chat: clone the SERVICE-OWNED repo into
      |   state/worktrees/<chat> and branch client/compliance/<short-id>
      +-- later messages: reuse that workspace, resume the agent thread by id
      |
      v
bwrap --unshare-user ... -- <configured-agent> ... --cd <workspace>
      |
      v
gateway commits the agent's changes on its behalf
```

### Repository isolation

The maintainer's checkout at `/home/yeli/shiny/compliance` is **never touched at
runtime**. It is read exactly once, by `scripts/bootstrap_repo.sh`, which makes
a service-owned bare clone:

```
git clone --no-hardlinks --bare /home/yeli/shiny/compliance state/repo.git
```

`--no-hardlinks` means the clone shares no object files with the original, so
nothing the agent does can corrupt or inflate it. `remote.origin.pushurl` is set
to `no-push-configured` so an accidental push fails immediately.

**Each chat then gets its own clone** of that service repo, under
`state/worktrees/<chat>`, rather than a linked `git worktree`. That choice was
forced by containment and is worth recording: a linked worktree keeps its index,
refs and objects in the parent repo, *outside* the worktree directory, and
Codex's inner sandbox only grants write access to the workspace. Neither
`--add-dir` nor `sandbox_workspace_write.writable_roots` widened it in 0.147.0 —
both were tried against live runs, and `git commit` failed with
`Unable to create '.../index.lock': Read-only file system`. A clone puts `.git`
inside the workspace, so no sandbox widening is needed and the inner sandbox
stays at its strictest setting. It also means a runaway job cannot corrupt the
shared clone. The cost is ~11 MB per chat.

`tests/test_repo_isolation.py` asserts that no runtime path resolves inside the
source repo, that the source has no client branches and no worktree metadata,
and that no object files are shared.

Refreshing the clone with upstream changes is a **maintainer action**:

```bash
git -C state/repo.git fetch origin '+refs/heads/main:refs/heads/main'
```

### Runtime providers

The default provider is **GitHub Copilot CLI**. It is selected in
`gateway/config.yaml` with `codex.provider: copilot` (the `codex` section name
and database column are retained for compatibility). The adapter invokes
Copilot's non-interactive JSONL mode, stores the `session.start` id, and uses
`--resume <id>` for later messages. Its CLI is kept inside the same
bubblewrap jail; `--allow-all-tools` is scoped by that outer jail, while
publication and privilege commands are explicitly denied.

The worker accepts `COMPLIANCE_AGENT_PROVIDER=codex` as a rollback switch.
Codex retains its existing `codex exec`/`resume` implementation and credential
handling. Provider-specific executable and PATH overrides are documented in
`.env.example`.

### Why `codex exec`, not `app-server` (Codex provider)

**Implemented with `codex exec` plus `codex exec ... resume <thread_id>`.** Not
`app-server`. Being precise about this because it is easy to overclaim.

`codex app-server` does exist in 0.147.0 and has a `daemon` subcommand with
`start` / `stop` / `bootstrap`, but it is marked `[experimental]` and speaks a
JSON-RPC protocol over stdio or a unix socket whose schema you have to generate
yourself (`generate-json-schema`). It would have to be reverse-engineered and
pinned to an experimental interface.

`codex exec --json` turned out to give exactly what was needed anyway. It emits
a `thread.started` event carrying a real thread id:

```json
{"type":"thread.started","thread_id":"01a0152c-c0b5-7800-8137-96f808c2273e"}
```

and that id is accepted verbatim by `codex exec ... resume <id>`, which restores
the full conversation. Verified end to end: the second message in a chat
correctly recalled the commit message from the first. The thread id stored in
`chats.codex_thread_id` is that real id, not a synthetic one.

One sharp edge worth knowing: global options must come **before** the `resume`
subcommand. `codex exec resume <id> --sandbox ...` is rejected by the argument
parser. `tests/test_codex_argv.py` guards the ordering.

### Sandbox isolation

`/home/yeli/.codex/config.toml` sets `approval_policy = "never"` and
`sandbox_mode = "danger-full-access"` **globally**. Inheriting that would defeat
the entire design, so every invocation passes:

- `--ignore-user-config` — no config.toml anywhere is loaded, including ours
- `--sandbox workspace-write` — explicit, and `build_argv()` raises on any
  other value
- `--cd <worktree>` — explicit
- `-c` hardening: `network_access=false`, `exclude_slash_tmp=true`,
  `exclude_tmpdir_env_var=true`

The child environment is constructed from scratch rather than inherited, so the
gateway's shared secret never reaches the Codex process.

### Agent credentials

With the default Copilot provider, authenticate once into the service-owned
Copilot state directory using OAuth's device flow:

```bash
COPILOT_HOME=/home/yeli/services/compliance-client-agent/state/codex_home \
  /home/yeli/.npm-global/bin/copilot login --device-code
```

Complete the displayed URL/code approval in a browser. The jailed worker uses
that same `COPILOT_HOME`, so the OAuth credential is available without exposing
the host's keyring. For headless automation, `COPILOT_GITHUB_TOKEN` is also
supported; it must have the GitHub Copilot Requests permission and is forwarded
only to the Copilot child. The gateway secret and unrelated
`GH_TOKEN`/`GITHUB_TOKEN` values are not passed.

When the Codex rollback provider is selected, the service runs as `yeli` and
authenticates with the maintainer's existing Codex login at `~/.codex/auth.json`.
That remains the supported Codex steady state.

Consequence worth naming: every job bills and attributes to that one login. The
`jobs` table records a per-job `user_id`, but the Codex side sees one identity.

`resolve_codex_home()` checks the service `CODEX_HOME` first, so dropping a
service-owned `auth.json` there would take precedence without a code change if
that decision is ever revisited. The runtime identity is logged **once at
startup**, not per job — a per-job warning would fire on every job and become
noise people learn to ignore. `/health` reports it as `runtime_user` and
`agent_auth`.

Using the maintainer's `CODEX_HOME` for credentials does **not** inherit their
`config.toml`, because `--ignore-user-config` means no config file is read at
all. That isolation is independent of the credential decision and stays.

### Who commits, and why

**The gateway commits, not the agent.** Codex's sandbox denies writes to `.git`
even when it sits inside the writable workspace, so the agent is told explicitly
not to try — it edits files and describes what it did, and the gateway stages
and commits everything as soon as the turn ends.

This turned out to be the better arrangement regardless. Commit authorship and
message format are server-controlled and consistent, and the agent cannot amend,
rewrite history, or craft a misleading commit. It can only change files.

A workspace still dirty after a successful run therefore means the *commit*
failed, which marks the chat `needs_attention`. Nothing is reset or deleted; the
edits stay on disk.

### Worktree lifecycle

- Created on the first message of a chat, reused thereafter.
- **Never** auto-deleted, auto-reset, or auto-cleaned. A job that fails, times
  out, or leaves uncommitted changes marks the chat `needs_attention` and stops.
  Further messages on that chat return 409 until a maintainer clears it.
  Automatic recovery would mean automatically destroying whatever the last run
  left behind.
- A job left `running` by a gateway crash is marked `needs_attention` on the
  next start rather than retried.

### Disconnect safety

`POST /v1/jobs` returns a `job_id` as soon as the row is written; everything
after that runs in a task owned by the application, not the request. The Codex
child is spawned with `start_new_session=True`, so it has its own process group
and never receives a signal aimed at the HTTP request. Events are rows in
SQLite, so a dropped SSE stream is re-readable with a higher `after` cursor.
A client hanging up cannot kill Codex, reset git, or delete a worktree.

---

## API

Bound to `127.0.0.1:8642`. Auth is a shared secret in the `Authorization`
header (bare or `Bearer <secret>`), read from `$COMPLIANCE_GATEWAY_SECRET` in
`.env` (mode 600) and compared with `hmac.compare_digest`. It **fails closed**:
if the variable is unset the gateway raises rather than starting with an empty
or default secret. The secret is machine-generated random hex and is
deliberately *not* the shared login password — separate credentials, separate
purposes.

| Endpoint | Purpose |
|---|---|
| `POST /v1/jobs` | Queue a job. Returns `202` with `job_id` immediately. |
| `GET /v1/jobs/{job_id}` | Full job record. |
| `GET /v1/jobs/{job_id}/events` | **SSE** stream. `?stream=false` for a JSON snapshot, `?after=<id>` to resume. |
| `GET /health` | No auth. Repo presence, default branch, queue depth, provider and auth mode. |

SSE was chosen over long-poll because Open WebUI's event emitter is already
incremental and a dropped connection costs nothing — the events are durable.

`POST /v1/jobs` accepts **only** `{chat_id, user_id, message, messages?}`.
Anything else is a `400` naming the field — never a silent drop, because a
silently ignored `repo_path` looks like it worked. Rejected keys include repo
paths, project ids, worktree paths, branch names, sandbox and approval modes,
`codex_home`, model, system prompts, and anything credential-shaped. The scan is
recursive, so `{"messages":[{"repo_path":...}]}` is caught too, and it is
case- and hyphen-insensitive.

```bash
curl -s -X POST http://127.0.0.1:8642/v1/jobs \
  -H "Authorization: Bearer $COMPLIANCE_GATEWAY_SECRET" \
  -H 'Content-Type: application/json' \
  -d '{"chat_id":"c1","user_id":"lennon","message":"Add a comment to R/foo.R"}'
```

### Schema

`chats` — chat_id (PK), user_id, codex_thread_id, worktree_path, git_branch,
created_at, updated_at, status

`jobs` — job_id (PK), chat_id, user_id, codex_thread_id, request, started_at,
ended_at, worktree, branch, commit_before, commit_after, files_changed (json),
commands_run (json), result, status, error, maintainer_action_required,
preview_requested, preview_url, preview_error, handoff_path

`events` — id, job_id, ts, kind, payload (json)

`user_id` is recorded per chat and per job even though the planned browser login
is a single shared account, so per-user attribution can be switched on later
without a migration. See `docs/INGRESS.md`, "Known limitations".

---

## Operations

```bash
cd /home/yeli/services/compliance-client-agent

# One-time setup
uv venv gateway/.venv && uv pip install --python gateway/.venv/bin/python \
    fastapi 'uvicorn[standard]' pydantic pyyaml httpx pytest pytest-asyncio
# .env already exists (mode 600) and holds COMPLIANCE_GATEWAY_SECRET plus the
# shared-login credentials. Do not recreate or overwrite it.
./scripts/bootstrap_repo.sh               # create state/repo.git

# The gateway runs as a systemd USER unit (installed 2026-08-19). Lingering is
# enabled for yeli, so it starts at boot without anyone logging in.
systemctl --user status  compliance-agent-gateway.service
systemctl --user restart compliance-agent-gateway.service
systemctl --user stop    compliance-agent-gateway.service

# Start by hand instead (only if the unit is stopped — the port is single-bind)
./scripts/run_gateway.sh                                   # foreground
nohup ./scripts/run_gateway.sh > logs/gateway.log 2>&1 &   # background

# Health
curl -s http://127.0.0.1:8642/health | python3 -m json.tool

# Logs
tail -f logs/gateway.log

# Confirm it is loopback-only (must show 127.0.0.1, never 0.0.0.0)
ss -ltnp | grep 8642

# --- Open WebUI (the browser UI) ---
docker compose ps
docker compose logs -f webui
docker compose up -d          # apply a compose.yml change / recreate
docker compose restart webui
docker compose down           # stop the UI; the gateway is unaffected

# Signup must read false in the RUNNING container, always.
docker inspect compliance-webui --format '{{json .Config.Env}}' \
  | tr ',' '\n' | grep ENABLE_SIGNUP

# Tests
gateway/.venv/bin/python -m pytest gateway/tests -q -rxX

# Backup: the database and the client branches are the state worth keeping.
sqlite3 state/gateway/gateway.sqlite3 ".backup 'backup-$(date +%F).sqlite3'"
tar czf worktrees-$(date +%F).tar.gz state/repo.git

# List recent jobs, identities, workspaces, commits, changed files, and previews
python3 scripts/inspect_client_activity.py

# Also show exactly what clients asked (request text may contain sensitive content)
python3 scripts/inspect_client_activity.py --requests --limit 50

# Inspect one client's committed edits using the worktree_path from the query
git -C state/worktrees/<dir> log --oneline --decorate
git -C state/worktrees/<dir> show --stat --oneline HEAD
git -C state/worktrees/<dir> diff <commit_before>..<commit_after>

# Inspect commands and events recorded for one job
python3 scripts/inspect_client_activity.py --events <job-id>

# Clear a needs_attention chat (deliberately manual — inspect first)
git -C state/worktrees/<dir> status
sqlite3 state/gateway/gateway.sqlite3 \
  "UPDATE chats SET status='active' WHERE chat_id='<chat-id>';"
```

Relocating to `/srv` under a service user: see `docs/RELOCATION.md`.

---

## Bringing up the client UI

Open WebUI **is** deployed, as a dedicated container defined by `compose.yml`.
It is not the host's general-purpose Open WebUI and shares nothing with one.

- Image `ghcr.io/open-webui/open-webui:0.10.2`, pinned by tag **and** digest.
  Never `:latest` — an unattended image bump would silently change the auth
  surface of a service that reaches a code-executing agent.
- `network_mode: host` with `HOST=127.0.0.1`. The container is on the host's
  network namespace precisely so it can reach the gateway on `127.0.0.1:8642`,
  and it pins itself to loopback so that convenience does not become exposure.
  The alternative — making the gateway listen on `0.0.0.0` so a bridged
  container could reach it — was rejected outright.
- Persistent state binds to `state/webui-data`, seeded once from the image by
  `scripts/init_webui_data.sh` so `OFFLINE_MODE=true` has models to find.
- `no-new-privileges:true`, all capabilities dropped.

### Accounts — Lennon's hands only

No agent creates accounts or sets passwords here. The user table starts empty.

1. Reach the UI (see "LAN ingress" below).
2. The first load offers **Create Admin Account** even with `ENABLE_SIGNUP=false`
   — on 0.10.2 the signup route skips the check while the user table is empty.
   Create **your own admin account** first.
   If that screen does not appear, the `compose.firstrun.yml` overlay exists for
   exactly this case; its own header documents the flip back.
3. Create the shared client account from the credentials in `.env`.
4. Confirm `ENABLE_SIGNUP` is still `false` in the running container.

### The pipe

`openwebui/compliance_agent_pipe.py` is written but **not yet installed** — it
needs an admin account to exist first.

The pipe is a transport and nothing else: no shell execution, no filesystem
access, no git, no credentials beyond the gateway secret read from the
environment. It takes `chat_id` from Open WebUI's `__metadata__` and the user id
from `__user__`, POSTs to the gateway, streams status lines through
`__event_emitter__`, and returns the final message with files changed and the
commit hash.

Install: admin panel → Functions → Add, paste the file, save, enable it.
`COMPLIANCE_GATEWAY_URL` and `COMPLIANCE_GATEWAY_SECRET` are **already** in the
container's environment via `compose.yml` + `.env`; do not put the secret in a
valve default, which would persist it into the Open WebUI database.

---

## LAN ingress

**Status: applied 2026-08-19.** nginx block installed and reloaded; the only
remaining gate is the ufw rule below. Design rationale in `docs/INGRESS.md`.

Lennon reaches this host remotely at `10.48.50.117`, so loopback-only is not a
usable end state. The settled design is:

```
http://10.48.50.117/complianceAgent  --301-->  https://10.48.50.117:8443/
```

**Why a port and not a clean `/complianceAgent/` subpath.** Open WebUI 0.10.2
has no base-path setting. Its built SPA references `/static/`, `/_app/` and
`/api/` as root-absolute paths, and after login its client-side router rewrites
the address bar to `/`. Hosted under a prefix, the first load 404s against the
devportal and navigation escapes the prefix entirely. Rewriting it with nginx
`sub_filter` would be a permanent, silently-breaking maintenance burden. The
port is the honest answer; the redirect preserves the address Lennon wanted.

nginx terminates TLS on 8443 and proxies to `127.0.0.1:8080`. The container and
the gateway both stay on loopback — nginx is the only thing exposed. TLS is not
optional here: the shared login password would otherwise cross the office LAN in
cleartext.

The server block lives at `nginx/compliance-agent.conf` in this tree, with
deployed copies in both `/etc/nginx/sites-available/` and `sites-enabled/`
(a regular file, not a symlink — `ln` is not in the NOPASSWD grant). Edit the
tree copy and reinstall both; do not let them diverge. Note `listen 8443 ssl
http2;` rather than the modern `http2 on;` — this host runs nginx 1.24.0, which
predates the split directive and rejects the newer form outright.

`CORS_ALLOW_ORIGIN` in `compose.yml` must name the ingress origin exactly
(`https://10.48.50.117:8443`) and move in lockstep with `WEBUI_URL`. If it
still points at loopback the page loads and then every API call is refused,
which reads as a broken UI rather than a config error. It is deliberately not
`*`: that would let any page on the LAN drive the API with the client's session.

Still gated on the maintainer, because `ufw` is not in the NOPASSWD grant:

```bash
sudo ufw allow from 10.48.50.0/24 to any port 8443 proto tcp comment 'compliance agent UI'
```

The certificate is self-signed, so the browser warns once per client. Check the
fingerprint before clicking through:
`openssl x509 -in secrets/compliance-agent.crt -noout -fingerprint -sha256`

**No nginx Basic Auth in front.** `secrets/.htpasswd` exists and `docs/INGRESS.md`
designs it, but it is deliberately not applied: Open WebUI's own login already
satisfies the requirement that a stranger who knows the address cannot get in,
and a second prompt is friction a client will route around. It is one
`auth_basic` pair of lines away if that judgement changes.

---

## Known residual risks

`tests/test_security.py` and `tests/test_jail.py` encode all of this. Containment
assertions are real tests against a real jail, not mocks — the whole question is
whether the kernel actually stops this.

**Test counts: 126 total — 125 pass, 1 xfail.**

### Closed by the jail — verified, not asserted

Each was previously an accepted permanent risk. Each is now a passing
containment test in `tests/test_jail.py`.

| Risk | Now |
|---|---|
| Writes outside the workspace | Blocked — read-only filesystem |
| `~/.ssh` readable | Not visible |
| Other repos under `/home/yeli` readable | Not visible |
| `backup/data/people.rds` readable | Not visible (checkout not bound) |
| `/srv/shiny-server` writable | Not visible |
| `sudo` usable | Fails on `no_new_privs` |
| Docker socket reachable | Not bound |
| Codex credential writable | Read-only bind |

### Still open

| # | Risk | Status |
|---|---|---|
| 1 | **Network egress from the codex process.** The jail keeps the network because Codex needs the API. Agent-spawned commands are denied network by the inner sandbox, but the codex process itself has egress. Not a boundary against a compromised binary or model-side exfiltration. | Accepted, by necessity |
| 2 | **The gateway process is unjailed.** It runs as `yeli` with full sudo rights; only the agent is contained. A gateway bug, or a code path that forgets to wrap, is unprotected. `jail.required: true` and the jail tests are the guard. | Accepted |
| 3 | **`yeli` still holds NOPASSWD root and docker membership.** Unchanged, and unfixable here — it needs root. The jail denies the agent its use; it does not remove it. | Accepted, permanent |
| 4 | **Prompt injection via free text.** The API rejects *structured* overrides, but `message` is natural language handed to a model and is never screened. Containment does not fix this — it bounds it to the workspace. | Open (`xfail`) |
| 5 | **Kernel / bubblewrap escape.** A user-namespace escape defeats the containment entirely. Unprivileged userns has a busy CVE history. | Accepted, inherent |
| 6 | **The agent can wreck its own workspace.** By design. Nothing is auto-merged, so the blast radius is one chat's clone. | By design |

### Also worth knowing

- **The Codex credential is readable inside the jail.** It is bound read-only so
  it cannot be altered or deleted, but a shell command can read it. Exfiltration
  is blocked by the inner sandbox's network denial, which is a mitigation rather
  than a boundary. Avoiding this entirely would need a credential-broker
  process, which Codex does not support.
- **`~/.local/bin` is bound read-only.** Codex resolves helper binaries through
  a symlink chain, so the directory has to be visible. It exposes tool binaries,
  read-only, and nothing else.
- **`AGENTS.md` is not yet in the service clone.** It is uncommitted in the
  maintainer's checkout. It appears once Lennon commits it and the clone is
  refreshed.
- The single-job lock is **process-wide**, not host-wide.
- SQLite is WAL with a single writer. Fine at this scale.

## Deferred — not started

Public exposure of any kind, Tailscale ingress, and DNS. Nothing here is
reachable from outside `10.48.50.0/24` once the ufw rule above is applied, and
that is the intended end state.

Still open, in order:

| # | Item | Whose hands |
|---|---|---|
| 1 | ~~Apply the nginx 8443 block + `/complianceAgent` redirect~~ | **done 2026-08-19** |
| 2 | `sudo ufw allow from 10.48.50.0/24 to any port 8443 proto tcp` | **Lennon** — ufw needs a password |
| 3 | Create the admin account, then the shared client account | **Lennon** — no agent creates accounts |
| 4 | Install the pipe (needs #3) | Lennon, in the admin panel |
| 5 | Browser end-to-end: one real chat producing one real commit | Lennon |
| 6 | Commit `AGENTS.md` in the source repo and refresh the service clone | **Lennon** — it is still uncommitted, so the agent runs with no repo instructions |
| 7 | Prune the leftover test clones in `state/worktrees/` (~87 MB) | agent, on request |

A dedicated service UID is **not** deferred: it has been declined. See "Runtime
identity" above.

## Layout

```
├── README.md
├── .env                      # mode 600, gitignored, NOT in this listing's scope
├── .env.example              # no real secrets
├── compose.yml               # the compliance-webui container; pinned by digest
├── compose.firstrun.yml      # first-admin overlay; never used on its own
├── secrets/
│   ├── .htpasswd             # mode 600, dir 700, gitignored — not currently applied
│   └── compliance-agent.{crt,key}   # self-signed, SAN IP:10.48.50.117
├── nginx/                    # (pending) source of truth for the 8443 TLS block
├── systemd/
│   └── compliance-agent-gateway.service   # installed as a USER unit
├── scripts/
│   ├── bootstrap_repo.sh     # one-time service clone
│   ├── init_webui_data.sh    # seed state/webui-data from the image
│   └── run_gateway.sh
├── gateway/
│   ├── config.yaml           # server-owned config; relative paths
│   ├── pyproject.toml
│   ├── app/
│   │   ├── config.py         # relocatable path resolution
│   │   ├── db.py             # sqlite schema + durable queue
│   │   ├── gitops.py         # service clone, worktrees, diffs
│   │   ├── codex_runner.py   # argv construction, sandbox enforcement
│   │   ├── schemas.py        # the client-override guard
│   │   ├── worker.py         # single-slot asyncio worker
│   │   └── main.py           # FastAPI routes
│   └── tests/                # 126 tests: 125 pass, 1 xfail
├── openwebui/
│   └── compliance_agent_pipe.py   # written, NOT installed (needs an admin account)
├── memory/                   # LOCAL project memory, bound read-only into the jail
├── docs/
│   ├── INGRESS.md            # ingress + shared-login design
│   └── RELOCATION.md         # moving to /srv under a service user
├── evidence/
│   └── phase4.log            # real end-to-end terminal output
└── state/
    ├── repo.git/             # service-owned bare clone
    ├── worktrees/            # one per chat
    ├── codex_home/           # dedicated CODEX_HOME (no auth.json by design)
    ├── webui-data/           # Open WebUI database + model cache (mode 700)
    └── gateway/gateway.sqlite3
```
