# compliance — Handoff Log

## 2026-08-19 — Ming (Claude Code, asgard): client UI deployed, LAN ingress applied

Paused mid-sequence at Lennon's request. **Resume at `TODO.md` → "RESUME HERE"**,
which is a five-step ordered list starting with the one command no agent can run.

Done and verified this session:

- **Gateway under systemd.** User unit installed at
  `~/.config/systemd/user/compliance-agent-gateway.service`, enabled and active.
  Lingering was already on for `yeli`, so it survives reboot — the unit's own
  header comment claiming lingering "has not been run" is stale.
- **Open WebUI deployed.** `compose.yml`, image pinned by tag *and* digest,
  `network_mode: host` with `HOST=127.0.0.1`. Left behind by an agent Lennon
  killed mid-task; assessed and found complete except documentation.
- **LAN ingress applied.** nginx TLS on 8443 → `127.0.0.1:8080`, plus
  `http://10.48.50.117/complianceAgent` → 301 → `https://10.48.50.117:8443/`.
  Implemented by Wei (OpenCode, `opencode-go/qwen3.7-plus`) from a written
  packet; every claim re-verified independently afterwards.
- **README** brought back in line with reality — it had been asserting Open
  WebUI was not installed and the test count was 124.

Three things worth carrying forward, because each cost real time:

1. **Open WebUI 0.10.2 cannot be hosted under a URL subpath.** Its built SPA
   references `/static/`, `/_app/` and `/api/` as root-absolute, and its router
   rewrites the address bar to `/` after login. The requested
   `10.48.50.117/complianceAgent` is therefore a redirect to a dedicated TLS
   port, not a real prefix. Do not retry the subpath.
2. **nginx here is 1.24.0** and rejects the modern `http2 on;` directive. Use
   `listen 8443 ssl http2;`. Wei caught this before the reload.
3. **`CORS_ALLOW_ORIGIN` must track `WEBUI_URL`.** It was still pointing at
   loopback after the ingress landed; the page would have loaded and then every
   API call would have been refused, which reads as a broken UI rather than a
   config error. Now `https://10.48.50.117:8443`, deliberately not `*`.

State at pause: tests 125 passed / 1 xfailed; Open WebUI user table 0 rows (no
agent has created an account, by rule); 8080 and 8642 loopback-only, 8443 the
only wide listener; shared password present in `.env` alone.

## 2026-08-18 — Ming (Claude Code, asgard): project memory created

Created this memory tree. There was no `projects/compliance/` before today and
no entry in `projects/INDEX.md`; both were added in this pass.

Content is limited to what was verifiable from the repository on disk
(DESCRIPTION, NAMESPACE, README.md, DEPLOYMENT.md, .gitignore, git log, test
layout). No project history was reconstructed beyond the three existing commits.

Also built the local-only compliance client agent gateway at
`/home/yeli/services/compliance-client-agent`. See its README for the
architecture, the residual-risk list, and the top blocker for any external
exposure.
