#!/usr/bin/env bash
# No Docker, Colima, or Kubernetes is needed to run these smoke tests.
set -Eeuo pipefail
S="$(cd "$(dirname "$0")/../.." && pwd)/scripts/start-agent-manager.sh"
T="$(mktemp -d)"
trap 'rm -rf "$T"' EXIT
mkdir -p "$T/bin" "$T/home/.kube"
printf 'original configuration\n' > "$T/home/.kube/config"
cat > "$T/bin/mock" <<'MOCK'
#!/usr/bin/env bash
set -eu
name="${0##*/}"
printf '%s %s\n' "$name" "$*" >> "$MOCK_LOG"
case "$name:$1" in
  uname:*) echo Darwin;;
  colima:list) printf 'PROFILE STATUS ARCH CPUS MEMORY DISK RUNTIME ADDRESS\nagent-manager Running aarch64 8 16GiB 100GiB docker\n';;
  colima:start) exit 0;;
  docker:context) exit 0;;
  docker:info) exit 0;;
  docker:run) touch "$MOCK_CLUSTER"; exit 0;;
  docker:logs) echo 'mock server logs';;
  k3d:cluster)
    if [[ "$2" == list ]]; then
      echo 'NAME SERVERS AGENTS LOADBALANCER'
      [[ ! -f "$MOCK_CLUSTER" ]] || echo 'amp-local 1/1 0/0 true'
    fi
    ;;
  k3d:kubeconfig) echo "$HOME/.kube/config";;
  kubectl:*) exit 0;;
  amctl:version) echo 'amctl version 1.0.0';;
  amctl:context) echo 'context ok';;
  sleep:*) exit 0;;
esac
MOCK
chmod +x "$T/bin/mock"
for n in colima docker k3d kubectl curl amctl uname sleep; do ln -s mock "$T/bin/$n"; done
export PATH="$T/bin:$PATH" HOME="$T/home" MOCK_LOG="$T/log" MOCK_CLUSTER="$T/cluster" AMP_VERSION=1.0.0
: > "$MOCK_LOG"
# Existing cluster: must NOT call installer, and must not delete anything.
touch "$MOCK_CLUSTER"
bash "$S" up > "$T/reuse.out"
grep -q 'already exists. Reusing' "$T/reuse.out"
if grep -Eq '^docker run|^k3d cluster delete|^colima delete' "$MOCK_LOG"; then echo 'FAIL: destructive/install command in reuse'; exit 1; fi
# Fresh profile/cluster, existing Colima profile: call official installer.
rm "$MOCK_CLUSTER"
: > "$MOCK_LOG"
bash "$S" up > "$T/fresh.out"
grep -q '^docker run ' "$MOCK_LOG"
grep -q 'WSO2 Agent Manager is ready' "$T/fresh.out"
grep -q -- '--entrypoint /bin/bash' "$MOCK_LOG"
# Validate kubeconfig backup was performed.
find "$HOME/.kube" -name 'config.backup.aurora.*' | grep -q .
echo 'PASS: existing-cluster reuse, clean first install, kubeconfig backup'
