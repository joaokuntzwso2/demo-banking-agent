#!/usr/bin/env bash
# Banking Core diagnostics. Read-only; never deletes the database.
set -euo pipefail
cd "$(dirname "$0")/.."
echo '=== Service status ==='
docker compose ps -a db core || true
echo '=== Recent Core / PostgreSQL logs ==='
docker compose logs --tail=100 db core || true
echo '=== Banking Core HTTP health ==='
curl --verbose --connect-timeout 3 --max-time 10 http://localhost:8100/health || true
echo

echo '=== PostgreSQL client sessions, lock waits and blocking processes ==='
docker compose exec -T db psql -U aurora -d aurora -c   "SELECT pid, state, wait_event_type, wait_event, pg_blocking_pids(pid) AS blocked_by, LEFT(query, 140) AS query FROM pg_stat_activity WHERE datname = 'aurora' AND backend_type = 'client backend' ORDER BY pid;" || true

echo '=== Seed data counts ==='
docker compose exec -T db psql -U aurora -d aurora -c   "SELECT CASE WHEN to_regclass('public.customers') IS NULL THEN 'missing' ELSE 'present' END AS customer_table;" || true

echo '=== Core health check state ==='
container_id="$(docker compose ps -q core)"
if [ -n "$container_id" ]; then
  docker inspect "$container_id" --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}no healthcheck configured{{end}}' || true
fi
