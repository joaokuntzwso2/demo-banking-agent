#!/usr/bin/env bash
# Install the CLEAN Agent Manager/Banco Aurora stack from an already fresh clone.
# Run from anywhere inside the NEW demo-banking-agent checkout.
set -Eeuo pipefail

fail() { printf '\nERROR: %s\n' "$*" >&2; exit 1; }
log() { printf '\n=== %s ===\n' "$*"; }

REPO="$(git rev-parse --show-toplevel 2>/dev/null)" || fail 'Run this from inside the NEW demo-banking-agent clone.'
[[ "$(basename "$REPO")" == demo-banking-agent ]] || fail 'Wrong repository.'
cd "$REPO"

for tool in git colima docker k3d kubectl python3 cloudflared; do
  command -v "$tool" >/dev/null 2>&1 || fail "Missing prerequisite: $tool"
done

[[ -f agent-manager-support/support.env ]] || fail 'Fresh support.env missing; reset/clone procedure was not completed.'
[[ -f tests/test_tunnel_public_stabilization.py ]] || fail 'Tunnel-stability fix is not present in this clone.'
[[ "$(uname -s)" == Darwin ]] || fail 'This launcher is for macOS.'

if [[ "$(uname -m)" == arm64 ]]; then
  arch -x86_64 /usr/bin/true || fail 'Rosetta is not functioning; no VM has been started by this script.'
fi

log 'Confirm startup scripts compile'
bash -n scripts/start-everything.sh
bash -n scripts/start-agent-manager.sh
python3 -m py_compile scripts/aurora-full-reconcile.py scripts/verify-aurora-tunnel-public.py
python3 -m unittest discover -s tests -p test_reconciler.py -v
python3 -m unittest discover -s tests -p test_tunnel_public_stabilization.py -v

log 'Select the dedicated Agent Manager profile (other Colima profiles untouched)'
unset DOCKER_HOST DOCKER_CONTEXT KUBECONFIG AURORA_AGENT_SOURCE_PUBLISH
export PATH="$HOME/.local/bin:$PATH"
export AMP_COLIMA_PROFILE=agent-manager AMP_K3D_CLUSTER=amp-local AMP_VERSION=1.0.0
export AMP_COLIMA_CPU=8 AMP_COLIMA_MEMORY=16 AMP_COLIMA_DISK=100

log 'Create a new Colima VZ/Rosetta VM and install Agent Manager'
bash scripts/start-agent-manager.sh up

log 'Verify the new Colima profile really uses VZ and Rosetta'
python3 - "$HOME/.colima/agent-manager/colima.yaml" <<'PY'
from pathlib import Path
import re, sys
p = Path(sys.argv[1])
if not p.is_file():
    raise SystemExit('Cannot locate dedicated Colima VM config')
s = p.read_text()
for key, val in [('vmType', 'vz'), ('rosetta', 'true')]:
    if not re.search(rf'(?m)^\s*{key}:\s*{val}\s*(?:#.*)?$', s):
        raise SystemExit(f'Unexpected Colima setting: {key} is not {val}. Inspect {p}')
print('COLIMA_VZ_ROSETTA=OK')
PY

kubectl --context k3d-amp-local get nodes

log 'Log in to the newly installed Agent Manager (interactive)'
amctl login --url http://api.amp.localhost:8080

log 'Install banking services, authenticated MCP proxy, and the six managed agents'
./scripts/start-everything.sh up

log 'Read-only platform status'
./scripts/start-everything.sh status
printf '\nSUCCESS: Fresh Banco Aurora installation passed the startup checks.\n'
