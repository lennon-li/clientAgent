"""Git operations against the SERVICE-OWNED clone.

REPO ISOLATION
--------------
Every function here operates on `state/repo.git`, a bare clone created once by
scripts/bootstrap_repo.sh with:

    git clone --no-hardlinks --bare <source_repo> state/repo.git

`--no-hardlinks` means the clone shares no object files with the maintainer's
checkout, so nothing the client agent does can corrupt or grow it. `git
worktree add` writes to `state/repo.git/worktrees/`, inside the service tree.

After bootstrap, the running gateway never opens the maintainer's checkout.
Client branches (`client/compliance/*`) exist only in the service clone and are
invisible to the maintainer until they deliberately fetch them.

No absolute path is hardcoded in this module.
"""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

_MIN_PATH = "/usr/local/bin:/usr/bin:/bin"


def git_env() -> dict[str, str]:
    """Minimal environment for git children. Never inherits the caller's env."""
    return {
        "PATH": os.environ.get("COMPLIANCE_GIT_PATH", _MIN_PATH),
        "HOME": os.environ.get("HOME", os.path.expanduser("~")),
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_ASKPASS": "/bin/false",
        "GIT_CONFIG_NOSYSTEM": "1",
        "LC_ALL": "C",
    }


class GitError(RuntimeError):
    pass


def run_git(
    repo: str | Path, *args: str, check: bool = True, timeout: int = 120
) -> subprocess.CompletedProcess[str]:
    proc = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        env=git_env(),
        timeout=timeout,
        stdin=subprocess.DEVNULL,
    )
    if check and proc.returncode != 0:
        raise GitError(
            f"git {' '.join(args)} failed ({proc.returncode}): {proc.stderr.strip()}"
        )
    return proc


# --------------------------------------------------------------------------
# bootstrap (run once, by a maintainer, not by the running gateway)
# --------------------------------------------------------------------------

def bootstrap_clone(source_repo: str | Path, repo_path: str | Path) -> Path:
    """Create the service-owned bare clone. Idempotent: no-op if it exists.

    This is the ONLY function in the codebase that reads `source_repo`.
    """
    repo_path = Path(repo_path)
    if (repo_path / "HEAD").exists():
        return repo_path
    repo_path.parent.mkdir(parents=True, exist_ok=True)
    proc = subprocess.run(
        [
            "git", "clone", "--no-hardlinks", "--bare",
            str(source_repo), str(repo_path),
        ],
        capture_output=True, text=True, env=git_env(), timeout=600,
        stdin=subprocess.DEVNULL,
    )
    if proc.returncode != 0:
        raise GitError(f"clone failed: {proc.stderr.strip()}")
    # Block accidental pushes back to the maintainer's checkout. A maintainer
    # refresh is `git -C state/repo.git fetch origin '+refs/heads/*:refs/heads/*'`,
    # which is a fetch and still works.
    run_git(repo_path, "config", "remote.origin.pushurl", "no-push-configured")
    return repo_path


# --------------------------------------------------------------------------
# runtime
# --------------------------------------------------------------------------

def detect_default_branch(repo: str | Path, fallback: str = "main") -> str:
    """Detect the default branch of the service clone. Never hardcoded.

    Order: the clone's own HEAD symref (authoritative for a bare clone, and it
    was inherited from upstream at clone time) -> a recorded origin/HEAD ->
    configured fallback. Deliberately does NOT run `ls-remote`, which would
    reach back to the maintainer's checkout at runtime.
    """
    proc = run_git(repo, "symbolic-ref", "--quiet", "HEAD", check=False)
    ref = proc.stdout.strip()
    if proc.returncode == 0 and ref.startswith("refs/heads/"):
        return ref.split("/", 2)[2]

    proc = run_git(
        repo, "symbolic-ref", "--quiet", "refs/remotes/origin/HEAD", check=False
    )
    if proc.returncode == 0 and proc.stdout.strip():
        return proc.stdout.strip().rsplit("/", 1)[-1]

    return fallback


def head_commit(repo: str | Path, ref: str = "HEAD") -> str:
    return run_git(repo, "rev-parse", ref).stdout.strip()


def branch_exists(repo: str | Path, branch: str) -> bool:
    proc = run_git(
        repo, "show-ref", "--verify", "--quiet", f"refs/heads/{branch}", check=False
    )
    return proc.returncode == 0


@dataclass(frozen=True)
class Worktree:
    path: Path
    branch: str
    base_commit: str
    excluded_present: tuple[str, ...] = ()


def check_excluded_paths(
    worktree_path: str | Path, excluded: tuple[str, ...]
) -> tuple[str, ...]:
    """Report which gitignored data/build directories are present.

    Defence-in-depth, not an access-control gate: the client is internal and
    authorized for employee compliance data, so a hit here is reported and
    recorded, never a refusal. `git worktree add` materialises only tracked
    content, so the expected result is an empty tuple; test_worktree.py asserts
    that. Contents are never opened -- existence only.
    """
    wt = Path(worktree_path)
    return tuple(name for name in excluded if (wt / name).exists())


def create_workspace(
    repo: str | Path,
    worktrees_dir: str | Path,
    branch: str,
    base_branch: str,
    excluded: tuple[str, ...] = (),
) -> Worktree:
    """Create a per-chat workspace: a self-contained clone on a new branch.

    WHY A CLONE, NOT `git worktree add`
    -----------------------------------
    A linked worktree keeps its index, refs and objects in the parent repo
    (state/repo.git/worktrees/<name> plus the shared object store), which is
    OUTSIDE the worktree directory. Codex's inner `workspace-write` sandbox
    only grants write access to the workspace itself, and in 0.147.0 neither
    `--add-dir` nor `sandbox_workspace_write.writable_roots` widened it -- both
    were tried, and `git commit` failed with:

        fatal: Unable to create '.../worktrees/<name>/index.lock':
        Read-only file system

    A clone puts `.git` inside the workspace, so every write git needs is
    inside the one directory the agent is allowed to write. No sandbox
    widening is required, which means the inner sandbox stays at its
    strictest setting.

    It is also better isolation on its own terms: a runaway job cannot corrupt
    the shared service clone, and each chat's history is independent. The cost
    is ~11 MB of disk per chat, which is not worth optimising.
    """
    worktrees_dir = Path(worktrees_dir)
    worktrees_dir.mkdir(parents=True, exist_ok=True)
    target = worktrees_dir / branch.replace("/", "__")

    if target.exists():
        raise GitError(f"Workspace path already exists: {target}")

    base_commit = head_commit(repo, base_branch)

    proc = subprocess.run(
        ["git", "clone", "--no-hardlinks", "--branch", base_branch,
         str(repo), str(target)],
        capture_output=True, text=True, env=git_env(), timeout=600,
        stdin=subprocess.DEVNULL,
    )
    if proc.returncode != 0:
        raise GitError(f"workspace clone failed: {proc.stderr.strip()}")

    run_git(target, "checkout", "-b", branch)
    # The chat's clone must not be able to publish anywhere.
    run_git(target, "config", "remote.origin.pushurl", "no-push-configured")

    return Worktree(
        path=target,
        branch=branch,
        base_commit=base_commit,
        excluded_present=check_excluded_paths(target, excluded),
    )


#: Retained name for the previous linked-worktree implementation.
create_worktree = create_workspace


def worktree_is_dirty(worktree_path: str | Path) -> bool:
    proc = run_git(worktree_path, "status", "--porcelain", check=False)
    return bool(proc.stdout.strip())


def changed_files(worktree_path: str | Path, commit_before: str) -> list[str]:
    """Files touched since commit_before, committed or not."""
    files: set[str] = set()
    proc = run_git(
        worktree_path, "diff", "--name-only", f"{commit_before}..HEAD", check=False
    )
    files.update(f for f in proc.stdout.split("\n") if f.strip())
    proc = run_git(worktree_path, "status", "--porcelain", check=False)
    for line in proc.stdout.splitlines():
        if len(line) > 3:
            files.add(line[3:].strip())
    return sorted(files)


def repo_status_clean(
    repo: str | Path, allow: tuple[str, ...] = ()
) -> tuple[bool, list[str]]:
    """Return (clean_apart_from_allowed, all_porcelain_entries)."""
    proc = run_git(repo, "status", "--porcelain", check=False)
    entries = [line for line in proc.stdout.splitlines() if line.strip()]
    offending = [e for e in entries if e[3:].strip() not in allow]
    return (not offending, entries)


def commit_all(
    worktree: str | Path, message: str, author: str = "compliance-agent <agent@localhost>"
) -> str | None:
    """Stage everything in the workspace and make one local commit.

    WHY THE GATEWAY COMMITS, NOT THE AGENT
    --------------------------------------
    Codex's inner sandbox denies writes to `.git` even when it sits inside the
    writable workspace, and neither `--add-dir` nor
    `sandbox_workspace_write.writable_roots` lifted that in 0.147.0. Both were
    tried against a live run; `git commit` failed with:

        fatal: Unable to create '.../index.lock': Read-only file system

    Rather than weaken the sandbox to let the agent write git metadata, the
    gateway commits on its behalf, outside the jail.

    This is the better arrangement anyway. Commit authorship and message format
    are now server-controlled and consistent, and the agent cannot rewrite
    history, amend, or craft misleading commits -- it can only change files.

    Returns the new commit hash, or None if there was nothing to commit.
    """
    worktree = Path(worktree)
    if not worktree_is_dirty(worktree):
        return None

    run_git(worktree, "add", "-A")
    proc = run_git(
        worktree,
        "-c", f"user.name={author.split(' <')[0]}",
        "-c", f"user.email={author.split('<')[1].rstrip('>')}",
        "commit", "-m", message,
        check=False,
    )
    if proc.returncode != 0:
        raise GitError(f"gateway commit failed: {proc.stderr.strip() or proc.stdout.strip()}")
    return head_commit(worktree)
