"""Publish only the configured static gambling-dashboard outputs."""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path

from .config import PreviewConfig
from .jail import JailSpec, build_bwrap_argv

OUTPUTS = (
    "gambling-dashboard.html",
    "connexontario-gambling-dashboard.html",
    "osduhs-gambling-dashboard.html",
    "hospital-gambling-dashboard.html",
)


def publish(worktree: Path, config: PreviewConfig) -> str:
    """Copy allowlisted rendered files to the fixed non-production target."""
    if not config.enabled:
        raise RuntimeError("Preview publishing is disabled by server configuration.")

    missing = [name for name in OUTPUTS if not (worktree / name).is_file()]
    if missing:
        raise RuntimeError(
            "Preview requires rendered dashboard output missing from the clean "
            f"worktree: {', '.join(missing)}. A maintainer must render from "
            "authorized source data; the gateway will not access or copy it."
        )

    config.target_dir.mkdir(parents=True, exist_ok=True)
    for name in OUTPUTS:
        shutil.copy2(worktree / name, config.target_dir / name)
    return config.url


def render_and_publish(
    worktree: Path,
    config: PreviewConfig,
    *,
    data_dir: Path,
    agent_data_dir: Path,
    codex_home: Path,
    extra_ro: tuple[Path, ...],
    setenv: tuple[tuple[str, str], ...],
) -> str:
    """Render untrusted dashboard source away from the agent and host paths."""
    if not data_dir.is_dir():
        raise RuntimeError("Authorized dashboard data is unavailable for preview rendering.")
    if not agent_data_dir.is_dir():
        raise RuntimeError("Approved aggregate dashboard data is unavailable for preview rendering.")

    with tempfile.TemporaryDirectory(prefix="preview-", dir=worktree.parent) as temp:
        render_dir = Path(temp) / "dashboard"
        shutil.copytree(
            worktree, render_dir,
            ignore=shutil.ignore_patterns(".git", "data", "*_cache", "*_files", "*.html"),
        )
        (render_dir / "data").symlink_to(data_dir)
        (render_dir / "agentData").symlink_to(agent_data_dir)
        jail = JailSpec(
            worktree=render_dir,
            codex_home=codex_home,
            extra_ro=extra_ro + (data_dir, agent_data_dir),
            setenv=setenv,
        )
        for source in (
            "gambling-dashboard.qmd",
            "connexontario-gambling-dashboard.qmd",
            "osduhs-gambling-dashboard.qmd",
            "hospital-gambling-dashboard.qmd",
        ):
            command = build_bwrap_argv(jail) + [
                "--unshare-net",
                "/home/yeli/.local/quarto-1.5.56/bin/quarto",
                "render",
                source,
            ]
            result = subprocess.run(
                command, capture_output=True, text=True, timeout=900, check=False
            )
            if result.returncode != 0:
                raise RuntimeError(
                    f"Preview rendering failed for {source}: "
                    + (result.stderr or result.stdout)[-1000:]
                )
        return publish(render_dir, config)
