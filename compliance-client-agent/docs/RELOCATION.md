# Relocating the service — optional

**Status: not planned, and not required.** The service runs from
`/home/yeli/services/compliance-client-agent` as `yeli`, permanently, by
decision on 2026-08-18. No service account is being created.

This document is kept because the relocatability property costs nothing to
maintain, is already enforced by tests, and a move to `/srv` may still happen
later for unrelated reasons. Nothing here is a prerequisite for anything, and
none of it has been done.

The tree is position-independent. Nothing under `gateway/app/` contains a
hardcoded absolute path, and every path in `config.yaml` is either relative to
the tree root or overridable by an environment variable.

## Tree root resolution

1. `$COMPLIANCE_AGENT_HOME` if set
2. otherwise, the parent of the directory holding `config.yaml`
   (`<tree>/gateway/config.yaml` → `<tree>`)

`Config.runtime_paths()` returns every path the running gateway may touch, and
`tests/test_repo_isolation.py` asserts they all resolve inside the tree root.

## If a move to /srv ever happens

Two separable things get conflated here, so worth stating clearly: relocating
the tree and changing the runtime user are independent. Relocation alone
changes nothing about privilege — it just moves files. The steps below include
the service-account variant for completeness, but creating that account has
been explicitly declined, so in practice a move would keep `User=yeli` and skip
steps 1 and 4.

```bash
# 1. Create the service account. NOT in the docker group, no sudoers entry.
sudo useradd --system --home-dir /srv/compliance-client-agent \
             --shell /usr/sbin/nologin svc-compliance

# 2. Copy the tree, minus state and virtualenv.
sudo mkdir -p /srv/compliance-client-agent
sudo rsync -a --exclude state/ --exclude logs/ --exclude gateway/.venv/ \
    /home/yeli/services/compliance-client-agent/ /srv/compliance-client-agent/
sudo chown -R svc-compliance:svc-compliance /srv/compliance-client-agent

# 3. Rebuild the virtualenv as the service user.
sudo -u svc-compliance uv venv /srv/compliance-client-agent/gateway/.venv
sudo -u svc-compliance /srv/compliance-client-agent/gateway/.venv/bin/python \
     -m pip install -e /srv/compliance-client-agent/gateway

# 4. Give the service its own Codex credential. Do NOT reuse the maintainer's.
sudo -u svc-compliance env \
     CODEX_HOME=/srv/compliance-client-agent/state/codex_home codex login

# 5. Bootstrap a fresh service clone. Requires read access to the source repo,
#    which is a one-time grant, not an ongoing one.
sudo -u svc-compliance env COMPLIANCE_AGENT_HOME=/srv/compliance-client-agent \
     /srv/compliance-client-agent/scripts/bootstrap_repo.sh

# 6. Verify nothing still points at /home/yeli.
sudo -u svc-compliance env COMPLIANCE_AGENT_HOME=/srv/compliance-client-agent \
     /srv/compliance-client-agent/gateway/.venv/bin/python -m pytest \
     /srv/compliance-client-agent/gateway/tests/test_repo_isolation.py -q
```

Step 5 is the only moment the service account needs to see
`/home/yeli/shiny/compliance`, and only for reading. After the clone exists,
revoke it. The gateway never opens the source repo again.

## What changes after relocation

- `memory_index` now points at `memory/` INSIDE the tree, so it relocates with
  everything else and needs no external bind. (It formerly pointed into
  `/home/yeli/obsidian`, which the jail's NEVER_BIND made unreadable.) The
  service user will not be able to read it (`/home/yeli` is mode 750). Either
  copy the memory tree somewhere readable and set `COMPLIANCE_MEMORY_INDEX`, or
  accept that the agent's bootstrap will note the file is missing and continue.
- If the runtime user stays `yeli` (the current decision), Codex authentication
  is unaffected: `resolve_codex_home()` keeps using `~/.codex`, which remains
  readable. Only a change of user would require step 4.
- If the runtime user ever *did* change, the ACCEPTED RISK tests in
  `tests/test_security.py` would start **failing** — they assert the current
  permissive reality (sudo present, `~/.ssh` readable, docker group membership).
  That failure would be the correct signal that the host got safer, and the
  right response would be to rewrite each one as an enforced guarantee and
  update the README's risk register.

## systemd unit (not installed)

```ini
[Unit]
Description=Compliance client agent gateway
After=network.target

[Service]
Type=exec
# Current decision: runs as yeli. Change only alongside a deliberate
# privilege decision -- see the README's risk register.
User=yeli
Group=yeli
Environment=COMPLIANCE_AGENT_HOME=/srv/compliance-client-agent
EnvironmentFile=/srv/compliance-client-agent/.env
WorkingDirectory=/srv/compliance-client-agent/gateway
ExecStart=/srv/compliance-client-agent/gateway/.venv/bin/uvicorn app.main:app \
          --host 127.0.0.1 --port 8642
Restart=on-failure

NoNewPrivileges=yes
PrivateTmp=yes
ProtectSystem=strict
ProtectHome=yes
ReadWritePaths=/srv/compliance-client-agent/state /srv/compliance-client-agent/logs

[Install]
WantedBy=multi-user.target
```

Note the tension in that unit: `ProtectHome=yes` would close several residual
risks outright by making `/home` invisible to the service — but with
`User=yeli` it would also hide `~/.codex` (breaking Codex authentication) and
the project memory tree under `~/obsidian`. Running as `yeli` and hardening
against `/home` are mutually exclusive; pick one knowingly. As written, keep
`ProtectHome=` off while `User=yeli`.

Installing this needs root and has not been done.
