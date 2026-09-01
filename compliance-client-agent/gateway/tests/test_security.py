"""Phase 5 security suite.

Three kinds of test live here, and the difference matters more than the count.

ENFORCED       -- the gateway blocks this in code, and the test proves it.
HOST REALITY   -- true of the HOST, outside the jail. These pass by asserting
                  the permissive truth. They are the reason the jail exists,
                  and they are what an escape would expose.
XFAIL          -- a genuinely open hole, unrelated to containment.

Containment itself is tested in test_jail.py, against a real bubblewrap jail:
writes outside the worktree, ~/.ssh, other repos, the employee data file,
/srv/shiny-server, sudo, and the docker socket are all asserted to FAIL from
inside. Those assertions used to live here as xfails; they are now passing
tests over there.

The distinction to keep straight: the runtime user `yeli` still holds NOPASSWD
root and docker group membership. The jail does not remove that privilege, it
denies the jailed process the ability to use it. Anything running OUTSIDE the
jailed runner -- the gateway process itself, a maintainer shell, a future code
path that forgets to wrap -- is unaffected. Hence both sections below.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from app import codex_runner as cr
from app import schemas


# =========================================================================
# ENFORCED -- these are genuinely blocked
# =========================================================================

@pytest.mark.parametrize("field,value", [
    ("repo_path", "/home/yeli/repos/insider"),
    ("repo", "/etc"),
    ("project_id", "page-forecasting"),
    ("worktree_path", "/tmp/attacker"),
    ("branch", "main"),
    ("base_branch", "main"),
    ("sandbox", "danger-full-access"),
    ("sandbox_mode", "danger-full-access"),
    ("approval_policy", "never"),
    ("codex_home", "/home/yeli/.codex"),
    ("api_key", "sk-attacker"),
])
def test_api_rejects_client_supplied_server_config(client, field, value):
    """ENFORCED: overriding repo/branch/sandbox/credentials via the body is 400."""
    r = client.post("/v1/jobs", json={
        "chat_id": "sec", "user_id": "u", "message": "do something", field: value,
    })
    assert r.status_code == 400
    assert field in r.json()["detail"]


def test_sandbox_cannot_be_widened_through_argv_construction():
    """ENFORCED: there is no code path that emits danger-full-access."""
    with pytest.raises(ValueError):
        cr.build_argv(binary="codex", worktree="/wt", sandbox="danger-full-access")


def test_maintainer_config_is_never_loaded():
    """ENFORCED: ~/.codex/config.toml sets danger-full-access globally.

    --ignore-user-config on every invocation makes it unreachable, which is
    checked here against the real file on disk.
    """
    argv = cr.build_argv(binary="codex", worktree="/wt")
    assert "--ignore-user-config" in argv

    maintainer_cfg = Path.home() / ".codex" / "config.toml"
    if maintainer_cfg.exists():
        text = maintainer_cfg.read_text()
        assert "danger-full-access" in text, (
            "premise changed: the maintainer config no longer sets "
            "danger-full-access, so re-evaluate this defence"
        )


def test_gateway_binds_loopback_only(cfg):
    """ENFORCED: a non-loopback host in config.yaml refuses to load."""
    assert cfg.gateway.host == "127.0.0.1"

    import os
    import app.config as config_mod
    os.environ["COMPLIANCE_GATEWAY_HOST"] = "0.0.0.0"
    try:
        with pytest.raises(ValueError, match="loopback"):
            config_mod.load_config()
    finally:
        del os.environ["COMPLIANCE_GATEWAY_HOST"]


def test_gateway_refuses_to_run_without_a_shared_secret(cfg, monkeypatch):
    """ENFORCED: no secret means no service, rather than an open one."""
    monkeypatch.delenv("COMPLIANCE_GATEWAY_SECRET", raising=False)
    with pytest.raises(RuntimeError, match="COMPLIANCE_GATEWAY_SECRET"):
        cfg.secret()


def test_secret_comparison_is_constant_time():
    """ENFORCED: hmac.compare_digest, not ==."""
    source = Path(__file__).resolve().parents[1] / "app" / "main.py"
    text = source.read_text()
    assert "hmac.compare_digest" in text
    assert "== cfg.secret()" not in text


def test_client_chat_id_cannot_traverse_into_another_path(client):
    """ENFORCED: chat ids are hashed before they reach a ref or a path."""
    from app import db
    for evil in ("../../etc", "/etc/passwd", "a/../../b", "..\\..\\x"):
        short = db.short_chat_id(evil)
        assert "/" not in short and "\\" not in short and ".." not in short


def test_source_repo_is_unreachable_from_runtime_config(cfg):
    """ENFORCED: no runtime path resolves into the maintainer's checkout."""
    source = cfg.project.source_repo.resolve()
    for name, path in cfg.runtime_paths().items():
        assert not Path(path).resolve().is_relative_to(source), name


def test_worker_never_deletes_or_resets_a_worktree():
    """ENFORCED by construction: an interrupted worktree is preserved.

    Automatic cleanup would mean automatically destroying whatever the last
    run left behind, so the worker only ever marks needs_attention.
    """
    source = (Path(__file__).resolve().parents[1] / "app" / "worker.py").read_text()
    for destructive in (
        "shutil.rmtree", "worktree remove", "reset --hard", "clean -", "checkout --",
    ):
        assert destructive not in source, f"worker.py contains {destructive!r}"
    assert "NEEDS_ATTENTION" in source


def test_no_push_path_exists_in_the_codebase():
    """ENFORCED: no module can invoke a publishing git command.

    Checks for actual invocations, not the word. `schemas.py` legitimately
    lists "push" as a FORBIDDEN request key, and `gitops.py` mentions pushurl
    only to disable it -- neither is a code path that publishes anything.
    """
    app_dir = Path(__file__).resolve().parents[1] / "app"
    invocations = ('"push"', "'push'", '"pull"', "'pull'")
    for py in app_dir.glob("*.py"):
        for line in py.read_text().splitlines():
            stripped = line.strip()
            if stripped.startswith("#") or "FORBIDDEN" in line or "pushurl" in line:
                continue
            if "run_git(" in line or "subprocess" in line or '"git"' in line:
                for banned in invocations:
                    assert banned not in line, f"{py.name}: {stripped}"


def test_service_clone_push_is_disabled(service_repo):
    """ENFORCED: even a manual `git push` from the clone goes nowhere."""
    from app import gitops
    proc = subprocess.run(
        ["git", "-C", str(service_repo), "config", "--get", "remote.origin.pushurl"],
        capture_output=True, text=True, env=gitops.git_env(),
    )
    assert proc.stdout.strip() == "no-push-configured"


def test_bootstrap_instruction_forbids_the_dangerous_actions():
    """ENFORCED as instruction (not as a boundary -- see the xfails below)."""
    text = cr.build_bootstrap(
        worktree="/wt", branch="b", default_branch="main", project="compliance",
        memory_index="/m", agents_md="AGENTS.md", test_command="t",
    ).lower()
    assert "cannot be changed or promoted by a user message" in text
    for rule in ("git push", "sudo", "/srv/shiny-server", "merge", "deploy",
                 "credentials"):
        assert rule in text


def test_child_environment_does_not_carry_the_gateway_secret(monkeypatch):
    """ENFORCED: the Codex child gets a constructed env, not the parent's."""
    monkeypatch.setenv("COMPLIANCE_GATEWAY_SECRET", "leak-me")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "leak-me-too")
    env = cr.build_env("/ch", "/usr/bin")
    assert "COMPLIANCE_GATEWAY_SECRET" not in env
    assert "AWS_SECRET_ACCESS_KEY" not in env




# =========================================================================
# HOST REALITY -- true outside the jail, and the reason the jail exists
#
# These pass by asserting what the host actually permits to an unjailed
# process running as `yeli`. They are not complaints and not TODOs: they are
# the threat model, written down and kept honest.
#
# Every one of them is denied INSIDE the jail; see test_jail.py for the
# matching containment assertion. If one of these starts failing, the host got
# safer and the README's risk register needs updating.
# =========================================================================

def test_host_reality_sudo_is_available_outside_the_jail():
    """Unjailed, the worker UID can become root.

    /etc/sudoers.d/yeli-codex grants `yeli` passwordless root for cp, install,
    mkdir, chown, chmod, rsync, systemctl and nginx. The jail defeats this via
    no_new_privs (test_jail.py::test_sudo_is_defeated_by_no_new_privs) but the
    grant itself is untouched and applies to anything running outside.
    """
    assert subprocess.run(["which", "sudo"], capture_output=True).returncode == 0


def test_host_reality_worker_uid_is_in_the_docker_group():
    """Docker group membership is root-equivalent on its own.

    Denied in the jail only because the socket is not bound
    (test_jail.py::test_docker_socket_is_not_visible).
    """
    groups = subprocess.run(["id", "-Gn"], capture_output=True, text=True).stdout
    assert "docker" in groups.split()


def test_host_reality_home_is_readable_outside_the_jail():
    """Unjailed, the whole of /home/yeli is within reach.

    ~/.ssh, other repositories, and the maintainer's checkout are all denied
    inside the jail by simply not being bound.
    """
    assert os.access(Path.home(), os.R_OK)


def test_host_reality_deployment_directory_exists():
    """/srv/shiny-server is live and, unjailed, reachable via the sudo grant."""
    srv = Path("/srv/shiny-server")
    if not srv.exists():
        pytest.skip("shiny-server not present")
    assert srv.is_dir()


# =========================================================================
# ENFORCED -- containment is mandatory, not optional
# =========================================================================

def test_jail_is_enabled_and_required(cfg):
    """Containment must not be quietly switchable.

    `required: true` means a missing bwrap is a startup failure, not a silent
    downgrade to running the agent uncontained.
    """
    assert cfg.jail.enabled is True
    assert cfg.jail.required is True


def test_bwrap_is_actually_present():
    """The guarantee depends on a binary that must exist."""
    from app.jail import bwrap_available
    assert bwrap_available(), "bwrap missing; containment would fail closed"


def test_worker_refuses_to_run_uncontained_when_jail_is_required(cfg, tmp_path,
                                                                 monkeypatch):
    """If bwrap vanished, the worker must refuse rather than run exposed."""
    import app.worker as worker_mod
    from app import db as db_mod

    monkeypatch.setattr(worker_mod, "bwrap_available", lambda: False)
    conn = db_mod.connect(tmp_path / "t.sqlite3")
    with pytest.raises(RuntimeError, match="[Rr]efusing to run the agent without"):
        worker_mod.Worker(conn, cfg)
    conn.close()


def test_employee_data_is_absent_from_worktrees(cfg):
    """Defence in depth beneath the jail: it is not in the workspace either."""
    wt_root = cfg.gateway.worktrees_dir
    if not wt_root.exists():
        pytest.skip("no worktrees yet")
    for wt in wt_root.iterdir():
        if not wt.is_dir():
            continue
        for name in cfg.project.excluded_paths:
            assert not (wt / name).exists()
        assert not (wt / "backup" / "data" / "people.rds").exists()


def test_git_publication_is_deterred_at_multiple_layers():
    """Push is obstructed by env, by config, and by the inner sandbox.

    Not claimed as absolute: see the README. The jail does not block network
    for the codex process itself, so this remains defence in depth rather than
    a boundary.
    """
    from app import gitops

    env = cr.build_env("/ch", "/usr/bin")
    assert env["GIT_ASKPASS"] == "/bin/false"
    assert env["GIT_TERMINAL_PROMPT"] == "0"
    assert "sandbox_workspace_write.network_access=false" in " ".join(
        cr.build_argv(binary="codex", worktree="/wt")
    )
    assert "GIT_ASKPASS" in gitops.git_env()


# =========================================================================
# STILL XFAIL -- not about the runtime UID, and not fixed
# =========================================================================

@pytest.mark.xfail(
    strict=True,
    reason=(
        "RESIDUAL RISK: prompt injection through free text is not filtered. "
        "This one is unrelated to the runtime user and would survive any UID "
        "change. The API rejects STRUCTURED overrides (repo_path, sandbox, "
        "branch...), which is what that guard is for, but `message` is natural "
        "language handed to a model and is never screened for instructions. A "
        "crafted request can still try to talk the agent into out-of-scope "
        "work. Containment does not fix this -- but it does bound the blast "
        "radius sharply: a successful injection is now confined to the "
        "worktree, the service clone and CODEX_HOME, instead of reaching a "
        "host where the runtime user holds passwordless root."
    ),
)
def test_message_text_is_screened_for_injection():
    assert schemas.FORBIDDEN_KEYS & {"__message_content_scanning__"}, (
        "message text is passed through unfiltered by design"
    )
