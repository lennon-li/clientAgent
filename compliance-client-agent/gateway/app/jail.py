"""Bubblewrap containment for the Codex worker.

WHY THIS EXISTS
---------------
The gateway runs as `yeli`, and `yeli` holds NOPASSWD root via
/etc/sudoers.d/yeli-codex plus `docker` group membership. Without containment,
"the agent cannot make changes outside the project folder" is a policy the
gateway keeps, not a property the system enforces.

Bubblewrap makes it a property. Every Codex invocation runs inside an
unprivileged user namespace where the filesystem is reconstructed from an empty
root and only explicitly bound paths exist.

The single most important effect is not a bind at all: bwrap sets
PR_SET_NO_NEW_PRIVS on the sandboxed process. That flag is inherited by every
descendant and cannot be cleared, and it makes setuid bits inert -- so `sudo`
fails inside the jail even though the sudoers grant is real and unchanged. The
NOPASSWD grant still exists; the jailed process simply cannot use it.

LAYERED DESIGN -- read this before changing anything
----------------------------------------------------
There are two sandboxes, doing different jobs, and neither replaces the other:

  OUTER (bubblewrap)   filesystem containment + privilege containment.
                       Deliberately does NOT unshare the network.
  INNER (codex)        `workspace-write` + `network_access = false`, which
                       denies network to the shell commands the AGENT spawns.

The outer jail keeps the network because Codex itself must reach the API --
`--unshare-net` breaks it outright. So the codex process has network egress,
while the commands it runs on the agent's behalf do not. That split is
intentional. See the README, "What the jail does not contain".
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

BWRAP = "/usr/bin/bwrap"

#: Read-only system paths. Absent ones are skipped rather than erroring, so the
#: same code works if the host layout differs.
SYSTEM_RO = (
    "/usr", "/bin", "/sbin", "/lib", "/lib64", "/lib32", "/etc",
)

#: Paths that must NEVER be bound, at any privilege, for any reason.
#: Asserted by tests/test_jail.py.
NEVER_BIND = (
    "/var/run/docker.sock",
    "/run/docker.sock",
    "/srv",
    "/etc/sudoers.d",
    "/home/yeli/.ssh",
    "/home/yeli/shiny",
    "/home/yeli/repos",
    "/home/yeli/obsidian",
)


def bwrap_available() -> bool:
    return os.access(BWRAP, os.X_OK)


@dataclass(frozen=True)
class JailSpec:
    """Everything the jail is allowed to see."""

    worktree: Path
    codex_home: Path
    #: Deprecated. Each chat now gets a self-contained clone, so the service
    #: clone is never written during a job and is not bound into the jail.
    repo_git: Path | None = None
    auth_src: Path | None = None
    extra_ro: tuple[Path, ...] = field(default_factory=tuple)
    #: Environment set INSIDE the jail, for every process in it. Needed for the
    #: R toolchain: HOME is a throwaway tmpfs, so R would otherwise not find the
    #: user library and would silently fall back to older system packages.
    setenv: tuple[tuple[str, str], ...] = field(default_factory=tuple)

    def writable(self) -> tuple[Path, ...]:
        """The only paths the jailed process may write.

        The workspace is a self-contained clone, so its `.git` is inside it and
        the service clone never needs to be writable -- it is not bound at all.
        codex_home must be writable because Codex persists session/thread state
        there, which is what makes `resume` work across turns.
        """
        return (self.worktree, self.codex_home)


def build_bwrap_argv(spec: JailSpec) -> list[str]:
    """Construct the bubblewrap argv wrapping a Codex invocation.

    Paths are bound at their REAL absolute locations rather than remapped. That
    is load-bearing: a linked git worktree's `.git` file records an absolute
    gitdir, and Codex is passed an absolute --cd. Remapping would break both.
    Binding at the same path exposes only the bound subtree -- the intermediate
    directories are fresh tmpfs, so /home/yeli itself stays empty.
    """
    argv: list[str] = [
        BWRAP,
        # Namespaces. Note: NOT --unshare-net; see the module docstring.
        "--unshare-user",
        "--unshare-ipc",
        "--unshare-pid",
        "--unshare-uts",
        "--unshare-cgroup",
        # Die with the gateway rather than leaking a detached jail.
        "--die-with-parent",
        # Own session: no controlling terminal to escape to.
        "--new-session",
        # Empty root; everything below is opt-in.
        "--tmpfs", "/",
        "--proc", "/proc",
        "--dev", "/dev",
        "--tmpfs", "/tmp",
        "--tmpfs", "/run",
        "--tmpfs", "/var",
        "--hostname", "compliance-jail",
    ]

    # Codex needs DNS. On this host /etc/resolv.conf is a symlink into /run,
    # which is a fresh tmpfs inside the jail, so the link would dangle and
    # every API call would fail.
    #
    # /etc is bound read-only, so we cannot lay a file over /etc/resolv.conf
    # directly -- bwrap would have to create the target inside a read-only
    # mount. Instead bind the real file at the SYMLINK'S TARGET path, which
    # lives under the writable /run tmpfs, so the existing symlink resolves.
    resolv_binds: list[tuple[str, str]] = []
    resolv = Path("/etc/resolv.conf")
    try:
        real_resolv = resolv.resolve(strict=True)
        if resolv.is_symlink():
            target = os.path.realpath(resolv)
            resolv_binds.append((str(real_resolv), target))
        else:
            resolv_binds.append((str(real_resolv), "/etc/resolv.conf"))
    except OSError:
        pass

    for path in SYSTEM_RO:
        p = Path(path)
        if p.is_symlink():
            argv += ["--symlink", os.readlink(path), path]
        elif p.exists():
            argv += ["--ro-bind", path, path]

    # Read-only extras: the codex binary itself (it lives under ~/.local/bin,
    # which is otherwise invisible in here) and the R toolchain, which lives
    # partly under the user's home. A green test suite that no longer runs R
    # would be worse than no jail at all.
    for extra in spec.extra_ro:
        if Path(extra).exists():
            argv += ["--ro-bind", str(extra), str(extra)]

    # Writable working set.
    for path in spec.writable():
        argv += ["--bind", str(path), str(path)]

    # The Codex credential, read-only, mounted ON TOP of the writable
    # CODEX_HOME so the agent cannot overwrite or delete it.
    if spec.auth_src and Path(spec.auth_src).is_file():
        argv += ["--ro-bind", str(spec.auth_src), str(spec.codex_home / "auth.json")]

    for src, dest in resolv_binds:
        argv += ["--ro-bind", src, dest]

    # HOME is a throwaway tmpfs inside the jail. It is NOT /home/yeli: nothing
    # of the maintainer's home is reachable except the explicit binds above.
    argv += ["--tmpfs", "/jail-home", "--setenv", "HOME", "/jail-home"]
    for key, value in spec.setenv:
        argv += ["--setenv", key, value]

    # Seal the scaffolding. Without this, the intermediate directories bwrap
    # creates on the root tmpfs (/home, /home/yeli, ...) are writable, so an
    # agent could `touch /home/yeli/EVIL`. The write would land in an ephemeral
    # tmpfs rather than the real home -- but "writes outside the project folder
    # appear to succeed" is not a guarantee worth shipping. Remounting the root
    # tmpfs read-only leaves the explicit --bind mounts writable, because those
    # are separate mounts.
    argv += ["--remount-ro", "/"]
    argv += ["--chdir", str(spec.worktree)]

    assert_jail_safe(argv)
    return argv


def assert_jail_safe(argv: list[str]) -> None:
    """Fail closed on a jail that would defeat its own purpose."""
    for required in ("--unshare-user", "--unshare-pid", "--die-with-parent",
                     "--new-session"):
        if required not in argv:
            raise ValueError(f"jail argv lacks {required}")

    joined = " ".join(argv)
    for banned in NEVER_BIND:
        # Match as a bound path argument, not as a substring of a longer path.
        if banned in argv:
            raise ValueError(f"jail argv binds forbidden path {banned!r}")
    if "--share-net" in argv:
        raise ValueError("jail argv must not explicitly re-share the network")
    if "--cap-add" in joined:
        raise ValueError("jail argv must not add capabilities")
