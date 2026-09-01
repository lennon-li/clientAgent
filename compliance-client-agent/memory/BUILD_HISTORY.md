# Jer / Compliance Client Agent — Build History

This is a non-secret operational history for Jer. It contains no credentials,
tokens, or employee data. It records the decisions that explain the current
agent setup so a new chat can continue the work without relying on an earlier
conversation.

## 2026-08-18 to 2026-08-28 — initial build

- Built a local compliance client-agent gateway under
  `/home/yeli/services/compliance-client-agent`.
- The gateway runs on loopback port `8642` and uses a service-owned bare clone
  of the compliance repository.
- Each chat receives its own feature branch named
  `client/compliance/<chat-id>` and its own worktree. The gateway commits the
  work at the end of a job; it never pushes or merges.
- Added bubblewrap containment so the agent can write only its worktree and
  service-owned runtime state. Production Shiny files, Docker, SSH keys, and
  the maintainer checkout are not exposed to the agent.
- Switched the provider to GitHub Copilot using the maintainer-configured OAuth
  login. The configured model is `claude-sonnet-5`.
- Installed the Open WebUI pipe and named the client-facing agent **Jer**.
- Added a model access record granting signed-in users read-only access to
  Jer; regular users can now select it while remaining unable to edit the
  function or its model configuration.
- Fixed the Open WebUI websocket origin configuration so progress and final
  replies render instead of remaining stuck on “Thinking…”.
- The gateway mounts this service-local memory tree read-only. The old shared
  `/home/yeli/obsidian` memory path is intentionally unavailable inside the
  client-agent sandbox.
- Provisioned a non-production Shiny preview location at
  `/srv/shiny-server/test/compliance`. Its user-facing LAN URL is:
  `http://10.48.50.117/shiny/test/compliance/`.

## Operating contract

- Jer accepts new client instructions about compliance-report changes and user
  testing, while preserving the repository and production safety rules.
- Prefix a request with `/preview` when the client wants the current feature
  branch made available for user testing.
- After a successful preview deployment, Jer gives the LAN URL above and asks
  the client to open it, test the requested behavior, and report back.
- A maintainer reviews the branch and creates the PR. Jer does not push, merge,
  or deploy the live `/srv/shiny-server/compliance` application.

## Known activation note

After changing gateway configuration or bootstrap instructions, restart the
user gateway service so the running process loads the new files:

`systemctl --user restart compliance-agent-gateway.service`
