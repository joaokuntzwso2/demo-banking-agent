#!/usr/bin/env bash
# One-command Banco Aurora supporting services + Agent Manager MCP Proxy publisher.
# Creates only a synthetic backend. Does NOT deploy six agents.
set -Eeuo pipefail
umask 077
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/.." && pwd)"
CONFIG="$HERE/support.env"
SECRETS="$HERE/secrets.env"
ACTION="${1:-start}"

log() { printf '\n>>> %s\n' "$*"; }
die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }
need() { command -v "$1" >/dev/null 2>&1 || die "Missing prerequisite: $1"; }

if [[ ! -f "$ROOT/mock_core/app.py" || ! -f "$ROOT/mcp_server/app.py" ]]; then
  die "Place the entire 'agent-manager-support' directory directly inside your demo-banking-agent Git checkout."
fi
if [[ ! -f "$CONFIG" ]]; then
  cp "$HERE/support.env.example" "$CONFIG"
  chmod 600 "$CONFIG"
  die "Created $CONFIG. Edit its deployment settings, then run ./start.sh again."
fi
# support.env contains persisted defaults. Preserve explicitly supplied runtime
# overrides from the secure tunnel launcher, which must be authoritative.
# In particular, losing AURORA_MCP_UPSTREAM_URL here silently substitutes the
# private Kubernetes HTTP URL for the verified public HTTPS tunnel endpoint.
_AURORA_UPSTREAM_SET="${AURORA_MCP_UPSTREAM_URL+x}"
_AURORA_UPSTREAM_VALUE="${AURORA_MCP_UPSTREAM_URL-}"
_AURORA_DISCOVERY_SET="${AURORA_MCP_DISCOVERY_URL+x}"
_AURORA_DISCOVERY_VALUE="${AURORA_MCP_DISCOVERY_URL-}"
_AURORA_ORIGIN_SET="${AURORA_MCP_ORIGIN_KEY+x}"
_AURORA_ORIGIN_VALUE="${AURORA_MCP_ORIGIN_KEY-}"
_AURORA_MODE_SET="${AMP_DISCOVERY_MODE+x}"
_AURORA_MODE_VALUE="${AMP_DISCOVERY_MODE-}"

# Only user-maintained configuration is sourced. Do not log secret values.
set -a
# shellcheck disable=SC1090
source "$CONFIG"
set +a

if [[ "$_AURORA_UPSTREAM_SET" == x ]]; then
  export AURORA_MCP_UPSTREAM_URL="$_AURORA_UPSTREAM_VALUE"
fi
if [[ "$_AURORA_DISCOVERY_SET" == x ]]; then
  export AURORA_MCP_DISCOVERY_URL="$_AURORA_DISCOVERY_VALUE"
fi
if [[ "$_AURORA_ORIGIN_SET" == x ]]; then
  export AURORA_MCP_ORIGIN_KEY="$_AURORA_ORIGIN_VALUE"
fi
if [[ "$_AURORA_MODE_SET" == x ]]; then
  export AMP_DISCOVERY_MODE="$_AURORA_MODE_VALUE"
fi
unset _AURORA_UPSTREAM_SET _AURORA_UPSTREAM_VALUE \
  _AURORA_DISCOVERY_SET _AURORA_DISCOVERY_VALUE \
  _AURORA_ORIGIN_SET _AURORA_ORIGIN_VALUE \
  _AURORA_MODE_SET _AURORA_MODE_VALUE
DEPLOY_TARGET="${DEPLOY_TARGET:-kubernetes}"
KUBE_NAMESPACE="${KUBE_NAMESPACE:-aurora-demo}"
AMP_PUBLISH="${AMP_PUBLISH:-true}"
PORTAL_ENABLED="${PORTAL_ENABLED:-false}"
export AURORA_REPO_ROOT="$ROOT"

case "$KUBE_NAMESPACE" in
  *[!a-z0-9-]*|'') die "KUBE_NAMESPACE must be lowercase DNS-label characters";;
esac

initialize_secrets() {
  need python3
  export AURORA_SUPPORT_SECRETS_FILE="$SECRETS"
  python3 - <<'PY'
import os, secrets
from pathlib import Path
p=Path(os.environ['AURORA_SUPPORT_SECRETS_FILE'])
old={}
if p.exists():
    for line in p.read_text().splitlines():
        if not line or line.startswith('#') or '=' not in line: continue
        k,v=line.split('=',1);old[k]=v
values={
    'POSTGRES_PASSWORD':secrets.token_hex(24),
    'BANK_CORE_READ_KEY':secrets.token_hex(32),
    'BANK_CORE_WRITE_KEY':secrets.token_hex(32),
    'BANK_APPROVAL_KEY':secrets.token_hex(32),
    'BANK_RESET_KEY':secrets.token_hex(32),
    'BANK_CASE_OPERATOR_KEY':secrets.token_hex(32),
    'CONCIERGE_CALL_KEY':secrets.token_hex(32),
    'CONCIERGE_APPROVAL_KEY':secrets.token_hex(32),
    'DEMO_ACCESS_CODE':secrets.token_urlsafe(18),
    'DEMO_RESET_CODE':secrets.token_urlsafe(18),
    'DEMO_OPERATOR_CODE':secrets.token_urlsafe(18),
    'DEMO_SESSION_SIGNING_KEY':secrets.token_hex(32),
    'CONCIERGE_URL':'',
    'CONCIERGE_API_KEY':'',
}
values.update(old)
values['DATABASE_URL']=f"postgresql+psycopg://aurora:{values['POSTGRES_PASSWORD']}@aurora-db.{os.environ.get('KUBE_NAMESPACE','aurora-demo')}.svc.cluster.local:5432/aurora"
with p.open('w') as f:
    f.write('# Private demo-only secrets. NEVER commit. Keep POSTGRES_PASSWORD stable after DB creation.\n')
    for k,v in values.items():
        if '\n' in v or '\r' in v:raise ValueError('Newlines not allowed in secret values')
        f.write(f'{k}={v}\n')
os.chmod(p,0o600)
PY
  # Only grep keys, never show values in logs.
  chmod 600 "$SECRETS"
}

validate_portal() {
  if [[ "$PORTAL_ENABLED" == true ]]; then
    python3 - "$SECRETS" <<'PY'
import sys
vals={}
for line in open(sys.argv[1]):
    if '=' in line and not line.startswith('#'):
        k,v=line.strip().split('=',1);vals[k]=v
for k in ('CONCIERGE_URL','CONCIERGE_API_KEY'):
    if not vals.get(k):
        raise SystemExit(f'ERROR: Set {k} in secrets.env after the managed Concierge is deployed')
PY
  fi
}

kubectl_cmd() { kubectl --context "$KUBE_CONTEXT" "$@"; }

run_kubernetes() {
  need kubectl
  [[ -n "${KUBE_CONTEXT:-}" && "$KUBE_CONTEXT" != REPLACE* ]] || die 'Set KUBE_CONTEXT in support.env'
  [[ "$(kubectl config current-context)" == "$KUBE_CONTEXT" ]] || die "Active kubectl context differs from KUBE_CONTEXT=$KUBE_CONTEXT. Switch deliberately with kubectl config use-context."
  [[ -n "${AURORA_IMAGE:-}" && "$AURORA_IMAGE" != REPLACE* ]] || die 'Set AURORA_IMAGE to a pullable registry image (or configure BUILD_AND_PUSH=true)'
  [[ "$AURORA_IMAGE" =~ ^[a-zA-Z0-9./:_-]+$ ]] || die 'Invalid AURORA_IMAGE reference'
  need python3
  local pull_policy='Always'
  case "${IMAGE_DELIVERY:-k3d}" in
    k3d)
      need docker; need k3d
      [[ -n "${K3D_CLUSTER:-}" ]] || die 'Set K3D_CLUSTER to your Agent Manager k3d cluster name'
      log "Building Banco Aurora locally and importing into k3d cluster $K3D_CLUSTER"
      docker build -t "$AURORA_IMAGE" "$ROOT"
      k3d image import "$AURORA_IMAGE" -c "$K3D_CLUSTER"
      pull_policy='IfNotPresent'
      ;;
    registry)
      if [[ "${BUILD_AND_PUSH:-false}" == true ]]; then
        need docker
        log "Building and pushing image $AURORA_IMAGE (explicit BUILD_AND_PUSH=true)"
        args=(buildx build --push -t "$AURORA_IMAGE")
        if [[ -n "${AURORA_IMAGE_PLATFORM:-}" ]]; then
          args+=(--platform "$AURORA_IMAGE_PLATFORM")
        fi
        docker "${args[@]}" "$ROOT"
      fi
      ;;
    *) die 'IMAGE_DELIVERY must be k3d or registry';;
  esac
  log "Creating namespace and private secrets in context $KUBE_CONTEXT"
  kubectl_cmd create namespace "$KUBE_NAMESPACE" --dry-run=client -o yaml | kubectl_cmd apply -f - >/dev/null
  kubectl_cmd -n "$KUBE_NAMESPACE" create secret generic aurora-support-secrets \
    --from-env-file="$SECRETS" --dry-run=client -o yaml | kubectl_cmd apply -f - >/dev/null
  # Use a global path in the EXIT trap. Bash function-local variables are
  # unset when the function returns; referencing them with `set -u` crashed
  # the publisher even after workloads were successfully deployed.
  local tmp
  tmp="$(mktemp)"
  AURORA_RENDER_TMP="$tmp"
  trap 'if [[ -n "${AURORA_RENDER_TMP:-}" ]]; then rm -f -- "$AURORA_RENDER_TMP"; fi' EXIT
  export AURORA_RENDER_NS="$KUBE_NAMESPACE" AURORA_RENDER_IMAGE="$AURORA_IMAGE" AURORA_RENDER_PULL_POLICY="$pull_policy" AURORA_RENDER_TEMPLATE="$HERE/k8s/support.yaml" AURORA_RENDER_OUT="$tmp"
  python3 - <<'PY'
import os
from pathlib import Path
s=Path(os.environ['AURORA_RENDER_TEMPLATE']).read_text()
ns=os.environ['AURORA_RENDER_NS']
allowed=os.getenv('MCP_ALLOWED_HOSTS','').strip() or f'aurora-mcp:*,aurora-mcp.{ns}.svc.cluster.local:*'
# Always allow cluster DNS of the private service (and no global wildcard).
for mandatory in (f'aurora-mcp.{ns}.svc.cluster.local:*','aurora-mcp:*'):
    if mandatory not in allowed.split(','):allowed+=','+mandatory
if "'" in allowed or '\n' in allowed:raise SystemExit('Invalid MCP_ALLOWED_HOSTS')
s=s.replace('__NS__',ns).replace('__IMAGE__',os.environ['AURORA_RENDER_IMAGE']).replace('__PULL_POLICY__',os.environ['AURORA_RENDER_PULL_POLICY']).replace('__MCP_ALLOWED_HOSTS__',allowed)
Path(os.environ['AURORA_RENDER_OUT']).write_text(s)
PY
  kubectl_cmd apply -f "$tmp"
  kubectl_cmd -n "$KUBE_NAMESPACE" rollout status deployment/aurora-db --timeout=180s
  kubectl_cmd -n "$KUBE_NAMESPACE" rollout restart deployment/aurora-core deployment/aurora-mcp >/dev/null
  kubectl_cmd -n "$KUBE_NAMESPACE" rollout status deployment/aurora-core --timeout=180s
  kubectl_cmd -n "$KUBE_NAMESPACE" rollout status deployment/aurora-mcp --timeout=180s
  if [[ "$PORTAL_ENABLED" == true ]]; then
    export AURORA_RENDER_TEMPLATE="$HERE/k8s/portal.yaml"
    python3 - <<'PY'
import os
from pathlib import Path
s=Path(os.environ['AURORA_RENDER_TEMPLATE']).read_text()
s=s.replace('__NS__',os.environ['AURORA_RENDER_NS']).replace('__IMAGE__',os.environ['AURORA_RENDER_IMAGE']).replace('__PULL_POLICY__',os.environ['AURORA_RENDER_PULL_POLICY'])
Path(os.environ['AURORA_RENDER_OUT']).write_text(s)
PY
    kubectl_cmd apply -f "$tmp"
    kubectl_cmd -n "$KUBE_NAMESPACE" rollout restart deployment/aurora-portal >/dev/null
    kubectl_cmd -n "$KUBE_NAMESPACE" rollout status deployment/aurora-portal --timeout=180s
  fi
  kubectl_cmd -n "$KUBE_NAMESPACE" get pods,svc
  export AURORA_MCP_UPSTREAM_URL="${AURORA_MCP_UPSTREAM_URL:-http://aurora-mcp.${KUBE_NAMESPACE}.svc.cluster.local:8101/mcp}"
  log "Private upstream MCP URL: $AURORA_MCP_UPSTREAM_URL"
  printf 'Core URL for deployed Concierge: http://aurora-core.%s.svc.cluster.local:8100\n' "$KUBE_NAMESPACE"
  [[ "$PORTAL_ENABLED" != true ]] || echo "UI: ./start.sh portal (port-forward to http://localhost:8300)"
}

run_docker() {
  need docker
  export MCP_HOST_BIND="${MCP_HOST_BIND:-127.0.0.1}"
  if [[ "$MCP_HOST_BIND" != '127.0.0.1' && "$MCP_HOST_BIND" != '::1' ]]; then
    [[ "${ALLOW_PRIVATE_LAN_BIND:-false}" == true ]] || die 'Refusing to expose unprotected upstream MCP off loopback. Use Kubernetes ClusterIP/private ingress, or deliberately set ALLOW_PRIVATE_LAN_BIND=true on an isolated trusted network.'
    echo 'WARNING: direct upstream MCP has no OAuth. Restrict ingress to the Agent Manager gateway using firewall/network rules.' >&2
  fi
  log 'Starting PostgreSQL, Banking Core and upstream MCP with Docker Compose'
  files=(--env-file "$CONFIG" --env-file "$SECRETS" -f "$HERE/compose.support.yaml")
  if [[ "$PORTAL_ENABLED" == true ]]; then
    docker compose "${files[@]}" --profile portal up -d --build --wait
  else
    docker compose "${files[@]}" up -d --build --wait db core mcp
  fi
  docker compose "${files[@]}" ps
  local core_url="http://localhost:${HOST_CORE_PORT:-8110}/health"
  python3 - "$core_url" <<'PY'
import sys,urllib.request
with urllib.request.urlopen(sys.argv[1],timeout=5) as r:
    if r.status!=200:raise SystemExit('Core health check failed')
print('Core readiness verified')
PY
  echo "Local Core: http://localhost:${HOST_CORE_PORT:-8110}"
  echo "Local upstream MCP: http://localhost:${HOST_MCP_PORT:-8111}/mcp"
  if [[ "$PORTAL_ENABLED" == true ]]; then
    echo "UI: http://localhost:${HOST_PORTAL_PORT:-8330}"
  fi
}

publish() {
  if [[ "$AMP_PUBLISH" != true ]]; then
    log 'AMP_PUBLISH=false — upstream is running; MCP Proxy was not registered.'
    return
  fi
  need amctl
  [[ -n "${AURORA_MCP_UPSTREAM_URL:-}" ]] || die 'Set AURORA_MCP_UPSTREAM_URL to a PRIVATE reachable address; localhost is not reachable from remote Agent Manager.'
  if [[ "$AURORA_MCP_UPSTREAM_URL" =~ ^https?://(localhost|127\.0\.0\.1|0\.0\.0\.0|host\.docker\.internal)(:|/) ]]; then
    die 'Agent Manager cannot use a localhost/Docker-host URL as a remote upstream. Configure private ingress or use same-cluster Kubernetes deployment.'
  fi
  log 'Publishing identity-secured Banco Aurora MCP Proxy with amctl'
  python3 "$HERE/publish_proxy.py" --repo "$ROOT"
}

# Safe recovery after a successful Core/MCP deployment but failed AMP
# registration. Does not rebuild images, restart pods, or touch DB state.
publish_only() {
  [[ "$DEPLOY_TARGET" == kubernetes ]] || die 'publish action requires DEPLOY_TARGET=kubernetes'
  need kubectl
  [[ -n "${KUBE_CONTEXT:-}" ]] || die 'Set KUBE_CONTEXT in support.env'
  [[ "$(kubectl config current-context)" == "$KUBE_CONTEXT" ]] || die "Active kubectl context differs from $KUBE_CONTEXT"
  kubectl_cmd -n "$KUBE_NAMESPACE" rollout status deployment/aurora-core --timeout=30s
  kubectl_cmd -n "$KUBE_NAMESPACE" rollout status deployment/aurora-mcp --timeout=30s
  export AURORA_MCP_UPSTREAM_URL="${AURORA_MCP_UPSTREAM_URL:-http://aurora-mcp.${KUBE_NAMESPACE}.svc.cluster.local:8101/mcp}"
  log "Reusing existing upstream MCP at $AURORA_MCP_UPSTREAM_URL (no workloads restarted)"
  publish
}

case "$ACTION" in
  start|support)
    initialize_secrets
    validate_portal
    case "$DEPLOY_TARGET" in
      kubernetes) run_kubernetes;;
      docker) run_docker;;
      *) die "DEPLOY_TARGET must be kubernetes or docker";;
    esac
    if [[ "$ACTION" == start ]]; then
      publish
      log 'Completed: backing services started; MCP Proxy publication requested and validated.'
    else
      log 'Backing services are ready. MCP publication intentionally deferred to authenticated HTTPS tunnel.'
    fi
    ;;
  publish)
    publish_only
    ;;
  status)
    case "$DEPLOY_TARGET" in
      kubernetes) need kubectl; kubectl_cmd -n "$KUBE_NAMESPACE" get pods,svc;;
      docker) need docker; docker compose --env-file "$CONFIG" --env-file "$SECRETS" -f "$HERE/compose.support.yaml" ps;;
    esac
    ;;
  portal)
    [[ "$DEPLOY_TARGET" == kubernetes ]] || die 'Docker mode uses the configured HOST_PORTAL_PORT, not kubectl port-forward.'
    [[ "$PORTAL_ENABLED" == true ]] || die 'Set PORTAL_ENABLED=true and configure Concierge secrets before forwarding portal'
    need kubectl
    echo 'Open http://localhost:8300 (Ctrl+C to stop forwarding)'
    kubectl_cmd -n "$KUBE_NAMESPACE" port-forward svc/aurora-portal 8300:8300
    ;;
  stop)
    if [[ "$DEPLOY_TARGET" == docker ]]; then
      need docker
      docker compose --env-file "$CONFIG" --env-file "$SECRETS" -f "$HERE/compose.support.yaml" --profile portal down
      echo 'Containers stopped; persistent PostgreSQL volume retained; Agent Manager proxy unchanged.'
    else
      need kubectl
      kubectl_cmd -n "$KUBE_NAMESPACE" scale deployment/aurora-core deployment/aurora-mcp deployment/aurora-db --replicas=0
      [[ "$PORTAL_ENABLED" != true ]] || kubectl_cmd -n "$KUBE_NAMESPACE" scale deployment/aurora-portal --replicas=0
      echo 'Workloads scaled down; PostgreSQL PVC retained; Agent Manager proxy unchanged.'
    fi
    ;;
  dry-run)
    initialize_secrets
    [[ -n "${AURORA_MCP_UPSTREAM_URL:-}" ]] || export AURORA_MCP_UPSTREAM_URL="http://aurora-mcp.${KUBE_NAMESPACE}.svc.cluster.local:8101/mcp"
    python3 "$HERE/publish_proxy.py" --repo "$ROOT" --dry-run
    echo 'Dry run complete (no Kubernetes, Docker or Agent Manager API changes).'
    ;;
  *) die 'Usage: ./start.sh [start|publish|status|portal|stop|dry-run]';;
esac
