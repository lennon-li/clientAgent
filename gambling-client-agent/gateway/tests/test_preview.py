from pathlib import Path

import pytest

from app.config import PreviewConfig
from app.preview import OUTPUTS, publish


EXPECTED_OUTPUTS = {
    "gambling-dashboard.html",
    "connexontario-gambling-dashboard.html",
    "osduhs-gambling-dashboard.html",
    "hospital-gambling-dashboard.html",
}


def config(target: Path) -> PreviewConfig:
    return PreviewConfig(enabled=True, target_dir=target, url="https://example.test/gambling/")


def test_preview_copies_only_allowlisted_dashboard_outputs(tmp_path):
    assert set(OUTPUTS) == EXPECTED_OUTPUTS
    worktree = tmp_path / "worktree"
    target = tmp_path / "preview"
    worktree.mkdir()
    for name in OUTPUTS:
        (worktree / name).write_text(name, encoding="utf-8")
    (worktree / "data.csv").write_text("must not publish", encoding="utf-8")

    assert publish(worktree, config(target)) == "https://example.test/gambling/"
    assert {path.name for path in target.iterdir()} == set(OUTPUTS)


def test_preview_refuses_to_publish_incomplete_render(tmp_path):
    worktree = tmp_path / "worktree"
    worktree.mkdir()
    (worktree / OUTPUTS[0]).touch()

    with pytest.raises(RuntimeError, match="missing from the clean worktree"):
        publish(worktree, config(tmp_path / "preview"))
