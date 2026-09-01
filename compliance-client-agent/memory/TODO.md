# compliance — TODO

Last updated: 2026-08-19

## RESUME HERE — client agent UI, five steps to first real chat

Everything on the machine side is built, running, and verified. What remains is
a short sequence that starts with one command only Lennon can run. Do these in
order; each one unblocks the next.

- [ ] **1. Open the firewall.** `ufw` is not in the NOPASSWD grant, so no agent
      can do this. One command, once:
      ```bash
      sudo ufw allow from 10.48.50.0/24 to any port 8443 proto tcp comment 'compliance agent UI'
      ```
      Until this runs, port 8443 is firewalled and the UI is unreachable from
      anywhere but asgard itself. Every check recorded below was made from
      loopback, so **reachability from a remote machine is still unproven** —
      it is the one link in the chain that has not been tested.

- [ ] **2. Open the UI and accept the certificate.** Browse to
      `http://10.48.50.117/complianceAgent`, which 301s to
      `https://10.48.50.117:8443/`. The certificate is self-signed, so the
      browser warns once per client. Verify before clicking through:
      ```
      SHA-256 99:E7:47:6D:B7:8B:0F:94:D9:B1:D4:B1:B7:4B:3B:C3:AB:6A:7F:2E:79:41:9A:3B:2E:8E:02:E2:24:BF:4C:47
      ```
      Re-read it any time with:
      `openssl x509 -in ../secrets/compliance-agent.crt -noout -fingerprint -sha256`

- [ ] **3. Create the accounts — Lennon's hands only, no agent.** The user
      table is empty (verified 0 rows). On 0.10.2 the first load offers
      "Create Admin Account" even with `ENABLE_SIGNUP=false`, because the signup
      route skips the check while the table is empty.
      1. Create **your own admin account** first.
      2. Then the shared client account, credentials in `.env` (mode 600).
      3. Confirm signup is still closed:
         `docker inspect compliance-webui --format '{{json .Config.Env}}' | tr ',' '\n' | grep ENABLE_SIGNUP`
      If the "Create Admin Account" screen does not appear, use the
      `compose.firstrun.yml` overlay — its own header documents the flip back.

- [ ] **4. Install the pipe.** Needs step 3 done first. Admin panel →
      Functions → Add, paste `openwebui/compliance_agent_pipe.py`, save, enable.
      `COMPLIANCE_GATEWAY_URL` and `COMPLIANCE_GATEWAY_SECRET` are **already**
      in the container environment via `compose.yml` + `.env` — do not put the
      secret in a valve default, which would persist it into the Open WebUI
      database.

- [ ] **5. End-to-end acceptance.** One real chat that produces one real commit
      on a `client/compliance/*` branch in `state/repo.git`. This is the first
      test of the whole path — nothing before it exercises the pipe or the
      gateway from a browser.

## Immediate (project repo, unrelated to the UI)

- [ ] Review and commit (or reject) the uncommitted `AGENTS.md` in the repo
      root. **This matters more now**: the service clone does not contain it,
      so the client agent currently runs with no repo instructions at all. It
      appears once the commit lands and the clone is refreshed.
- [ ] Decide whether the legacy root-level `app.R`, `app-2026-04-21.R`,
      `appApril14.R`, `appold.R`, `appold1.R` and the built `.tar.gz` / `.zip`
      should be pruned now that the packaged app lives at `inst/app/app.R`.
- [ ] Test coverage is one file (`test-compliance-rulebook.R`) against 22 files
      in `R/`. Decide a target and which functions matter most.

## Housekeeping (agent may do on request)

- [ ] Prune the leftover test clones in `state/worktrees/` (~87 MB, 9 dirs).
      Deliberately left in place; nobody has asked for them to go.

## Blocked / needs Lennon

- [ ] Nothing else. The old blocker on this list — "any external exposure is
      blocked on the privilege issue" — has been **superseded**, not resolved.
      `yeli` still holds NOPASSWD sudo and docker membership, so the gateway
      process is still effectively root. What changed is that the *agent* is
      now contained by a bubblewrap jail that defeats that privilege from the
      inside via `no_new_privs`, and exposure is scoped to the LAN behind
      nginx + a login. See the README risk register, items 2 and 3, which
      record both as accepted and permanent.
