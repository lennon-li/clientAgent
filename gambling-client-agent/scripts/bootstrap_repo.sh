#!/usr/bin/env bash
# Create the service-owned bare clone of the gambling repository.
#
# This is the ONLY step that reads the maintainer's checkout. Run it once, by
# hand, as the account that will own the service. After it completes, the
# running gateway never opens the source repo again.
#
#   ./scripts/bootstrap_repo.sh [source_repo] [dest]
#
# Defaults come from gateway/config.yaml (project.source_repo, project.repo_path).
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export GAMBLING_AGENT_HOME="${GAMBLING_AGENT_HOME:-$HERE}"

exec "$HERE/gateway/.venv/bin/python" -c '
import sys
from app.config import load_config
from app.gitops import bootstrap_clone, detect_default_branch, head_commit

cfg = load_config()
src = sys.argv[1] if len(sys.argv) > 1 else str(cfg.project.source_repo)
dst = sys.argv[2] if len(sys.argv) > 2 else str(cfg.project.repo_path)
print(f"source (read once): {src}")
print(f"service clone     : {dst}")
path = bootstrap_clone(src, dst)
branch = detect_default_branch(path, cfg.project.default_branch_fallback)
print(f"default branch    : {branch}")
print(f"head              : {head_commit(path, branch)}")
' "$@"
