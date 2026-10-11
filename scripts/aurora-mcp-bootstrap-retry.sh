#!/usr/bin/env bash
# Recover transient Cloudflare Quick Tunnel failure DURING the same startup.
# Reconciles only project-owned tunnel processes and the idempotent MCP proxy.
# Never weakens authenticated-origin verification or resets K3d/Colima/agents.
set -Eeuo pipefail
umask 077
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

log() { printf '[aurora-mcp-recovery] %s\n' "$*"; }
fail() { printf '[aurora-mcp-recovery] ERROR: %s\n' "$*" >&2; exit 1; }

# Default maximum: three tunnel lifecycles, with a bounded post-publish grace.
cycles="${AURORA_MCP_BOOTSTRAP_CYCLES:-3}"
probes="${AURORA_MCP_STABILIZATION_PROBES:-6}"
wait="${AURORA_MCP_STABILIZATION_SECONDS:-5}"
backoff="${AURORA_MCP_RECOVERY_BACKOFF_SECONDS:-8}"
[[ "$cycles" =~ ^[1-5]$ ]] || fail 'AURORA_MCP_BOOTSTRAP_CYCLES must be 1..5'
[[ "$probes" =~ ^([1-9]|1[0-2])$ ]] || fail 'AURORA_MCP_STABILIZATION_PROBES must be 1..12'
[[ "$wait" =~ ^([0-9]|[12][0-9]|30)$ ]] || fail 'AURORA_MCP_STABILIZATION_SECONDS must be 0..30'
[[ "$backoff" =~ ^([0-9]|[12][0-9]|30)$ ]] || fail 'AURORA_MCP_RECOVERY_BACKOFF_SECONDS must be 0..30'
[[ -f scripts/verify-aurora-tunnel-public.py ]] || fail 'Missing strict public-origin verifier'
[[ -f scripts/publish-mcp-secure-tunnel.sh ]] || fail 'Missing MCP publisher'

# A successful *current* public-origin probe is necessary; a live PID is never
# treated as tunnel health. This is the same strict checker used by `check`.
verify_public() {
  python3 scripts/verify-aurora-tunnel-public.py
}

for ((cycle=1; cycle<=cycles; cycle++)); do
  log "MCP tunnel publish/verification cycle $cycle/$cycles"

  # First cycle keeps an already-verified tunnel. After any failed cycle,
  # rotate the owned tunnel to avoid repeatedly using a dead Cloudflare route.
  if ((cycle > 1)) || ! verify_public >/dev/null 2>&1; then
    log 'Releasing only owned Cloudflare/bridge/port-forward processes.'
    bash scripts/publish-mcp-secure-tunnel.sh stop \
      || fail 'Cannot safely release owned tunnel processes; refusing further recovery'
  fi

  # `up` verifies marked 401 and idempotently updates the same known MCP proxy.
  # Do not try to create agents or mutate infrastructure until stable.
  if AURORA_ALLOW_EPHEMERAL_PROXY_UPDATE=true \
       bash scripts/publish-mcp-secure-tunnel.sh up; then
    for ((probe=1; probe<=probes; probe++)); do
      if verify_public; then
        log "Authenticated public-origin verified after publication (probe $probe/$probes; cycle $cycle/$cycles)."
        exit 0
      fi
      if ((probe < probes)); then
        log "Transient public-origin check failure; allowing stabilization ($probe/$probes)."
        sleep "$wait"
      fi
    done
    log 'Tunnel publisher completed, but the independently authenticated public check remains unhealthy.'
  else
    log 'Tunnel publication failed. Will retry using a new owned tunnel if attempts remain.'
  fi

  if ((cycle < cycles)); then
    log "Retrying tunnel lifecycle after ${backoff}s (no Agent Manager reinstall or agent rebuild)."
    sleep "$backoff"
  fi
done

fail "MCP HTTPS did not stabilize in $cycles cycles. Refusing Stage 4/5/6; inspect: bash scripts/publish-mcp-secure-tunnel.sh diagnose"
