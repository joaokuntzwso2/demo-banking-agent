#!/usr/bin/env bash
# Explicit destructive reset of ONLY the isolated Banco Aurora Colima profile.
# Followed automatically by start-everything.sh up. Never run via ordinary up.
set -Eeuo pipefail
umask 077
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
[[ $# == 1 && "$1" == --yes ]] || { echo 'Usage: aurora-fresh-reset.sh --yes' >&2; exit 2; }
[[ "$(uname -s)" == Darwin ]] || { echo 'This reset is only for Colima on macOS' >&2; exit 1; }
# Prevent accidentally targeting an arbitrary or shared profile via env variables.
[[ "${AMP_COLIMA_PROFILE:-agent-manager}" == agent-manager && "${AMP_K3D_CLUSTER:-amp-local}" == amp-local ]] \
  || { echo 'Refusing fresh reset: profile/cluster overrides are not the dedicated demo names' >&2; exit 1; }
for bin in colima python3; do command -v "$bin" >/dev/null || { echo "Missing $bin" >&2; exit 1; }; done
cd "$ROOT"
echo '[aurora-fresh] DESTRUCTIVE RESET explicitly requested (--yes).'
echo '[aurora-fresh] Target: Colima profile agent-manager and ALL its images, volumes, Kubernetes/AMP state.'
echo '[aurora-fresh] Other Colima profiles and Git sources will not be touched.'
# Do not terminate unidentified local processes; the existing publisher verifies PID ownership.
if [[ -L agent-manager-support/.mcp-public-tunnel ]]; then
  echo '[aurora-fresh] Refusing a symlinked tunnel state directory.' >&2
  exit 1
fi
if [[ -d agent-manager-support/.mcp-public-tunnel ]]; then
  if [[ -f agent-manager-support/support.env ]]; then
    if ! bash scripts/publish-mcp-secure-tunnel.sh stop; then
      # When Colima was deleted earlier, the watchdog handoff can fail even
      # though there are no live tracked processes. Allow only a provably
      # inactive tunnel and free listener ports; never kill unknown owners.
      tunnel_status="$(bash scripts/publish-mcp-secure-tunnel.sh status)" || exit 1
      if [[ "$tunnel_status" != *'kpf: stopped'* ||
            "$tunnel_status" != *'bridge: stopped'* ||
            "$tunnel_status" != *'tunnel: stopped'* ]]; then
        echo '[aurora-fresh] Tunnel processes are still active; aborting.' >&2
        exit 1
      fi
      python3 - <<'PYPORTS'
import socket
for port in (18101, 18102):
    with socket.socket() as sock:
        try: sock.bind(('127.0.0.1', port))
        except OSError:
            raise SystemExit(f'Port {port} remains occupied; refusing reset')
PYPORTS
      echo '[aurora-fresh] No tunnel processes or port listeners survived; proceeding.'
    fi
  else
    echo '[aurora-fresh] Tunnel state exists without support.env; refusing unknown process cleanup.' >&2
    exit 1
  fi
fi
# Important: Colima delete WITHOUT --data may preserve Docker/k3d volumes.
if colima list 2>/dev/null | awk 'NR>1 && $1=="agent-manager" {found=1} END {exit !found}'; then
  colima delete agent-manager --data --force
fi
# Clear only regenerated demo state. Preserve other unrelated env/credential files.
python3 - "$ROOT" <<'PYRESET'
import pathlib, sys
root=pathlib.Path(sys.argv[1])
support=root/'agent-manager-support'
for name in ('secrets.env','.backend-fingerprint','full-automation-state.json',
             'executive-scenario-report.json'):
    p=support/name
    if p.exists():
        if p.is_symlink() or not p.is_file():
            raise SystemExit('Unsafe reset path (expected regular file): '+str(p))
        p.unlink()
p=support/'.mcp-public-tunnel'
if p.exists():
    if p.is_symlink() or not p.is_dir():
        raise SystemExit('Unsafe tunnel state directory')
    # Constrain deletion to exactly the known repo-owned state directory.
    import shutil
    shutil.rmtree(p)
config=support/'support.env'
if config.exists():
    if config.is_symlink() or not config.is_file():
        raise SystemExit('Unsafe support.env path')
    updates={'AMP_ENVIRONMENT_UUID':'','AURORA_MCP_UPSTREAM_URL':'',
             'PORTAL_ENABLED':'false'}
    lines=[]; seen=set()
    for line in config.read_text().splitlines():
        if '=' in line and not line.lstrip().startswith('#'):
            key=line.split('=',1)[0].strip()
            if key in updates:
                line=f'{key}={updates[key]}';seen.add(key)
        lines.append(line)
    for key,value in updates.items():
        if key not in seen:lines.append(f'{key}={value}')
    config.write_text('\n'.join(lines)+'\n')
    config.chmod(0o600)
print('[aurora-fresh] Demo runtime state cleared; cached environment UUID removed.')
PYRESET
echo '[aurora-fresh] Reset complete; returning to normal up bootstrap.'
