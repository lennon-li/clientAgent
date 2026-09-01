# Gambling dashboard agent — Current state

Last verified: 2026-09-01

- Source repository: `/home/yeli/repos/gambling`, default branch `main`.
- Entry points: `gambling-dashboard.qmd`,
  `connexontario-gambling-dashboard.qmd`, `osduhs-gambling-dashboard.qmd`, and
  `hospital-gambling-dashboard.qmd`.
- Local validation command: `quarto check`.
- Raw inputs under `data/` and rendered dashboard artifacts are intentionally
  untracked and excluded from client-agent worktrees.
- The gateway listens only on `127.0.0.1:8643`, creates
  `client/gambling/<chat-id>` branches in its service-owned clone, and never
  pushes or modifies the source checkout.
- A `/preview` request asks the trusted gateway to render and publish only the
  four allowlisted dashboard HTML files to `/srv/shiny-server/test/gambling`.
  Approved local inputs are mounted read-only into the isolated render process;
  they are never exposed to the client agent.
