#!/usr/bin/env bash
# Banco Aurora full-stack orchestrator — WSO2 Agent Manager v1.0.0, macOS/Colima.
# Fail closed: success means every configured validation gate passed.
set -Eeuo pipefail
umask 077
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ACTION="${1:-up}"
log(){ printf '\n[aurora-full] %s\n' "$*"; }
die(){ printf '\n[aurora-full] FAILED: %s\n' "$*" >&2; exit 1; }
need(){ command -v "$1" >/dev/null 2>&1 || die "Missing prerequisite: $1"; }
[[ "$ACTION" == up || "$ACTION" == check || "$ACTION" == agents || "$ACTION" == status || "$ACTION" == fresh ]] || die 'Usage: scripts/start-everything.sh [up|check|agents|status|fresh --yes]'
cd "$ROOT"
if [[ "$ACTION" == fresh ]]; then
  [[ $# == 2 && "${2:-}" == --yes ]] || die 'Usage: ./scripts/start-everything.sh fresh --yes (DELETES the dedicated Colima VM and all its data)'
  bash scripts/aurora-fresh-reset.sh --yes
  # Re-enter the exact same vetted startup path; never duplicate installation logic.
  exec bash scripts/start-everything.sh up
fi
export PATH="$HOME/.local/bin:$PATH"
# AURORA_V15_MACOS_DEPENDENCY_PREFLIGHT
command -v python3 >/dev/null 2>&1 \
  || die "Missing prerequisite: python3 (install Python 3.11 or 3.12 before running startup)"
python3 scripts/aurora-macos-preflight-v15.py --action "$ACTION" \
  || die 'macOS dependency preflight failed; no startup operations attempted'
need python3
# AURORA_V15_DEPENDENCY_PREFLIGHT — BEFORE any infrastructure mutation
if [[ "$ACTION" != status ]]; then
  python3 scripts/aurora-startup-preflight-v15.py dependencies --action "$ACTION" \
    || die 'Mac dependency preflight failed; see missing/unsupported tools above'
fi
need kubectl
if [[ "$ACTION" == status ]]; then
  bash scripts/start-agent-manager.sh status
  bash scripts/publish-mcp-secure-tunnel.sh status
  python3 scripts/aurora-full-reconcile.py status
  exit 0
fi
if [[ "$ACTION" == check ]]; then
  need amctl
  # AURORA_V15_AMCTL_AUTH_CHECK
  python3 scripts/aurora-startup-preflight-v15.py auth --action check \
    || die 'Agent Manager login must be verified before scenario checks'
  python3 scripts/verify-aurora-tunnel-public.py \
    || die 'MCP Quick Tunnel is stale; run ./scripts/start-everything.sh up to repair it'
  # AURORA_V14_MCP_FORWARD_GUARD
  python3 scripts/aurora-mcp-forward-guard-v14.py ensure --gateway-check \
    || die 'MCP forward/gateway proof failed; no S01-S06 writes attempted'
  # AURORA_CONCIERGE_CORE_EGRESS_VERIFY_V1
  python3 scripts/aurora-concierge-core-egress.py verify \
    || die 'Concierge cannot reach Aurora Core; run startup up to provision narrow egress'
  python3 scripts/aurora-full-reconcile.py check
  exit $?
fi
if [[ "$ACTION" == up ]]; then
  need git; need docker; need colima; need k3d; need cloudflared
  log 'Stage 0/6: Verify published agent source and generated build folders.'
  python3 scripts/sync-managed-agent-source.py ensure
  log 'Stage 1/6: Start/reuse the existing Colima + Agent Manager installation.'
  bash scripts/start-agent-manager.sh up
  need amctl
  # AURORA_V15_AMCTL_AUTH_UP
  python3 scripts/aurora-startup-preflight-v15.py auth --action up \
    || die 'Agent Manager login could not be verified before starting bank reconciliation'
  log 'Stage 2/6: Prepare config and deploy/reconcile bank backing services.'
  python3 scripts/aurora-full-reconcile.py prepare
  # 'support' is a new safe action: deploy bank backend WITHOUT re-publishing a
  # private HTTP endpoint (Agent Manager correctly blocks private IPs).
  # Rebuild if runtime source/config changed, deployments are missing or
  # unhealthy, or explicitly requested. Otherwise preserve active MCP tunnel.
  BACKEND_STATE="$ROOT/agent-manager-support/.backend-fingerprint"
  EXPECTED_BACKEND="$(python3 scripts/aurora-full-reconcile.py backend-fingerprint)"
  PREVIOUS_BACKEND=''
  [[ ! -f "$BACKEND_STATE" ]] || PREVIOUS_BACKEND="$(cat "$BACKEND_STATE")"
  if [[ "${AURORA_REFRESH_BACKEND:-0}" == 1 || "$EXPECTED_BACKEND" != "$PREVIOUS_BACKEND" ]] \
    || ! kubectl --context k3d-amp-local -n aurora-demo rollout status deployment/aurora-db --timeout=15s >/dev/null 2>&1 \
    || ! kubectl --context k3d-amp-local -n aurora-demo rollout status deployment/aurora-core --timeout=15s >/dev/null 2>&1 \
    || ! kubectl --context k3d-amp-local -n aurora-demo rollout status deployment/aurora-mcp --timeout=15s >/dev/null 2>&1 \
    || ! kubectl --context k3d-amp-local -n aurora-demo get secret aurora-support-secrets >/dev/null 2>&1; then
    log 'Backing source changed, infrastructure unhealthy, or refresh requested; reconciling.'
    bash agent-manager-support/start.sh support
    printf '%s\n' "$EXPECTED_BACKEND" > "$BACKEND_STATE"
    chmod 600 "$BACKEND_STATE"
  else
    log 'Backing services ready and source unchanged; skipping rebuild and restarts.'
  fi
  # AURORA_V15_AMCTL_AUTH_STAGE3
  python3 scripts/aurora-startup-preflight-v15.py auth --action up \
    || die 'Agent Manager session invalid before tunnel/proxy reconciliation'
  log 'Stage 3/6: Validate real HTTPS MCP origin; recover expired Quick Tunnels.'
  # PID liveness does NOT prove Cloudflare registration. The public probe
  # compares the origin's authenticated 401 marker over private + public paths.
  if ! python3 scripts/verify-aurora-tunnel-public.py >/dev/null 2>&1; then
    log 'Public MCP tunnel unavailable/stale; stop only owned tunnel processes.'
    bash scripts/publish-mcp-secure-tunnel.sh stop \
      || die 'Cannot stop the expired owned tunnel safely; inspect diagnose output'
  fi
  AURORA_ALLOW_EPHEMERAL_PROXY_UPDATE=true \
    bash scripts/publish-mcp-secure-tunnel.sh up \
    || die 'Tunnel/upstream reconciliation failed; no agent rebuild requested'
  python3 scripts/verify-aurora-tunnel-public.py \
    || die 'MCP public HTTPS origin did not pass the authenticated marker test'
  # AURORA_V14_MCP_FORWARD_GUARD
  python3 scripts/aurora-mcp-forward-guard-v14.py ensure \
    || die 'Authenticated MCP bridge lost its Kubernetes forward'
  log 'Stage 4/6: Verify identity-secured MCP registration and tool scopes.'
  python3 scripts/aurora-full-reconcile.py check-proxy
fi
if [[ "$ACTION" == agents ]]; then
  need git; need cloudflared
  # AURORA_V15_AMCTL_AUTH_AGENTS
  python3 scripts/aurora-startup-preflight-v15.py auth --action agents \
    || die 'Agent Manager login must be verified before agent reconciliation'
  log 'Preflight: Verify published agent source and generated build folders.'
  python3 scripts/sync-managed-agent-source.py ensure
  log 'Verify/recover MCP public tunnel before reusing specialist deployments.'
  if ! python3 scripts/verify-aurora-tunnel-public.py >/dev/null 2>&1; then
    bash scripts/publish-mcp-secure-tunnel.sh stop \
      || die 'Cannot stop expired owned MCP tunnel; inspect diagnose output'
  fi
  AURORA_ALLOW_EPHEMERAL_PROXY_UPDATE=true \
    bash scripts/publish-mcp-secure-tunnel.sh up \
    || die 'MCP HTTPS tunnel recovery failed; no duplicate agents requested'
  python3 scripts/verify-aurora-tunnel-public.py \
    || die 'MCP public HTTPS origin not verified'
  python3 scripts/aurora-full-reconcile.py check-proxy
fi
# AURORA_V15_AMCTL_AUTH_STAGE5
python3 scripts/aurora-startup-preflight-v15.py auth --action "$ACTION" \
  || die 'Agent Manager session invalid before agent reconciliation'
log 'Stage 5/6: Create, build, configure and deploy six managed agents.'
python3 scripts/aurora-full-reconcile.py agents
# AURORA_V14_MCP_FORWARD_GUARD
python3 scripts/aurora-mcp-forward-guard-v14.py ensure --gateway-check \
  || die 'Native gateway not healthy; refusing S01-S06 stateful checks'
# AURORA_CONCIERGE_CORE_EGRESS_ENSURE_V1
python3 scripts/aurora-concierge-core-egress.py ensure \
  || die 'Refusing S01-S06; narrow Concierge-to-Core egress could not be verified'
log 'Stage 6/6: Check role assignments, endpoints, and portal integration.'
python3 scripts/aurora-full-reconcile.py check
log 'Banco Aurora completed all configured readiness gates.'
