"""The running gateway must never touch the maintainer's checkout.

All work happens on the service-owned bare clone at state/repo.git, created
once by scripts/bootstrap_repo.sh. `project.source_repo` is bootstrap-only and
is deliberately excluded from Config.runtime_paths().
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from app import gitops


def test_no_runtime_path_resolves_inside_the_source_repo(cfg):
    source = cfg.project.source_repo.resolve()
    for name, path in cfg.runtime_paths().items():
        resolved = Path(path).resolve()
        assert not resolved.is_relative_to(source), (
            f"runtime path {name}={resolved} resolves inside the maintainer's "
            f"checkout {source}"
        )


def test_repo_path_is_the_service_clone_not_the_checkout(cfg):
    assert cfg.project.repo_path.resolve() != cfg.project.source_repo.resolve()
    assert cfg.project.repo_path.name == "repo.git"


def test_runtime_paths_omit_source_repo(cfg):
    assert "source_repo" not in cfg.runtime_paths()


def test_every_runtime_path_lives_under_the_service_tree(cfg):
    """Relocatability: nothing escapes COMPLIANCE_AGENT_HOME."""
    home = cfg.home.resolve()
    for name, path in cfg.runtime_paths().items():
        assert Path(path).resolve().is_relative_to(home), f"{name} escapes {home}"


def test_source_repo_has_no_client_branches(cfg):
    """Client branches exist only in the service clone."""
    if not cfg.project.source_repo.exists():
        pytest.skip("source repo not present")
    proc = subprocess.run(
        ["git", "-C", str(cfg.project.source_repo), "branch", "--list",
         f"{cfg.project.branch_prefix}/*"],
        capture_output=True, text=True, env=gitops.git_env(),
    )
    assert proc.stdout.strip() == "", (
        f"client branches leaked into the maintainer's checkout: {proc.stdout}"
    )


def test_source_repo_has_no_registered_worktrees(cfg):
    if not cfg.project.source_repo.exists():
        pytest.skip("source repo not present")
    wt_dir = cfg.project.source_repo / ".git" / "worktrees"
    assert not wt_dir.exists(), (
        f"worktree metadata was written into the maintainer's checkout: {wt_dir}"
    )


def test_clone_shares_no_object_files_with_the_source(cfg, service_repo):
    """`--no-hardlinks` means the clone cannot corrupt or bloat the original."""
    src_objects = cfg.project.source_repo / ".git" / "objects"
    if not src_objects.exists():
        pytest.skip("source repo not present")

    def inodes(root: Path) -> set[int]:
        return {
            p.stat().st_ino
            for p in root.rglob("*")
            if p.is_file() and "pack" not in p.name
        }

    shared = inodes(src_objects) & inodes(service_repo / "objects")
    assert not shared, f"{len(shared)} object files are hardlinked to the source"


def test_service_clone_cannot_push_to_the_source(service_repo):
    proc = subprocess.run(
        ["git", "-C", str(service_repo), "config", "--get", "remote.origin.pushurl"],
        capture_output=True, text=True, env=gitops.git_env(),
    )
    assert proc.stdout.strip() == "no-push-configured"


def test_default_branch_is_detected_not_hardcoded(service_repo, cfg):
    branch = gitops.detect_default_branch(service_repo, "SHOULD-NOT-BE-USED")
    assert branch == "main"
    assert branch != cfg.project.default_branch_fallback or branch == "main"


def test_detect_default_branch_falls_back_when_undetectable(tmp_path):
    empty = tmp_path / "not-a-repo"
    empty.mkdir()
    assert gitops.detect_default_branch(empty, "fallback-branch") == "fallback-branch"
