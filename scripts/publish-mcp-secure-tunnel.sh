#!/usr/bin/env bash
# Local development only: secure public HTTPS tunnel -> auth bridge -> private k3d MCP.
set -Eeuo pipefail
umask 077
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SUPPORT="$ROOT/agent-manager-support"
STATE="$SUPPORT/.mcp-public-tunnel"
ACTION="${1:-up}"
mkdir -p "$STATE"
chmod 700 "$STATE"
fail(){ echo "ERROR: $*" >&2; exit 1; }
need(){ command -v "$1" >/dev/null 2>&1 || fail "Missing $1 (cloudflared: brew install cloudflared)"; }
[[ -f "$SUPPORT/support.env" ]] || fail 'Missing agent-manager-support/support.env'
# non-secret config supplied by user
set -a
# shellcheck disable=SC1091
source "$SUPPORT/support.env"
set +a
KUBE_NAMESPACE="${KUBE_NAMESPACE:-aurora-demo}"
[[ "$KUBE_NAMESPACE" =~ ^[a-z0-9-]+$ ]] || fail 'Invalid namespace'
[[ -n "${KUBE_CONTEXT:-}" ]] || fail 'Configure KUBE_CONTEXT'
[[ "${DEPLOY_TARGET:-kubernetes}" == 'kubernetes' ]] || fail 'Only Kubernetes deployment supported'
[[ "${AMP_ORG:-default}" == 'default' || -n "${AMP_ORG:-}" ]] || fail 'AMP_ORG missing'
HOST="aurora-mcp.${KUBE_NAMESPACE}.svc.cluster.local"
PRIVATE_URL="http://${HOST}:8101/mcp"

proc_running(){
  local name="$1" pid
  [[ -f "$STATE/$name.pid" ]] || return 1
  pid="$(cat "$STATE/$name.pid")"
  [[ "$pid" =~ ^[0-9]+$ ]] || return 1
  kill -0 "$pid" 2>/dev/null || return 1
  local cmd
  cmd="$(ps -p "$pid" -o command= 2>/dev/null || :)"
  case "$name" in
    kpf) [[ "$cmd" == *"kubectl"*"port-forward"*"aurora-mcp"* ]];;
    bridge) [[ "$cmd" == *"secure_origin_bridge.py"* ]];;
    tunnel) [[ "$cmd" == *"cloudflared"*"tunnel"* ]];;
    *) return 1;;
  esac
}

stop_all(){
  # AURORA_V14_2_OWNED_MCP_HANDOFF
  python3 "$(dirname "${BASH_SOURCE[0]}")/aurora-mcp-forward-guard-v14.py" handoff \
    || { echo "ERROR: Safe Aurora MCP watchdog handoff failed; refusing tunnel cleanup." >&2; return 1; }
  local name pid
  for name in tunnel bridge kpf; do
    if proc_running "$name"; then
      pid="$(cat "$STATE/$name.pid")"
      kill "$pid" 2>/dev/null || :
      echo "Stopped $name (PID $pid)"
    fi
    rm -f "$STATE/$name.pid"
  done
  rm -f "$STATE/url"
}

status(){
  local name
  for name in kpf bridge tunnel; do
    if proc_running "$name"; then echo "$name: running"; else echo "$name: stopped"; fi
  done
  if [[ -f "$STATE/url" ]]; then
    echo "Public MCP URL: $(cat "$STATE/url")/mcp"
  fi
  if proc_running tunnel && proc_running bridge && proc_running kpf \
      && [[ -s "$STATE/url" && -s "$STATE/origin.key" ]]; then
    echo 'Tunnel components: complete (HTTPS still requires validation)'
  else
    echo 'Tunnel components: incomplete/stale (ensure/up will repair)'
  fi
  echo "WARNING: Cloudflare Quick Tunnel URLs are temporary. Stop the tunnel when not demonstrating."
}

ensure_key(){
  if [[ ! -f "$STATE/origin.key" ]]; then
    python3 - "$STATE/origin.key" <<'PY'
import os, pathlib, secrets, sys
p=pathlib.Path(sys.argv[1])
fd=os.open(p,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
with os.fdopen(fd,'w') as f: f.write(secrets.token_urlsafe(48)+'\n')
PY
  fi
  chmod 600 "$STATE/origin.key"
}

wait_port(){
  python3 - "$1" <<'PY'
import socket,sys,time
port=int(sys.argv[1])
for _ in range(100):
    try:
        with socket.create_connection(('127.0.0.1',port),timeout=.3):
            sys.exit(0)
    except OSError:time.sleep(.2)
sys.exit(f'Port {port} not reachable within 20 seconds')
PY
}

# Deliberately use the host's configured proxy for public HTTPS checks.
# Forcing --noproxy '*' broke publication on proxy-managed corporate networks.
# Loopback checks must bypass proxy configuration instead.
http_status(){
  local url="$1" err="$2" headers="$3" response rc=0
  shift 3
  response="$(curl "$@" --silent --show-error --output /dev/null \
    --write-out '%{http_code}' --dump-header "$headers" --connect-timeout 7 --max-time 12 \
    "$url" 2>"$err")" || rc=$?
  printf '%s %s\n' "$rc" "${response:-000}"
}

has_origin_marker(){
  local headers="$1"
  [[ -f "$headers" ]] && tr -d '\r' < "$headers" | grep -Eiq '^X-Aurora-Origin-Auth:[[:space:]]*required[[:space:]]*$'
}

verify_local_origin(){
  local rc code
  read -r rc code < <(http_status 'http://127.0.0.1:18102/mcp' \
    "$STATE/local-curl.err" "$STATE/local-headers.txt" --noproxy '*')
  if [[ "$rc" == 0 && "$code" == 401 ]] && has_origin_marker "$STATE/local-headers.txt"; then
    return 0
  fi
  echo "Local bridge failed marked HTTP 401 check (HTTP $code; curl exit $rc). See $STATE/bridge.log and $STATE/local-curl.err" >&2
  return 1
}

print_diagnostics(){
  echo '--- Tunnel diagnostics (no secrets) ---' >&2
  local name
  for name in kpf bridge tunnel; do
    if proc_running "$name"; then echo "$name process: running" >&2
    else echo "$name process: not running" >&2; fi
  done
  if [[ -f "$STATE/url" ]]; then echo "Public URL: $(cat "$STATE/url")/mcp" >&2; fi
  if [[ -s "$STATE/public-curl.err" ]]; then
    echo 'Last public curl error:' >&2
    tail -n 5 "$STATE/public-curl.err" >&2
  fi
  if [[ -f "$STATE/cloudflared.log" ]]; then
    echo 'Last cloudflared lines:' >&2
    tail -n 24 "$STATE/cloudflared.log" >&2
  fi
  if [[ -f "$STATE/bridge.log" ]]; then
    echo 'Last bridge lines:' >&2
    tail -n 8 "$STATE/bridge.log" >&2
  fi
  if [[ -f "$STATE/kubectl.log" ]]; then
    echo 'Last port-forward lines:' >&2
    tail -n 6 "$STATE/kubectl.log" >&2
  fi
  echo 'No registration was attempted unless HTTP 401 was confirmed through public HTTPS.' >&2
}

# curl error 6 means DNS; retry using DoH when the local curl supports it.
# All paths retain normal TLS validation. Never fall back to --insecure.
verify_public_origin(){
  local target="$1" attempts="${AURORA_PUBLIC_PROBE_ATTEMPTS:-40}"
  [[ "$attempts" =~ ^[0-9]+$ ]] && (( attempts >= 1 && attempts <= 120 )) \
    || fail 'AURORA_PUBLIC_PROBE_ATTEMPTS must be 1..120'
  local i rc code
  for ((i=1;i<=attempts;i++)); do
    proc_running tunnel && proc_running bridge && proc_running kpf || {
      echo 'Tunnel or local relay stopped during the public verification.' >&2
      print_diagnostics
      return 1
    }
    read -r rc code < <(http_status "$target/mcp" "$STATE/public-curl.err" "$STATE/public-headers.txt")
    if [[ "$rc" == 0 && "$code" == 401 ]] && has_origin_marker "$STATE/public-headers.txt"; then
      echo "Verified: public HTTPS endpoint reached authenticated Aurora bridge (marked 401; attempt $i)."
      return 0
    fi
    # Unrecognized HTTP status is still a genuine response; never publish.
    if [[ "$rc" == 0 && "$code" != 000 ]]; then
      if [[ "$code" == 401 ]]; then
        echo "Public endpoint returned 401, but not the Aurora origin marker (attempt $i/$attempts)." >&2
      else
        echo "Public tunnel replied HTTP $code, expected marked 401 (attempt $i/$attempts)." >&2
      fi
    elif [[ "$rc" == 6 ]]; then
      echo "Public tunnel DNS unresolved (attempt $i/$attempts)." >&2
      # DoH is only a DNS alternative; TLS certificate verification stays on.
      if curl --help all 2>/dev/null | grep -q -- '--doh-url'; then
        read -r rc code < <(http_status "$target/mcp" "$STATE/public-curl.err" \
          "$STATE/public-headers.txt" --doh-url 'https://1.1.1.1/dns-query')
        if [[ "$rc" == 0 && "$code" == 401 ]] && has_origin_marker "$STATE/public-headers.txt"; then
          echo "Verified: public HTTPS endpoint reached authenticated Aurora bridge (marked 401 via DoH)."
          return 0
        fi
      fi
    elif [[ "$rc" == 28 ]]; then
      echo "Public tunnel timed out (attempt $i/$attempts)." >&2
    elif [[ "$rc" == 60 ]]; then
      echo 'Public HTTPS certificate verification failed; refusing to publish.' >&2
      print_diagnostics
      return 1
    else
      echo "Public tunnel not reachable (curl exit $rc, HTTP $code; attempt $i/$attempts)." >&2
    fi
    # Cloudflare URL registration and DNS visibility are not instantaneous.
    if (( i < attempts )); then sleep 3; fi
  done
  echo "ERROR: Public HTTPS origin never returned 401 after $attempts attempts." >&2
  print_diagnostics
  return 1
}

verify_unauthorized(){
  verify_public_origin "$1" || fail 'Cannot verify public origin security/reachability.'
}

publish(){
  need curl;need python3;need amctl;need kubectl
  [[ -f "$STATE/url" && -f "$STATE/origin.key" ]] || fail "No active tunnel credentials. Run: $0 up"
  proc_running tunnel && proc_running bridge && proc_running kpf || fail "Tunnel services not all running. Run: $0 stop; $0 up"
  local public_url origin_key
  public_url="$(cat "$STATE/url")"
  [[ "$public_url" =~ ^https://[a-z0-9-]+\.trycloudflare\.com$ ]] || fail 'Tunnel hostname does not match expected Cloudflare Quick Tunnel pattern'
  verify_public_origin "$public_url"
  origin_key="$(cat "$STATE/origin.key")"
  [[ ${#origin_key} -ge 32 ]] || fail 'Origin secret too weak'
  echo "Registering authenticated upstream $public_url/mcp (secret not printed)"
  AURORA_MCP_UPSTREAM_URL="$public_url/mcp" \
  AURORA_MCP_DISCOVERY_URL="$PRIVATE_URL" \
  AURORA_MCP_ORIGIN_KEY="$origin_key" \
  AMP_DISCOVERY_MODE=cluster \
    bash "$SUPPORT/start.sh" publish
  echo "Published MCP proxy. Keep the tunnel running while agents use this proxy."
}

port_available(){
  # Refuse to replace unknown port owners. Keep loopback-only binding.
  python3 - "$1" <<'PYPORT_50812'
import socket,sys
s=socket.socket()
try: s.bind(('127.0.0.1',int(sys.argv[1])))
except OSError: sys.exit(1)
finally: s.close()
PYPORT_50812
}

log_url(){
  [[ -f "$STATE/cloudflared.log" ]] || return 0
  python3 - "$STATE/cloudflared.log" <<'PYURL_50812'
from pathlib import Path
import re,sys
s=Path(sys.argv[1]).read_text(errors='replace')
m=re.findall(r'https://[a-z0-9-]+\.trycloudflare\.com\b',s)
print(m[-1] if m else '')
PYURL_50812
}

start_tunnel_fresh(){
  need kubectl;need cloudflared;need python3;need curl;need amctl
  local i ready=0
  # SIGTERM is asynchronous. Give owned listeners a bounded chance to exit.
  for ((i=0;i<30;i++)); do
    if port_available 18101 && port_available 18102; then ready=1; break; fi
    sleep 0.25
  done
  (( ready == 1 )) || fail 'Port 18101/18102 still occupied after cleanup. Another process may own the port; refusing to kill it.'
  ensure_key
  echo 'Checking healthy MCP deployment (no redeploy/restart)...'
  kubectl --context "$KUBE_CONTEXT" -n "$KUBE_NAMESPACE" rollout status deployment/aurora-mcp --timeout=30s
  echo 'Starting private Kubernetes port-forward...'
  nohup kubectl --context "$KUBE_CONTEXT" -n "$KUBE_NAMESPACE" \
    port-forward svc/aurora-mcp 18101:8101 --address 127.0.0.1 \
    >"$STATE/kubectl.log" 2>&1 </dev/null &
  echo "$!" > "$STATE/kpf.pid"
  wait_port 18101 || { stop_all;fail 'MCP port-forward failed'; }
  echo 'Starting authenticated local origin bridge...'
  nohup python3 "$SUPPORT/secure_origin_bridge.py" \
    --host "$HOST" --port 18102 --upstream-port 18101 --key-file "$STATE/origin.key" \
    >"$STATE/bridge.log" 2>&1 </dev/null &
  echo "$!" > "$STATE/bridge.pid"
  wait_port 18102 || { stop_all;fail 'Origin bridge failed'; }
  verify_local_origin || { stop_all;fail 'Authenticated local bridge did not become ready'; }
  echo 'Starting temporary HTTPS Cloudflare Quick Tunnel...'
  nohup cloudflared tunnel --url http://127.0.0.1:18102 \
    >"$STATE/cloudflared.log" 2>&1 </dev/null &
  echo "$!" > "$STATE/tunnel.pid"
  local url=''
  for _ in $(seq 1 90);do
    url="$(log_url)"
    [[ -n "$url" ]] && break
    proc_running tunnel || { cat "$STATE/cloudflared.log" >&2;stop_all;fail 'Cloudflared exited before publishing a URL'; }
    sleep 1
  done
  [[ -n "$url" ]] || { stop_all;fail 'Cloudflared did not return a temporary public URL'; }
  echo "$url" > "$STATE/url"
  echo "HTTPS upstream: $url/mcp"
  # Wait for a *real* HTTP 401 over HTTPS, not merely an announced hostname.
  # If verification fails, preserve logs, then stop all public exposure.
  verify_public_origin "$url" || { stop_all; fail 'Public Quick Tunnel could not be verified. Logs were preserved; run: scripts/publish-mcp-secure-tunnel.sh diagnose'; }
  echo "WARNING: Quick Tunnel URL is ephemeral; see README for stable named-tunnel setup."
}

ensure_tunnel(){
  need kubectl;need cloudflared;need python3;need curl;need amctl
  echo 'Checking MCP pod readiness without restarting it...'
  kubectl --context "$KUBE_CONTEXT" -n "$KUBE_NAMESPACE" rollout status deployment/aurora-mcp --timeout=60s
  local current_url='' announced_url=''
  [[ ! -f "$STATE/url" ]] || current_url="$(cat "$STATE/url")"
  announced_url="$(log_url)"
  if proc_running kpf && proc_running bridge && proc_running tunnel \
      && [[ -s "$STATE/origin.key" ]] \
      && [[ "$current_url" =~ ^https://[a-z0-9-]+\.trycloudflare\.com$ ]] \
      && [[ "$announced_url" == "$current_url" ]]; then
    echo "Existing authenticated tunnel: $current_url/mcp"
    if verify_local_origin && verify_public_origin "$current_url"; then
      echo 'Reusing verified tunnel without process restart or hostname rotation.'
      return 0
    fi
    echo 'Previously running tunnel is unhealthy. Restarting only its tracked local processes.' >&2
  else
    echo 'Incomplete or stale tunnel state detected. Reconciling local processes.'
  fi
  # Signal only process IDs that we can confirm are owned by this script.
  stop_all
  start_tunnel_fresh
}

case "$ACTION" in
  up|start) ensure_tunnel; publish;;
  ensure) ensure_tunnel;;
  publish) publish;;
  status) status;;
  diagnose) print_diagnostics;;
  stop) stop_all;;
  *) fail 'Usage: publish-mcp-secure-tunnel.sh [up|ensure|publish|status|diagnose|stop]';;
esac
