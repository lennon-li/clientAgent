"""Containment tests — executed against a REAL bubblewrap jail.

These are the tests that make "the agent cannot change anything outside the
project folder" a property rather than a promise. Each one runs a command
inside an actual jail built by app.jail and asserts it fails.

They are deliberately not mocked. A mocked containment test proves nothing:
the whole question is whether the kernel actually stops this.
"""

from __future__ import annotations

import pathlib

import os
import subprocess
from pathlib import Path

import pytest

from app.jail import (
    NEVER_BIND,
    JailSpec,
    assert_jail_safe,
    build_bwrap_argv,
    bwrap_available,
)

pytestmark = pytest.mark.skipif(
    not bwrap_available(), reason="bwrap not available on this host"
)

JAIL_ENV = {
    "PATH": "/usr/local/bin:/usr/bin:/bin",
    "HOME": "/jail-home",
    "TERM": "dumb",
    "LANG": "C.UTF-8",
    "GIT_TERMINAL_PROMPT": "0",
    "GIT_ASKPASS": "/bin/false",
}


@pytest.fixture
def jailed(cfg, tmp_worktree):
    """Run a shell command inside a real jail around a real worktree."""
    spec = JailSpec(
        worktree=tmp_worktree.path,
        codex_home=cfg.codex.codex_home,
        auth_src=Path.home() / ".codex" / "auth.json",
        extra_ro=cfg.jail.extra_ro,
        setenv=cfg.jail.setenv,
    )
    argv = build_bwrap_argv(spec)

    def run(script: str, timeout: int = 120) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            argv + ["/bin/bash", "-c", script],
            env=JAIL_ENV, capture_output=True, text=True, timeout=timeout,
        )

    return run


# =========================================================================
# The eight containment guarantees
# =========================================================================

def test_cannot_write_outside_the_worktree(jailed):
    """The headline requirement: no changes outside the project folder."""
    r = jailed('touch /home/yeli/ESCAPED 2>&1; echo "rc=$?"')
    assert "rc=0" not in r.stdout, f"write outside the worktree succeeded: {r.stdout}"
    assert not Path("/home/yeli/ESCAPED").exists()


def test_cannot_write_to_the_service_tree_outside_bound_paths(jailed):
    r = jailed('touch /home/yeli/services/compliance-client-agent/ESCAPED 2>&1; echo "rc=$?"')
    assert "rc=0" not in r.stdout
    assert not Path("/home/yeli/services/compliance-client-agent/ESCAPED").exists()


def test_ssh_keys_are_not_visible(jailed):
    r = jailed('[ -e /home/yeli/.ssh ] && echo VISIBLE || echo ABSENT')
    assert "ABSENT" in r.stdout


def test_other_repositories_are_not_visible(jailed):
    r = jailed(
        '[ -e /home/yeli/obsidian ] && echo O_VISIBLE || echo O_ABSENT; '
        '[ -e /home/yeli/repos ] && echo R_VISIBLE || echo R_ABSENT'
    )
    assert "O_ABSENT" in r.stdout
    assert "R_ABSENT" in r.stdout


def test_the_maintainer_checkout_and_employee_data_are_not_visible(jailed):
    """The compliance checkout -- and with it backup/data/people.rds."""
    r = jailed(
        '[ -e /home/yeli/shiny/compliance ] && echo CO_VISIBLE || echo CO_ABSENT; '
        '[ -r /home/yeli/shiny/compliance/backup/data/people.rds ] '
        '&& echo PII_READABLE || echo PII_BLOCKED'
    )
    assert "CO_ABSENT" in r.stdout
    assert "PII_BLOCKED" in r.stdout


def test_cannot_write_to_the_deployment_directory(jailed):
    r = jailed(
        'touch /srv/shiny-server/ESCAPED 2>&1; echo "rc=$?"; '
        '[ -e /srv ] && echo SRV_VISIBLE || echo SRV_ABSENT'
    )
    assert "rc=0" not in r.stdout
    assert "SRV_ABSENT" in r.stdout
    assert not Path("/srv/shiny-server/ESCAPED").exists()


def test_sudo_is_defeated_by_no_new_privs(jailed):
    """The sudoers grant is real and unchanged. no_new_privs makes it inert.

    PR_SET_NO_NEW_PRIVS is inherited by every descendant and cannot be cleared,
    so setuid binaries stop conferring privilege inside the jail.
    """
    r = jailed('grep NoNewPrivs /proc/self/status; sudo -n true 2>&1; echo "rc=$?"')
    assert "NoNewPrivs:\t1" in r.stdout
    assert "rc=0" not in r.stdout
    assert "no new privileges" in r.stdout.lower()


def test_docker_socket_is_not_visible(jailed):
    """Docker group membership is root-equivalent -- unless the socket is gone."""
    r = jailed(
        '[ -e /var/run/docker.sock ] && echo V1 || echo A1; '
        '[ -e /run/docker.sock ] && echo V2 || echo A2; '
        'command -v docker >/dev/null && docker ps 2>&1 | head -1 || echo "no docker"'
    )
    assert "A1" in r.stdout and "A2" in r.stdout
    assert "CONTAINER ID" not in r.stdout


# =========================================================================
# The jail must not break the actual job
# =========================================================================

def test_the_worktree_is_writable(jailed, tmp_worktree):
    r = jailed('touch ./jail-write-probe && echo WROTE')
    assert "WROTE" in r.stdout
    assert (tmp_worktree.path / "jail-write-probe").exists()


def test_git_commit_works_inside_the_jail(jailed):
    """The workspace is a clone, so `.git` is inside it and commits just work."""
    r = jailed(
        'echo "jail test" >> JAILTEST.md && '
        'git -c user.email=t@t -c user.name=t add JAILTEST.md && '
        'git -c user.email=t@t -c user.name=t commit -q -m "jail commit test" && '
        'git log --oneline -1'
    )
    assert r.returncode == 0, f"git commit failed in jail: {r.stderr}"
    assert "jail commit test" in r.stdout


def test_network_is_available_to_the_codex_process(jailed):
    """Deliberate: --unshare-net breaks Codex, which must reach the API.

    Command-level network denial is the INNER codex sandbox's job
    (network_access = false), not the jail's. See the README.
    """
    r = jailed('getent hosts api.openai.com >/dev/null && echo DNS_OK || echo DNS_FAIL')
    assert "DNS_OK" in r.stdout


def test_r_toolchain_resolves_the_user_library(jailed):
    """A green suite that no longer runs R would be a silent failure."""
    r = jailed('Rscript -e ".libPaths()" 2>/dev/null', timeout=300)
    assert "x86_64-pc-linux-gnu-library" in r.stdout, (
        f"R user library missing inside the jail: {r.stdout}"
    )


@pytest.mark.slow
def test_devtools_test_passes_inside_the_jail(jailed):
    """The real proof: the project's own suite runs, and passes, contained."""
    r = jailed('Rscript -e "devtools::test()" 2>&1', timeout=900)
    assert "FAIL 0" in r.stdout, f"devtools::test() did not pass in jail:\n{r.stdout[-2000:]}"
    assert "PASS 14" in r.stdout, f"expected 14 passing tests, got:\n{r.stdout[-2000:]}"


# =========================================================================
# Credential handling
# =========================================================================

def test_codex_credential_is_read_only_inside_the_jail(jailed, cfg):
    auth = cfg.codex.codex_home / "auth.json"
    r = jailed(f'echo tampered >> "{auth}"; echo "rc=$?"')
    assert "rc=0" not in r.stdout
    combined = (r.stdout + r.stderr).lower()
    assert "read-only" in combined or "permission denied" in combined


def test_maintainer_credential_and_history_are_not_visible(jailed):
    """The Codex PROGRAM directory is bound; the credential and history are not.

    ~/.codex/packages/.../bin must be visible or Codex cannot spawn its helper
    processes. Everything else under ~/.codex stays out, so the agent sees the
    binaries and never the maintainer's token or session history.
    """
    r = jailed(
        '[ -e /home/yeli/.codex/auth.json ] && echo AUTH_VISIBLE || echo AUTH_ABSENT; '
        '[ -e /home/yeli/.codex/history.jsonl ] && echo HIST_VISIBLE || echo HIST_ABSENT; '
        '[ -e /home/yeli/.codex/sessions ] && echo SESS_VISIBLE || echo SESS_ABSENT'
    )
    assert "AUTH_ABSENT" in r.stdout
    assert "HIST_ABSENT" in r.stdout
    assert "SESS_ABSENT" in r.stdout


# =========================================================================
# argv construction
# =========================================================================

def test_argv_never_binds_a_forbidden_path(cfg, tmp_path):
    spec = JailSpec(
        worktree=tmp_path / "wt", codex_home=tmp_path / "ch",
    )
    argv = build_bwrap_argv(spec)
    for banned in NEVER_BIND:
        assert banned not in argv, f"jail binds {banned}"


def test_argv_carries_every_required_namespace_flag(tmp_path):
    spec = JailSpec(
        worktree=tmp_path / "wt", codex_home=tmp_path / "ch"
    )
    argv = build_bwrap_argv(spec)
    for flag in ("--unshare-user", "--unshare-pid", "--unshare-ipc",
                 "--unshare-uts", "--unshare-cgroup", "--die-with-parent",
                 "--new-session", "--remount-ro"):
        assert flag in argv, f"missing {flag}"


def test_argv_does_not_unshare_the_network(tmp_path):
    """Documented deliberate choice, guarded so nobody 'fixes' it by accident."""
    spec = JailSpec(
        worktree=tmp_path / "wt", codex_home=tmp_path / "ch"
    )
    assert "--unshare-net" not in build_bwrap_argv(spec)


def test_assert_jail_safe_rejects_a_weakened_jail():
    with pytest.raises(ValueError, match="unshare-user"):
        assert_jail_safe(["/usr/bin/bwrap", "--unshare-pid", "--die-with-parent",
                          "--new-session"])
    with pytest.raises(ValueError, match="forbidden path"):
        assert_jail_safe(["/usr/bin/bwrap", "--unshare-user", "--unshare-pid",
                          "--die-with-parent", "--new-session",
                          "--bind", "/srv", "/srv"])


def test_codex_argv_is_wrapped_by_the_jail(cfg, tmp_path):
    """Integration: the composed argv is bwrap first, codex second."""
    from app import codex_runner as cr

    spec = JailSpec(
        worktree=tmp_path / "wt", codex_home=tmp_path / "ch"
    )
    composed = build_bwrap_argv(spec) + cr.build_argv(
        binary="codex", worktree=str(tmp_path / "wt")
    )
    assert composed[0].endswith("bwrap")
    assert "exec" in composed
    assert composed.index("--unshare-user") < composed.index("--sandbox")
    cr.assert_argv_safe(composed)


def test_empty_auth_mountpoint_is_not_mistaken_for_a_credential(tmp_path):
    """Regression: bwrap leaves a zero-byte auth.json where it binds the real one.

    Caught live -- /health started reporting `codex_auth: service-owned` after
    the first jailed run, because an empty mountpoint file had appeared in the
    service CODEX_HOME. An empty file is not a credential.
    """
    from app.codex_runner import resolve_codex_home

    service = tmp_path / "codex_home"
    service.mkdir()
    (service / "auth.json").touch()          # zero bytes, as bwrap leaves it

    maintainer = tmp_path / "maintainer"
    maintainer.mkdir()
    (maintainer / "auth.json").write_text('{"token": "x"}')

    resolved = resolve_codex_home(service, maintainer)
    assert resolved.is_maintainer_login is True
    assert resolved.path == maintainer


def test_project_memory_is_readable_but_not_writable(jailed, cfg):
    """The bootstrap tells the agent to read MEMORY_INDEX.md first.

    Regression guard: memory used to live under /home/yeli/obsidian, which is in
    NEVER_BIND, so the agent could never read the file it was told to read.
    """
    index = cfg.project.memory_index
    r = jailed(
        f'[ -r {index} ] && echo READABLE || echo UNREADABLE; '
        f'touch $(dirname {index})/wr 2>/dev/null && echo WRITABLE || echo READONLY'
    )
    assert "READABLE" in r.stdout
    assert "READONLY" in r.stdout


def test_memory_is_not_sourced_from_the_synced_vault(cfg):
    """Compliance has no remote project memory; it must not point at obsidian."""
    assert "/obsidian/" not in cfg.project.memory_index
    # Must live inside the service tree, alongside the other relocatable state.
    tree_root = cfg.codex.codex_home.parent.parent
    assert pathlib.Path(cfg.project.memory_index).is_relative_to(tree_root)
