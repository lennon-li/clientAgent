from pathlib import Path

import pytest

from app.config import PreviewConfig
from app.preview import FILES, publish


def config(target: Path) -> PreviewConfig:
    return PreviewConfig(
        enabled=True,
        target_dir=target,
        url="https://example.test/compliance/",
    )


def test_preview_copies_only_allowlisted_files(tmp_path):
    worktree = tmp_path / "worktree"
    target = tmp_path / "preview"
    worktree.mkdir()
    for source, _ in FILES:
        path = worktree / source
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(str(source), encoding="utf-8")
    (worktree / "secret.rds").write_text("must not publish", encoding="utf-8")

    assert publish(worktree, config(target)) == "https://example.test/compliance/"
    assert (target / "app.R").read_text(encoding="utf-8") == "inst/app/app.R"
    assert (target / "R/account2id.R").is_file()
    assert not (target / "secret.rds").exists()


def test_preview_refuses_incomplete_workspace(tmp_path):
    worktree = tmp_path / "worktree"
    worktree.mkdir()

    with pytest.raises(RuntimeError, match="missing source files"):
        publish(worktree, config(tmp_path / "preview"))
