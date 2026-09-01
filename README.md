# Client Agent Services

Private, sanitized source of truth for the Open WebUI client agents hosted on
Asgard:

- `compliance-client-agent/` — Jer, for the compliance R/Shiny project.
- `gambling-client-agent/` — Gamble, for the gambling dashboards.

Each service contains its gateway, Open WebUI Pipe, installer, service unit,
service-local memory, tests, and non-secret configuration templates. Runtime
state remains under `/home/yeli/services/<service>` and is deliberately not
versioned.

## Security boundary

Client agents work only in gateway-created `client/<project>/*` workspaces.
They cannot select `main`, another repository, credentials, sandbox settings,
or deployment targets. They cannot push, use `sudo`, or deploy production.
An explicit `/preview` request lets the trusted gateway update only the fixed,
allowlisted test target.

Direct maintainer agents are launched separately in the project checkout on
`main`; client chat text cannot select or inherit that role.

## Recovery

1. Copy the selected service directory to
   `/home/yeli/services/<service-name>`.
2. Create `.env` from `.env.example` and supply secrets and the permitted Open
   WebUI user ID locally.
3. Bootstrap the service-owned repository using `scripts/bootstrap_repo.sh`.
4. Restore the provider login into the service-owned state directory.
5. Install/start the user service, load the local `.env`, and run
   `scripts/install_openwebui_pipe.py` so the permitted user ID is explicit.
6. Run the full gateway test suite and verify the health and preview URLs.

Do not restore databases, worktrees, credentials, or client activity from this
repository. Back those up separately only when explicitly required and under
their own access controls.
