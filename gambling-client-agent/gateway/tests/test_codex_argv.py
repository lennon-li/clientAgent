"""Phase 2 guarantee: the constructed argv can never loosen the sandbox."""

from __future__ import annotations

import pytest

from app import codex_runner as cr


def test_argv_always_carries_workspace_write():
    argv = cr.build_argv(binary="codex", worktree="/wt")
    assert "--sandbox" in argv
    assert argv[argv.index("--sandbox") + 1] == "workspace-write"


def test_argv_always_ignores_user_config():
    """~/.codex/config.toml sets danger-full-access globally. Never load it."""
    argv = cr.build_argv(binary="codex", worktree="/wt")
    assert "--ignore-user-config" in argv


def test_argv_always_pins_working_directory():
    argv = cr.build_argv(binary="codex", worktree="/some/worktree")
    assert argv[argv.index("--cd") + 1] == "/some/worktree"


@pytest.mark.parametrize("thread_id", [None, "01a014f6-6279-7ee0-ac1f-e0e10a6f647a"])
def test_argv_never_contains_banned_flags(thread_id):
    argv = cr.build_argv(binary="codex", worktree="/wt", model="m", thread_id=thread_id)
    joined = " ".join(argv)
    for banned in cr.BANNED_ARGS:
        assert banned not in joined, f"{banned} leaked into argv"
    assert "danger-full-access" not in joined
    assert "--yolo" not in joined


def test_resume_flags_precede_subcommand():
    """clap rejects global options after `resume`; regression guard."""
    argv = cr.build_argv(binary="codex", worktree="/wt", thread_id="tid")
    assert argv.index("--sandbox") < argv.index("resume")
    assert argv.index("--cd") < argv.index("resume")
    assert argv[-2:] == ["resume", "tid"]


def test_non_workspace_write_sandbox_is_refused():
    with pytest.raises(ValueError):
        cr.build_argv(binary="codex", worktree="/wt", sandbox="danger-full-access")
    with pytest.raises(ValueError):
        cr.build_argv(binary="codex", worktree="/wt", sandbox="read-only")


def test_assert_argv_safe_catches_a_tampered_argv():
    bad = ["codex", "exec", "--ignore-user-config", "--sandbox",
           "danger-full-access", "--cd", "/wt"]
    with pytest.raises(ValueError):
        cr.assert_argv_safe(bad)

    missing_cd = ["codex", "exec", "--ignore-user-config", "--sandbox",
                  "workspace-write"]
    with pytest.raises(ValueError):
        cr.assert_argv_safe(missing_cd)


def test_hardening_disables_network_for_agent_commands():
    argv = cr.build_argv(binary="codex", worktree="/wt")
    assert "sandbox_workspace_write.network_access=false" in " ".join(argv)


def test_tmp_is_not_excluded_from_the_inner_sandbox():
    """Deliberate: /tmp is a private tmpfs inside the jail.

    Excluding it broke R -- devtools::test() failed with "creating temporary
    file for '-e' failed". Containment is the outer jail's job.
    """
    joined = " ".join(cr.build_argv(binary="codex", worktree="/wt"))
    assert "exclude_slash_tmp" not in joined


def test_add_dir_is_emitted_for_the_git_object_store():
    """A linked worktree commits into the bare clone, outside the worktree."""
    argv = cr.build_argv(binary="codex", worktree="/wt", writable_extra=("/rg",))
    assert argv[argv.index("--add-dir") + 1] == "/rg"


def test_add_dir_is_absent_when_nothing_extra_is_requested():
    assert "--add-dir" not in cr.build_argv(binary="codex", worktree="/wt")


def test_child_env_is_built_not_inherited(monkeypatch):
    """The parent holds the gateway secret; it must not reach the child."""
    monkeypatch.setenv("GAMBLING_GATEWAY_SECRET", "super-secret")
    env = cr.build_env("/codex/home", "/usr/bin", home="/srv/x")
    assert "GAMBLING_GATEWAY_SECRET" not in env
    assert env["CODEX_HOME"] == "/codex/home"
    assert env["HOME"] == "/srv/x"


def test_bootstrap_names_the_worktree_and_forbids_publication():
    text = cr.build_bootstrap(
        worktree="/wt", branch="client/gambling/abc", default_branch="main",
        project="gambling", memory_index="/mem/MEMORY_INDEX.md",
        agents_md="AGENTS.md", test_command="Rscript -e 'devtools::test()'",
    )
    assert "/wt" in text
    assert "MEMORY_INDEX.md" in text and "AGENTS.md" in text
    lowered = text.lower()
    for forbidden in ("git push", "sudo", "/srv/shiny-server", "merge", "deploy"):
        assert forbidden in lowered
    assert cr.MAINTAINER_MARKER in text
