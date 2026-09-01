from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

os.environ.setdefault("GAMBLING_GATEWAY_SECRET", "test-secret-not-real")
# API tests must never spawn a real Codex process.
os.environ.setdefault("GAMBLING_WORKER_DISABLED", "1")

from app.config import load_config  # noqa: E402

TREE_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="session")
def cfg():
    return load_config()


@pytest.fixture(scope="session")
def service_repo(cfg):
    """The real service-owned bare clone. Read-only for tests except worktrees."""
    if not (cfg.project.repo_path / "HEAD").exists():
        pytest.skip("service clone not bootstrapped; run scripts/bootstrap_repo.sh")
    return cfg.project.repo_path


@pytest.fixture
def tmp_worktree(cfg, service_repo, tmp_path):
    """Create a throwaway worktree from the service clone, then remove it.

    Removal is safe here in a way it is never safe in the worker: this branch
    was created by the test seconds earlier and holds nothing a human made.
    """
    from app import gitops

    branch = f"test/worktree-{os.getpid()}-{tmp_path.name}"
    base = gitops.detect_default_branch(service_repo, cfg.project.default_branch_fallback)
    wt = gitops.create_workspace(
        service_repo, tmp_path, branch, base, cfg.project.excluded_paths
    )
    yield wt
    # The workspace is a self-contained clone created seconds ago by this test
    # and holds nothing a human made, so removing it is safe here in a way it
    # is never safe in the worker.
    shutil.rmtree(wt.path, ignore_errors=True)


@pytest.fixture
def client(cfg, tmp_path, monkeypatch):
    """A TestClient backed by a scratch database, not the live one."""
    from fastapi.testclient import TestClient

    from app import config as config_mod
    from app.main import create_app

    scratch_db = tmp_path / "gateway.sqlite3"
    test_cfg = config_mod.Config(
        home=cfg.home,
        gateway=config_mod.GatewayConfig(
            host=cfg.gateway.host,
            port=cfg.gateway.port,
            state_dir=tmp_path,
            db_path=scratch_db,
            worktrees_dir=tmp_path / "worktrees",
            log_dir=tmp_path / "logs",
        ),
        project=cfg.project,
        codex=cfg.codex,
        jail=cfg.jail,
        limits=cfg.limits,
        preview=cfg.preview,
        raw=cfg.raw,
    )
    app = create_app(test_cfg)
    with TestClient(app) as c:
        c.headers.update({"Authorization": "Bearer test-secret-not-real"})
        yield c
