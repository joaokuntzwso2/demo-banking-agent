#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
command -v docker >/dev/null || { echo 'Docker is required' >&2; exit 1; }
docker compose up --build -d
bash scripts/smoke-test.sh
printf '\nBanking UI: http://localhost:8300\n'
printf 'Demo sign-in: DEMO_ACCESS_CODE from .env (or local Compose default)\n'
printf 'For write-capable scenarios: python scripts/run-executive-scenarios.py --report executive-scenario-report.json\n'
printf 'WARNING: scenario script resets fictional data before/after and is not a real-banking test.\n'
