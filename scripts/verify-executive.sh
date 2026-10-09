#!/usr/bin/env bash
# Execute the actual Banco Aurora source and running Docker integration checks.
# --full additionally performs synthetic money/card/case WRITES and RESETs the fixture.
set -euo pipefail
cd "$(dirname "$0")/.."
if [[ "${1:-}" != "" && "${1:-}" != "--full" ]]; then
  echo 'Usage: bash scripts/verify-executive.sh [--full]' >&2
  exit 2
fi
command -v docker >/dev/null || { echo 'Docker Compose is required.' >&2; exit 1; }
[[ -d tests && -d scenarios && -f compose.yaml ]] || { echo 'Run from the complete project checkout.' >&2; exit 1; }
echo '>>> Real Banking Core and specialist/MCP/Concierge/Portal readiness'
bash scripts/smoke-test.sh
echo '>>> Unit tests isolated in a temporary Core container (SQLite, not live demo PostgreSQL)'
docker compose run --rm --no-deps \
  -e DATABASE_URL=sqlite:// \
  -v "$PWD/tests:/app/tests:ro" \
  -v "$PWD/scenarios:/app/scenarios:ro" \
  -v "$PWD/deployment:/app/deployment:ro" \
  -v "$PWD/compose.yaml:/app/compose.yaml:ro" \
  core python -m pytest -q
if [[ "${1:-}" == '--full' ]]; then
  echo '>>> WARNING: Executing synthetic financial writes and resetting ALL local demo data before and after.'
  echo '>>> Running S01-S06 through the authenticated Banking Portal BFF'
  docker compose exec -T portal python scripts/run-executive-scenarios.py \
    --portal http://127.0.0.1:8300 --report /tmp/executive-scenario-report.json
  echo '>>> Evaluating REAL local deterministic assertions (NOT native WSO2 evaluations)'
  docker compose exec -T portal python scripts/evaluate-scenarios.py /tmp/executive-scenario-report.json
  docker compose cp portal:/tmp/executive-scenario-report.json ./executive-scenario-report.json
  echo '>>> Saved sanitized synthetic report to executive-scenario-report.json (gitignored).'
else
  echo '>>> Read-only checks done. For S01–S06 synthetic WRITE/RESET checks, rerun with --full.'
fi
echo '>>> DONE. S07 and S08 remain MANUAL WSO2 Agent Manager operator scenarios.'
