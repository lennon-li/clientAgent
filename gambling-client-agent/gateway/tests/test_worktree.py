"""Worktree creation, and the raw-data exclusion check.

The exclusion check is defence-in-depth for raw contact data. A freshly created
worktree must contain only tracked source, not files inherited from the
maintainer's working directory.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app import db, gitops


def test_fresh_worktree_contains_no_gitignored_data_directories(tmp_worktree, cfg):
    """Raw data and Quarto caches cannot materialise in a fresh clone."""
    present = gitops.check_excluded_paths(tmp_worktree.path, cfg.project.excluded_paths)
    assert present == (), f"gitignored directories leaked into the worktree: {present}"
    for name in cfg.project.excluded_paths:
        assert not (tmp_worktree.path / name).exists()


def test_contacts_csv_is_absent_from_a_fresh_worktree(tmp_worktree):
    """The contact extract is never available to the client agent."""
    assert not (tmp_worktree.path / "data" / "contacts.csv").exists()


def test_worktree_contains_the_tracked_dashboard_source(tmp_worktree):
    for expected in (
        "AGENTS.md",
        "connexontario-gambling-dashboard.qmd",
        "osduhs-gambling-dashboard.qmd",
        "tools/xlsb_to_csv.py",
    ):
        assert (tmp_worktree.path / expected).exists(), f"missing {expected}"


def test_worktree_is_on_its_own_branch_off_the_default_branch(tmp_worktree, service_repo, cfg):
    head = gitops.head_commit(tmp_worktree.path)
    base = gitops.detect_default_branch(service_repo, cfg.project.default_branch_fallback)
    assert head == gitops.head_commit(service_repo, base)
    assert tmp_worktree.base_commit == head

    current = gitops.run_git(tmp_worktree.path, "rev-parse", "--abbrev-ref", "HEAD")
    assert current.stdout.strip() == tmp_worktree.branch


def test_worktree_starts_clean(tmp_worktree):
    assert not gitops.worktree_is_dirty(tmp_worktree.path)


def test_creating_over_an_existing_directory_is_refused(cfg, service_repo, tmp_path):
    """Never clobber or silently reuse an existing worktree directory."""
    branch = "test/collision"
    (tmp_path / branch.replace("/", "__")).mkdir(parents=True)
    with pytest.raises(gitops.GitError, match="already exists"):
        gitops.create_worktree(
            service_repo, tmp_path, branch, "main", cfg.project.excluded_paths
        )


def test_check_excluded_paths_reports_without_raising(tmp_path):
    """A hit is reported, never a refusal -- it is not an authorization gate."""
    (tmp_path / "backup").mkdir()
    present = gitops.check_excluded_paths(tmp_path, ("backup", "output", "library"))
    assert present == ("backup",)


def test_check_excluded_paths_never_opens_file_contents(tmp_path):
    """Existence only. The check must not read data it is reporting on."""
    secret = tmp_path / "backup"
    secret.mkdir()
    (secret / "data").mkdir()
    payload = secret / "data" / "people.rds"
    payload.write_bytes(b"SENSITIVE")
    before = payload.stat().st_atime_ns
    gitops.check_excluded_paths(tmp_path, ("backup",))
    assert payload.stat().st_atime_ns == before


def test_changed_files_tracks_committed_and_uncommitted_edits(tmp_worktree):
    before = gitops.head_commit(tmp_worktree.path)
    (tmp_worktree.path / "scratch.txt").write_text("x\n")
    assert "scratch.txt" in gitops.changed_files(tmp_worktree.path, before)


def test_branch_name_is_derived_not_taken_from_the_client():
    """Client chat ids are untrusted and never land in a ref name verbatim."""
    evil = "../../../etc/passwd; rm -rf /"
    short = db.short_chat_id(evil)
    assert "/" not in short and ".." not in short and ";" not in short
    assert short == db.short_chat_id(evil)          # stable
    assert short != db.short_chat_id("other-chat")  # collision-resistant
