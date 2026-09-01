# Ingress — design only, nothing applied

**Status: NOT APPLIED.** No nginx config was written, no firewall rule was
added, no TLS certificate was issued, no Open WebUI instance was installed, and
no user account was created. This document is a design for Lennon to approve or
reject. Every command below is written out so it can be reviewed before it is
ever run.

The gateway itself stays on `127.0.0.1` permanently. It is not the thing that
gets exposed, and `app/config.py` refuses to load a non-loopback host. What
would be exposed, if anything, is Open WebUI in front of it.

## Current host facts (verified 2026-08-18)

- LAN address `10.48.50.117`; NAT egress `207.34.197.4`; no public DNS, nothing
  listening on 443.
- nginx is active and owns port 80 with `server_name _`, serving `/rstudio/`,
  `/shiny/`, `/r/` and `/PAGe/` from `/etc/nginx/sites-available/devportal`.
- Caddy is not installed and should not be.
- Tailscale is present; asgard is `100.80.45.60`.
- Shiny Server is on `:3838`.

## Proposed shape

```
browser on the LAN
      |
      v
nginx :80  (existing devportal server block, new /compliance-agent/ location)
      |
      v
Open WebUI 127.0.0.1:8080     <- browser-facing auth lives here
      |
      v  (compliance_agent_pipe.py)
gateway 127.0.0.1:8642        <- shared-secret header only
      |
      v
codex exec --sandbox workspace-write --cd <worktree>
```

Two things must stay true: the gateway never binds anything but loopback, and
the shared secret never reaches the browser. The pipe runs inside Open WebUI on
the server, so the secret stays server-side.

---

## Authentication

Lennon's requirement: a single generic login, so someone on the LAN who happens
to know the address cannot simply walk in. Two independent gates are described
below; use both, since each covers a failure of the other.

**The credentials already exist.** They were created by Lennon on 2026-08-18 and
must not be recreated or overwritten:

| File | Mode | Contents |
|---|---|---|
| `.env` | `600` | `COMPLIANCE_CLIENT_USER`, `COMPLIANCE_CLIENT_PASSWORD`, Open WebUI hardening vars, and `COMPLIANCE_GATEWAY_SECRET` |
| `secrets/.htpasswd` | `600` (dir `700`) | user `compliance`, apr1 hash, for nginx Basic Auth |

The plaintext password lives in `.env` and **nowhere else** — not in this
document, not in the README, not in a test fixture, not in a log line. Every
reference anywhere else is to the variable name `COMPLIANCE_CLIENT_PASSWORD`.
Both files are covered by `.gitignore`.

Note that `COMPLIANCE_GATEWAY_SECRET` is machine-generated random hex and is
deliberately **not** the login password. They are separate credentials serving
separate purposes: the login gates the browser, the gateway secret gates the
loopback API. Never make them the same value.

### Gate 1 — Open WebUI, one shared account, signups closed

The part that catches people out: **the first account ever created becomes an
administrator**, whatever the settings say. So the order matters.

Environment for the Open WebUI service (values from `.env`):

```bash
ENABLE_SIGNUP=false          # close registration
DEFAULT_USER_ROLE=pending    # anyone who slips through gets no access
ENABLE_COMMUNITY_SHARING=false
ENABLE_ADMIN_EXPORT=false
WEBUI_AUTH=true              # never False on a reachable instance

WEBUI_SECRET_KEY=<openssl rand -hex 32>

COMPLIANCE_GATEWAY_URL=http://127.0.0.1:8642
COMPLIANCE_GATEWAY_SECRET=<from .env>
```

First-run bootstrap order:

1. Start Open WebUI with `ENABLE_SIGNUP=true`, bound to `127.0.0.1` only,
   reachable from the server itself (e.g. an SSH tunnel). Not behind nginx yet.
2. Create the **admin** account first — Lennon's own, not the shared one.
3. Create the **shared** account second, from `Admin Panel → Users → Add User`,
   role `user`, username from `COMPLIANCE_CLIENT_USER` and password from
   `COMPLIANCE_CLIENT_PASSWORD` in `.env`.
4. Set `ENABLE_SIGNUP=false` and `DEFAULT_USER_ROLE=pending`, restart.
5. Verify from a second machine that the signup page is gone and a wrong
   password is rejected, **before** adding the nginx location block.

### Gate 2 — nginx, TLS + HTTP Basic Auth

An independent credential at the reverse proxy, so an unauthenticated request
never reaches the application at all — and TLS so neither credential crosses
the LAN in the clear.

**Materials already generated (not applied anywhere):**

| File | Mode | What it is |
|---|---|---|
| `secrets/.htpasswd` | `600` | user `compliance`, apr1 hash |
| `secrets/compliance-agent.crt` | `600` | self-signed cert, 825 days |
| `secrets/compliance-agent.key` | `600` | RSA 4096 private key |

The certificate covers `IP:10.48.50.117`, `IP:127.0.0.1`, `DNS:asgard`, and
`DNS:localhost`, so it validates whether reached by IP or hostname. It is
self-signed: browsers will warn until it is imported as a trusted root on each
client machine, or reissued from an internal CA. That warning is the only thing
distinguishing it from a CA-issued cert — the wire encryption is identical.

`secrets/.htpasswd` was generated with `openssl passwd -apr1`, because
`htpasswd` is **not installed** on this host (no `apache2-utils`, and
installing it needs root).

nginx runs as root and reads these at request time, so it needs access:

```bash
sudo install -o root -g root -m 600 \
    /home/yeli/services/compliance-client-agent/secrets/compliance-agent.key \
    /etc/ssl/private/compliance-agent.key
sudo install -o root -g root -m 644 \
    /home/yeli/services/compliance-client-agent/secrets/compliance-agent.crt \
    /etc/ssl/certs/compliance-agent.crt
sudo install -o root -g www-data -m 640 \
    /home/yeli/services/compliance-client-agent/secrets/.htpasswd \
    /etc/nginx/.htpasswd-compliance
```

To rotate the password later:

```bash
 openssl passwd -apr1            # leading space keeps it out of shell history
# then update BOTH secrets/.htpasswd and COMPLIANCE_CLIENT_PASSWORD in .env
```

**Complete server block.** This is a NEW server on a dedicated port (8443), so
it does not touch the existing `devportal` block on port 80 or any of the four
paths it serves:

```nginx
server {
    listen      10.48.50.117:8443 ssl;
    http2       on;
    server_name asgard 10.48.50.117;

    ssl_certificate     /etc/ssl/certs/compliance-agent.crt;
    ssl_certificate_key /etc/ssl/private/compliance-agent.key;

    ssl_protocols             TLSv1.2 TLSv1.3;
    ssl_prefer_server_ciphers off;
    ssl_session_cache         shared:ComplianceTLS:10m;
    ssl_session_timeout       1d;
    ssl_session_tickets       off;

    # Self-signed: no OCSP stapling, and no HSTS. HSTS would pin browsers to
    # HTTPS for this host across ALL ports, which would break the plain-HTTP
    # /rstudio/, /shiny/, /r/ and /PAGe/ paths on port 80.

    access_log /var/log/nginx/compliance-agent.access.log;
    error_log  /var/log/nginx/compliance-agent.error.log;

    # Agent jobs stream for minutes and clients paste long messages.
    client_max_body_size 25m;

    location / {
        auth_basic           "Compliance Agent";
        auth_basic_user_file /etc/nginx/.htpasswd-compliance;

        proxy_pass         http://127.0.0.1:8080;
        proxy_http_version 1.1;

        # Open WebUI needs websockets; without these the UI connects and hangs.
        proxy_set_header Upgrade    $http_upgrade;
        proxy_set_header Connection "upgrade";

        proxy_set_header Host              $host;
        proxy_set_header X-Real-IP         $remote_addr;
        proxy_set_header X-Forwarded-For   $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;

        # SSE progress must not be buffered or cut off mid-job.
        proxy_buffering    off;
        proxy_cache        off;
        proxy_read_timeout 3600s;
        proxy_send_timeout 3600s;
    }
}
```

Apply with `sudo nginx -t && sudo systemctl reload nginx` — **only after Lennon
has reviewed it**. Run `nginx -t` first, every time: a broken config takes down
`/rstudio/`, `/shiny/`, `/r/` and `/PAGe/` along with it. Port 8443 also has to
be opened in ufw (Gate 3), which is a separate deliberate step.

### Gate 3 (optional) — ufw allowlist for specific LAN hosts

If the audience is a handful of known machines, restrict by address rather than
serving the whole subnet:

```bash
sudo ufw status verbose                      # check current state first

sudo ufw allow from 10.48.50.41 to any port 8443 proto tcp comment 'compliance agent'
sudo ufw allow from 10.48.50.42 to any port 8443 proto tcp comment 'compliance agent'
```

Rather than the broad alternative:

```bash
# Wider, and correspondingly weaker:
sudo ufw allow from 10.48.50.0/24 to any port 8443 proto tcp
```

Using a dedicated port 8443 for this service is deliberate: it keeps the
allowlist scoped to the agent, so restricting it has no effect on port 80,
which serves `/rstudio/`, `/shiny/`, `/r/` and `/PAGe/` to other users. A ufw
rule is host-level, not path-level; had this shared port 80, tightening it
would have cut off those four paths too.

### Known limitations — state these plainly

**Attribution is lost.** With one shared login, every job, every commit, and
every row in the `jobs` table traces back to a single identity. The audit ledger
will faithfully record that "the compliance account" did something and will be
unable to say who that was. If a client branch contains a change nobody admits
to, the ledger cannot resolve it. This is an accepted consequence of the
shared-credential decision, not an oversight, and it compounds with the runtime
identity: Codex-side activity attributes to the maintainer's login as well.

The gateway is built so this is fixable without a migration. `chats.user_id` and
`jobs.user_id` already exist, and `compliance_agent_pipe.py` forwards the real
Open WebUI user id on every request even when the browser login is shared. The
day per-user accounts are created, attribution starts working with no schema
change and no pipe change.

**Cleartext credentials on the LAN.** Over plain HTTP, both the Open WebUI login
and the Basic Auth credential cross the network unencrypted on every request.
Anyone able to observe LAN traffic — a switch mirror port, a compromised host,
someone on the wifi — reads them directly. Basic Auth is base64, which is
encoding, not encryption.

Treat TLS as **required**, not optional, for anything beyond a same-host test.
An internal CA or a self-signed certificate is sufficient, since there is no
public DNS; the point is confidentiality on the wire, not public trust. Until
TLS is in place, the honest description of this setup is "a speed bump", not
"authenticated".

**What that password actually protects.** Given the runtime identity (see
below), the shared login is not merely a gate on a chat app. Anyone who obtains
it reaches an agent running as `yeli`, which holds passwordless root on this
host. Treat the password with the seriousness of a root credential, because
functionally that is what it is.

## Before exposing on the LAN

Exposure has **not been approved**, and nothing in this repository applies any
of it. This is the minimum that must be true first.

- [ ] **TLS in place.** Cert and key are generated (`secrets/compliance-agent.*`)
      but not installed, and nginx has no server block. Until then both
      credentials cross the LAN in cleartext on every request.
- [ ] **Certificate trusted or accepted.** Self-signed, so either import it as
      a trusted root on each client machine or reissue from an internal CA.
      Clicking through a browser warning every time trains people to ignore
      exactly the warning that would flag an interception.
- [ ] **Password rotated** off its initial value if it has ever been pasted into
      a chat, ticket, email, or a terminal that logs. Rotate in both
      `secrets/.htpasswd` and `COMPLIANCE_CLIENT_PASSWORD` in `.env`.
- [ ] **ufw scoped** to specific LAN hosts on port 8443, not the whole
      `10.48.50.0/24`.
- [ ] **Open WebUI verified** from a second machine: signup page gone, wrong
      password rejected, admin account separate from the shared one.
- [ ] **`nginx -t` passes** and the four existing paths on port 80 still serve.
- [ ] **Containment confirmed live.** `GET /health` reports
      `"containment": "bubblewrap"`, and `gateway/.venv/bin/python -m pytest
      gateway/tests/test_jail.py` passes on the machine actually serving.
- [ ] **The residual privilege question read and accepted.** See below.

## Explicitly not done, and why

| Item | Status | Reason |
|---|---|---|
| nginx server block | **written above, not applied** | /etc/nginx untouched, nginx never reloaded, port 8443 never opened. |
| Basic Auth file | **created, not installed** | `secrets/.htpasswd` exists, mode 600. Copying it into /etc/nginx needs root. |
| ufw rules | not added | Needs root; would affect existing services on port 80. |
| TLS certificate | **generated, not installed** | `secrets/compliance-agent.{crt,key}` exist, mode 600. Installing them into /etc/ssl needs root. |
| Open WebUI install | not installed | Explicitly deferred. |
| Any user account | not created | Explicitly deferred. |
| Tailscale Serve/Funnel | not configured | Out of scope; Funnel would be public exposure. |

## The runtime identity — what containment does and does not fix

The gateway runs as `yeli`, permanently. `yeli` holds NOPASSWD root via
`/etc/sudoers.d/yeli-codex` for `cp`, `install`, `mkdir`, `chown`, `chmod`,
`rsync`, `systemctl` and `nginx`, and is in the `docker` group.

**What the bubblewrap jail fixes.** Every Codex invocation now runs inside an
unprivileged user namespace. Verified live, with the agent running the probes
on itself and reporting verbatim:

```text
touch: cannot touch '/home/yeli/ESCAPED': Read-only file system
ls: cannot access '/home/yeli/.ssh': No such file or directory
sudo: The "no new privileges" flag is set, which prevents sudo from running as root.
```

So the chain "shared password → chat message → root" is **broken at the last
link**. Someone who obtains the password reaches an agent that cannot write
outside its workspace, cannot see the rest of the filesystem, and cannot
escalate.

**What it does not fix.** The sudoers grant and the docker group are unchanged.
They apply to anything running as `yeli` *outside* the jail — the gateway
process itself, a maintainer shell, or any future code path that forgets to
wrap. The jail denies the agent the use of that privilege; it does not remove
the privilege.

So the honest summary for anyone applying this document: a password leak is now
a serious incident bounded by a container, rather than an immediate host
compromise. That is a materially different risk, and worth the difference — but
"contained" is not "harmless", and the containment is only as good as the
guarantee that every invocation is wrapped. `jail.required: true` and
`tests/test_jail.py` exist to keep that guarantee honest.
