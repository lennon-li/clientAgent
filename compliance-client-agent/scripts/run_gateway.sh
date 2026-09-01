#!/usr/bin/env bash
# Start the gateway in the foreground on 127.0.0.1.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export COMPLIANCE_AGENT_HOME="${COMPLIANCE_AGENT_HOME:-$HERE}"
if [[ -f "$HERE/.env" ]]; then set -a; . "$HERE/.env"; set +a; fi
: "${COMPLIANCE_GATEWAY_SECRET:?set COMPLIANCE_GATEWAY_SECRET (see .env.example)}"
cd "$HERE/gateway"
exec .venv/bin/uvicorn app.main:app \
  --host 127.0.0.1 --port "${COMPLIANCE_GATEWAY_PORT:-8642}" \
  --log-level info "$@"
