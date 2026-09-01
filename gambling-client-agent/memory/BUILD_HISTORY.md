# Gamble — Gambling Dashboard Agent history

## 2026-09-01 — initial build

- Created the service at `/home/yeli/services/gambling-client-agent`.
- Isolated each Open WebUI chat into a local `client/gambling/*` branch and
  clone of a service-owned bare repository.
- Configured GitHub Copilot CLI inside the bubblewrap jail, with its own
  service state and no access to the source checkout, raw data, credentials,
  SSH keys, Docker socket, or production paths.
- Added the Open WebUI Pipe named **Gamble — Gambling Dashboard Agent**.
- Reserved `http://10.48.50.117/shiny/test/gambling/` as the sole optional
  non-production static-preview URL.

## Operating constraints

The dashboard's source data and generated outputs are deliberately excluded
from agent worktrees. Source changes may be committed without a render, but a
real-data render or preview requires the maintainer to provide a safe,
authorized workflow. Gamble never pushes, merges, deploys production, or
accesses raw client data.
