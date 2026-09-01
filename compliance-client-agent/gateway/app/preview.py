"""Publish only the allowlisted compliance Shiny preview files."""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

from .config import PreviewConfig


FILES = (
    (Path("inst/app/app.R"), Path("app.R")),
    (Path("R/account2id.R"), Path("R/account2id.R")),
)


def publish(worktree: Path, config: PreviewConfig) -> str:
    """Copy the committed chat workspace to the fixed non-production target."""
    if not config.enabled:
        raise RuntimeError("Preview publishing is disabled by server configuration.")

    missing = [str(source) for source, _ in FILES if not (worktree / source).is_file()]
    if missing:
        raise RuntimeError(
            "Preview requires missing source files: " + ", ".join(missing)
        )

    config.target_dir.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix="compliance-preview-", dir=config.target_dir.parent
    ) as temp:
        staging = Path(temp)
        for source, destination in FILES:
            staged = staging / destination
            staged.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(worktree / source, staged)

        config.target_dir.mkdir(parents=True, exist_ok=True)
        for _, destination in FILES:
            target = config.target_dir / destination
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(staging / destination, target)

    return config.url
