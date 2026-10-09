#!/usr/bin/env bash
# Banco Aurora: read-only smoke test for the local Compose topology.
set -euo pipefail
cd "$(dirname "$0")/.."

check_url() {
  local name="$1" url="$2"
  printf '\n>>> %-18s %s\n' "$name" "$url"
  curl --fail-with-body --show-error --silent --connect-timeout 4 --max-time 20 "$url"
  printf '\n'
}

printf '>>> Waiting for Banking Core readiness (bounded retries)...\n'
core_ready=false
for attempt in $(seq 1 25); do
  if curl --fail --silent --show-error --connect-timeout 2 --max-time 3 \
    'http://localhost:8100/health' >/dev/null 2>&1; then
    core_ready=true
    break
  fi
  sleep 2
done
if [ "$core_ready" != true ]; then
  printf '\nERROR: Banking Core is not serving HTTP.\n' >&2
  docker compose ps -a core db >&2 || true
  docker compose logs --tail=80 core db >&2 || true
  printf '\nRun bash scripts/diagnose-core.sh for PostgreSQL locks.\n' >&2
  exit 1
fi
check_url 'Banking Core' 'http://localhost:8100/health'
check_url 'Account agent' 'http://localhost:8201/health'
check_url 'Card agent' 'http://localhost:8202/health'
check_url 'Dispute agent' 'http://localhost:8203/health'
check_url 'Pix agent' 'http://localhost:8204/health'
check_url 'Wellness agent' 'http://localhost:8205/health'
check_url 'Concierge agent' 'http://localhost:8200/health'

printf '\n>>> MCP read through Account container\n'
docker compose exec -T account python -c '
import asyncio
from agents.common import mcp_tool
async def main():
    snapshot = await mcp_tool("get_account_balance", {"customer_id": "C001"})
    assert snapshot["account"]["balance"] >= 0
    print("Customer:", snapshot["customer"]["name"])
    print("Checking balance:", snapshot["account"]["balance"])
asyncio.run(main())
'

printf '\n>>> Concierge-to-specialist read\n'
curl --fail-with-body --show-error --silent --connect-timeout 4 --max-time 90 \
  'http://localhost:8200/chat' \
  -H 'Content-Type: application/json' \
  -H "X-Agent-Call-Key: ${CONCIERGE_CALL_KEY:-local-demo-concierge-call}" \
  --data '{"customer_id":"C001","message":"What is my account balance?","language":"en"}' \
  | python3 -c '
import json,sys
result=json.load(sys.stdin)
print("Agents selected:",result.get("data",{}).get("invoked_agents"))
print("Answer:",result.get("message"))
assert "Service temporarily unavailable" not in result.get("message", ""), "Specialist failed"
assert result.get("data",{}).get("invoked_agents"), "No specialists selected"
'

printf '\n>>> Portal HTTP response\n'
curl --fail-with-body --show-error --silent --output /dev/null \
  --write-out 'Portal HTTP status: %{http_code}\n' \
  --connect-timeout 4 --max-time 20 'http://localhost:8300/'

printf '\nSUCCESS: All read-only smoke checks passed. Open http://localhost:8300, then sign in using DEMO_ACCESS_CODE.\n'
