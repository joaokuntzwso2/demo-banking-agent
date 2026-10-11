#!/usr/bin/env bash
# Banco Aurora — idempotent local WSO2 Agent Manager v1.0.0 bootstrap.
# Run from any directory: bash scripts/start-agent-manager.sh [up|status|console|banking]
# Uses an isolated, pre-sized Colima VM. Never deletes an existing cluster or VM.
set -Eeuo pipefail

AMP_VERSION="${AMP_VERSION:-1.0.0}"
AMP_COLIMA_PROFILE="${AMP_COLIMA_PROFILE:-agent-manager}"
AMP_K3D_CLUSTER="${AMP_K3D_CLUSTER:-amp-local}"
AMP_COLIMA_CPU="${AMP_COLIMA_CPU:-8}"
AMP_COLIMA_MEMORY="${AMP_COLIMA_MEMORY:-16}"
AMP_COLIMA_DISK="${AMP_COLIMA_DISK:-100}"
AMP_API_URL="${AMP_API_URL:-http://api.amp.localhost:8080}"
AMP_CONSOLE_URL="${AMP_CONSOLE_URL:-http://console.amp.localhost:8080}"
AMP_IMAGE="ghcr.io/wso2/amp-quick-start:v${AMP_VERSION}"
ACTION="${1:-up}"
SELF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SELF_DIR}/.." && pwd)"

say() { printf '\n[aurora-amp] %s\n' "$*"; }
err() { printf '[aurora-amp] ERROR: %s\n' "$*" >&2; }
die() { err "$*"; exit 1; }
need() { command -v "$1" >/dev/null 2>&1 || die "Missing '$1'. Install it before running this script."; }
help_text() {
  cat <<'HELP'
Usage: bash scripts/start-agent-manager.sh [up|status|console|banking]

  up      Start/reuse dedicated Colima VM, create/install AMP only if no k3d
          cluster exists, restore kubeconfig, install amctl if needed, verify.
  status  Inspect current Docker/Colima/k3d/AMP state without starting/installing.
  console  Open the Agent Manager Console in your browser.
  banking Start/update Banco Aurora support services & publish the MCP proxy
          (requires existing agent-manager-support/support.env and amctl login).

Optional: bash scripts/start-agent-manager.sh up --with-banking
          Same as 'up', then run the banking support launcher.

Environment overrides (set BEFORE first install only):
  AMP_COLIMA_PROFILE=agent-manager AMP_K3D_CLUSTER=amp-local
  AMP_COLIMA_CPU=8 AMP_COLIMA_MEMORY=16 AMP_COLIMA_DISK=100
  AMP_VERSION=1.0.0

Safety: Never deletes or reconfigures existing Colima profiles, k3d clusters,
        Kubernetes persistent volumes or platform resources. Cannot recover a
        crash-looping cluster automatically; stops and prints diagnostics.
HELP
}

if [[ "${1:-}" == '-h' || "${1:-}" == '--help' ]]; then help_text; exit 0; fi
case "$ACTION" in up|status|console|banking) ;; *) help_text; die "Unknown action: $ACTION" ;; esac
if (( $# > 2 )) || { (( $# == 2 )) && [[ "${2}" != '--with-banking' || "$ACTION" != up ]]; }; then
  help_text; die "Only 'up --with-banking' accepts an extra argument"
fi
WITH_BANKING=false
if [[ "${2:-}" == '--with-banking' ]]; then WITH_BANKING=true; fi

ensure_prereqs() {
  need colima; need docker; need k3d; need kubectl; need curl
  [[ "$(uname -s)" == Darwin ]] || die 'This launcher is for macOS with Colima.'
}

profile_exists() {
  colima list | awk -v p="$AMP_COLIMA_PROFILE" 'NR>1 && $1 == p {found=1} END {exit(!found)}'
}

choose_docker() {
  unset DOCKER_HOST DOCKER_CONTEXT
  local target="colima-${AMP_COLIMA_PROFILE}"
  docker context inspect "$target" >/dev/null 2>&1 || die "Docker context '$target' is unavailable. Check Colima startup."
  docker context use "$target" >/dev/null
  docker info >/dev/null 2>&1 || die "Docker in '$target' is not ready."
  say "Docker context selected: $target"
}

start_colima() {
  if profile_exists; then
    say "Existing Colima profile '$AMP_COLIMA_PROFILE' found. Starting it without changing CPU/memory/disk."
    colima start --profile "$AMP_COLIMA_PROFILE"
  else
    say "Creating Colima '$AMP_COLIMA_PROFILE' (${AMP_COLIMA_CPU} CPU, ${AMP_COLIMA_MEMORY} GiB RAM, ${AMP_COLIMA_DISK} GiB disk)."
    colima start --profile "$AMP_COLIMA_PROFILE" --vm-type=vz --vz-rosetta \
      --network-address --cpu "$AMP_COLIMA_CPU" --memory "$AMP_COLIMA_MEMORY" --disk "$AMP_COLIMA_DISK"
  fi
  choose_docker
}

cluster_exists() {
  k3d cluster list 2>/dev/null | awk -v c="$AMP_K3D_CLUSTER" 'NR>1 && $1 == c {found=1} END {exit(!found)}'
}

merge_kubeconfig() {
  # Ignore inherited custom kubeconfig files; merge only into ~/.kube/config.
  unset KUBECONFIG
  # Does not disable certificate verification. Backup first.
  mkdir -p "${HOME}/.kube"
  if [[ -f "${HOME}/.kube/config" ]]; then
    cp -p "${HOME}/.kube/config" "${HOME}/.kube/config.backup.aurora.$(date +%Y%m%d-%H%M%S).$$"
  fi
  say "Refreshing kubeconfig for '$AMP_K3D_CLUSTER' (backup retained)."
  k3d kubeconfig merge "$AMP_K3D_CLUSTER" --kubeconfig-merge-default --kubeconfig-switch-context >/dev/null
}

kube_ok() {
  kubectl --context "k3d-${AMP_K3D_CLUSTER}" --request-timeout=15s get nodes >/dev/null 2>&1
}

kube_fail_diagnostics() {
  err "Kubernetes API for 'k3d-${AMP_K3D_CLUSTER}' did not become healthy."
  err "Do NOT rerun the installer or delete the cluster: it contains persistent state."
  err "Inspect: docker logs --tail 150 k3d-${AMP_K3D_CLUSTER}-server-0"
  err "and:     k3d cluster list"
  docker logs --tail 55 "k3d-${AMP_K3D_CLUSTER}-server-0" 2>&1 >&2 || true
  exit 1
}

wait_kubernetes() {
  local i
  for ((i=0; i<30; i++)); do
    if kube_ok; then
      kubectl --context "k3d-${AMP_K3D_CLUSTER}" wait --for=condition=Ready node --all --timeout=180s \
        || die 'Kubernetes nodes exist but have not become Ready; inspect kubectl describe nodes.'
      return 0
    fi
    sleep 3
  done
  kube_fail_diagnostics
}

platform_ready() {
  kubectl --context "k3d-${AMP_K3D_CLUSTER}" -n wso2-amp get deployment amp-api amp-console >/dev/null 2>&1 \
    && kubectl --context "k3d-${AMP_K3D_CLUSTER}" -n wso2-amp rollout status deployment/amp-api --timeout=5s >/dev/null 2>&1 \
    && kubectl --context "k3d-${AMP_K3D_CLUSTER}" -n wso2-amp rollout status deployment/amp-console --timeout=5s >/dev/null 2>&1
}

wait_platform() {
  say 'Checking Agent Manager API and Console deployments...'
  kubectl --context "k3d-${AMP_K3D_CLUSTER}" -n wso2-amp rollout status deployment/amp-api --timeout=240s \
    || die 'AMP API did not become ready. Inspect: kubectl -n wso2-amp get pods'
  kubectl --context "k3d-${AMP_K3D_CLUSTER}" -n wso2-amp rollout status deployment/amp-console --timeout=240s \
    || die 'AMP Console did not become ready. Inspect: kubectl -n wso2-amp get pods'
}

install_platform() {
  say "No existing cluster '$AMP_K3D_CLUSTER' found in this Colima profile."
  say "Installing official WSO2 Agent Manager ${AMP_VERSION} quick-start image: $AMP_IMAGE"
  say 'This is a new installation. Keep the installer output secure; it may contain generated identity-provider credentials.'
  local tty_args=(-i)
  if [[ -t 0 && -t 1 ]]; then tty_args=(-it); fi
  # This is the official Quick Start container, executed directly with its
  # bundled install.sh rather than opening the interactive installer shell.
  docker run --rm "${tty_args[@]}" \
    --name aurora-amp-installer \
    -v /var/run/docker.sock:/var/run/docker.sock \
    --network=host \
    --entrypoint /bin/bash \
    "$AMP_IMAGE" -lc './install.sh' \
    || die 'Official WSO2 installer failed. Check the output above; no automatic deletion/retry was attempted.'
}

install_amctl_if_missing() {
  export PATH="$HOME/.local/bin:$PATH"
  if command -v amctl >/dev/null 2>&1; then
    say "amctl is installed: $(amctl version 2>&1 | head -n 1)"
    return
  fi
  say "Installing WSO2 amctl ${AMP_VERSION} using the official installer."
  local tmp
  tmp="$(mktemp)"
  curl -fsSL https://wso2.github.io/agent-manager/install.sh -o "$tmp"
  AMCTL_VERSION="$AMP_VERSION" sh "$tmp" || { rm -f "$tmp"; die 'amctl installation failed'; }
  rm -f "$tmp"
  command -v amctl >/dev/null 2>&1 || die "amctl still not found; add '$HOME/.local/bin' to PATH."
}

show_urls() {
  cat <<EOF2

WSO2 Agent Manager Console: $AMP_CONSOLE_URL
WSO2 Agent Manager API:     $AMP_API_URL
Kubernetes context:        k3d-${AMP_K3D_CLUSTER}
Docker context:            colima-${AMP_COLIMA_PROFILE}

To authenticate the CLI (if needed):
  export PATH="\$HOME/.local/bin:\$PATH"
  amctl login --url "$AMP_API_URL"

Banco Aurora supporting services + MCP publisher (after authentication):
  bash scripts/start-agent-manager.sh banking
EOF2
}

start_banking() {
  local path="$REPO_ROOT/agent-manager-support/start.sh"
  [[ -f "$path" ]] || die "Missing '$path'. Install the Banco Aurora MCP Autostart kit first."
  [[ -f "$REPO_ROOT/agent-manager-support/support.env" ]] \
    || die 'Missing agent-manager-support/support.env. Copy support.env.example and configure it first.'
  command -v amctl >/dev/null 2>&1 || die 'amctl is missing. Run the up command first.'
  amctl context show >/dev/null 2>&1 || die "Log in first: amctl login --url $AMP_API_URL"
  platform_ready || die 'Agent Manager is not ready; run the up command first.'
  say 'Running Banco Aurora supporting-services publisher (separate from AMP installation).'
  (cd "$REPO_ROOT/agent-manager-support" && bash ./start.sh)
}

ensure_prereqs
case "$ACTION" in
  up)
    start_colima
    if cluster_exists; then
      say "Cluster '$AMP_K3D_CLUSTER' already exists. Reusing it; the installer will NOT run."
      k3d cluster start "$AMP_K3D_CLUSTER"
    else
      install_platform
    fi
    cluster_exists || die 'Installer returned, but the k3d cluster was not created.'
    merge_kubeconfig
    wait_kubernetes
    wait_platform
    install_amctl_if_missing
    say 'WSO2 Agent Manager is ready.'
    show_urls
    if [[ "$WITH_BANKING" == true ]]; then start_banking; fi
    ;;
  status)
    say "Colima profile: $AMP_COLIMA_PROFILE"
    colima list
    say 'Docker context:'
    docker context show || true
    say 'k3d clusters visible to the current Docker context:'
    k3d cluster list || true
    say 'Kubernetes AMP deployments:'
    kubectl --request-timeout=10s -n wso2-amp get deployments amp-api amp-console || true
    say 'No state changed.'
    ;;
  console)
    need open
    open "$AMP_CONSOLE_URL"
    ;;
  banking)
    choose_docker
    start_banking
    ;;
esac
