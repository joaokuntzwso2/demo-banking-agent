#!/usr/bin/env bash
# Validate source-level readiness and the live local banking demo.
# --full performs REAL SYNTHETIC banking writes and resets the local demo data.
set -euo pipefail
cd "$(dirname "$0")/.."
if [[ "${1:-}" != "" && "${1:-}" != "--full" ]]; then
  echo 'Usage: bash scripts/verify-v4.sh [--full]' >&2; exit 2
fi
python3 scripts/preflight-v4.py --mode source
python3 deployment/agent-manager/scripts/prepare-apps.py
python3 -m compileall -q agents banking_common mock_core mcp_server demo_portal
if command -v node >/dev/null 2>&1; then node --check demo_portal/static/app.js; fi
bash scripts/verify-executive.sh "${1:-}"
echo 'V4 source and local scenario checks completed. Managed platform checks remain manual.'
