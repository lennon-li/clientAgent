# Gamble — Gambling Dashboard Agent

Gamble is an Open WebUI Pipe backed by a loopback-only gateway at
`127.0.0.1:8643`. Each chat gets a separate clone and
`client/gambling/<chat-id>` branch from the service-owned bare clone; the
gateway commits completed changes locally. It never pushes, opens PRs, merges,
or touches the source checkout after initial bootstrap.

## Trusted role boundary

There are two distinct launch paths. A maintainer starts a normal interactive
CLI directly in `/home/yeli/repos/gambling` on `main`; that developer role can
use approved `agentData/`, commit and push reviewed source, and operate the
fixed trusted test preview. It is not launched through this service.

Open WebUI always launches Gamble through this gateway. The server-owned bare
clone, `client/gambling/*` branch, per-chat worktree, request schema,
Bubblewrap jail, explicit Copilot deny rules, and fixed preview target enforce
the client role. Chat text cannot select the maintainer checkout, `main`, data
paths, credentials, push access, or another deployment target. Do not add a
"developer mode" valve or prompt switch to the Pipe.

## Data and preview boundaries

Raw `data/` inputs contain client-contact information and are excluded from
the source repository and every agent worktree. Generated dashboard HTML and
caches are excluded as well. The agent may run `quarto check` and edit source,
but it cannot render against real data.

An explicit `/preview` request may ask the trusted gateway to render and
publish only these files:

- `gambling-dashboard.html`
- `connexontario-gambling-dashboard.html`
- `osduhs-gambling-dashboard.html`
- `hospital-gambling-dashboard.html`

to `/srv/shiny-server/test/gambling`, at
`http://10.48.50.117/shiny/test/gambling/`. The render receives approved inputs
through gateway-owned read-only mounts; the client agent never receives direct
data access. Production deployment is not implemented.

## Operations

1. Create the service-owned bare clone: `./scripts/bootstrap_repo.sh`.
2. Put a unique `GAMBLING_GATEWAY_SECRET` in `.env` (mode `600`) and in the
   Open WebUI container environment.
3. Install and start `systemd/gambling-agent-gateway.service`.
4. Load the local `.env`, then run `python3 scripts/install_openwebui_pipe.py`
   to idempotently install and enable the Pipe in Open WebUI. For example:
   `(set -a; . ./.env; set +a; python3 scripts/install_openwebui_pipe.py)`.

The agent runs inside Bubblewrap with only its chat worktree, its dedicated
Copilot state, the required binaries, and read-only service-local memory. The
source checkout, host credentials, other repositories, Docker socket, and
`/srv` are not exposed to the agent process.
