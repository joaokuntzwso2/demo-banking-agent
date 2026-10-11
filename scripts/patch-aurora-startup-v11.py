#!/usr/bin/env python3
"""Guarded, atomic source-only patch for Banco Aurora existing startup files.

Never changes Git state, Agent Manager, workloads, roles or credentials.
Refuses unfamiliar source shapes, keeps backups, and supports --check/--apply.
"""
from pathlib import Path
import argparse
import os
import subprocess
import sys
import tempfile

ROOT=Path(__file__).resolve().parent.parent
START=ROOT/'scripts'/'start-everything.sh'
RECON=ROOT/'scripts'/'aurora-full-reconcile.py'

TUNNEL_BLOCK="""  log 'Stage 3/6: Verify/reuse or establish authenticated public HTTPS MCP tunnel.'
  # Up is idempotent and self-healing for owned tunnel processes.
  # Explicitly permit a verified Banco Aurora *Quick Tunnel* URL rotation,
  # but never replace an unrelated proxy or disable URL safety validation.
  AURORA_ALLOW_EPHEMERAL_PROXY_UPDATE=true \\
    bash scripts/publish-mcp-secure-tunnel.sh up \\
    || die 'Secure tunnel reconciliation failed; run scripts/publish-mcp-secure-tunnel.sh diagnose'
"""
NEW_TUNNEL="""  log 'Stage 3/6: Validate real HTTPS MCP origin; recover expired Quick Tunnels.'
  # PID liveness does NOT prove Cloudflare registration. The public probe
  # compares the origin's authenticated 401 marker over private + public paths.
  if ! python3 scripts/verify-aurora-tunnel-public.py >/dev/null 2>&1; then
    log 'Public MCP tunnel unavailable/stale; stop only owned tunnel processes.'
    bash scripts/publish-mcp-secure-tunnel.sh stop \\
      || die 'Cannot stop the expired owned tunnel safely; inspect diagnose output'
  fi
  AURORA_ALLOW_EPHEMERAL_PROXY_UPDATE=true \\
    bash scripts/publish-mcp-secure-tunnel.sh up \\
    || die 'Tunnel/upstream reconciliation failed; no agent rebuild requested'
  python3 scripts/verify-aurora-tunnel-public.py \\
    || die 'MCP public HTTPS origin did not pass the authenticated marker test'
"""

BEFORE_CHECK="""if [[ "$ACTION" == check ]]; then
  need amctl
  python3 scripts/aurora-full-reconcile.py check
  exit $?
fi
"""
AFTER_CHECK="""if [[ "$ACTION" == check ]]; then
  need amctl
  python3 scripts/verify-aurora-tunnel-public.py \\
    || die 'MCP Quick Tunnel is stale; run ./scripts/start-everything.sh up to repair it'
  python3 scripts/aurora-full-reconcile.py check
  exit $?
fi
"""

BEFORE_AGENTS="""if [[ "$ACTION" == agents ]]; then
  need git
  log 'Preflight: Verify published agent source and generated build folders.'
  python3 scripts/sync-managed-agent-source.py ensure
fi
"""
AFTER_AGENTS="""if [[ "$ACTION" == agents ]]; then
  need git; need cloudflared
  log 'Preflight: Verify published agent source and generated build folders.'
  python3 scripts/sync-managed-agent-source.py ensure
  log 'Verify/recover MCP public tunnel before reusing specialist deployments.'
  if ! python3 scripts/verify-aurora-tunnel-public.py >/dev/null 2>&1; then
    bash scripts/publish-mcp-secure-tunnel.sh stop \\
      || die 'Cannot stop expired owned MCP tunnel; inspect diagnose output'
  fi
  AURORA_ALLOW_EPHEMERAL_PROXY_UPDATE=true \\
    bash scripts/publish-mcp-secure-tunnel.sh up \\
    || die 'MCP HTTPS tunnel recovery failed; no duplicate agents requested'
  python3 scripts/verify-aurora-tunnel-public.py \\
    || die 'MCP public HTTPS origin not verified'
  python3 scripts/aurora-full-reconcile.py check-proxy
fi
"""

BEFORE_DENIAL="""    # Native role-denial verification is separate from in-app denial: the
    # report above tests business flows but cannot test a deliberately
    # unauthorized AgentID token, which the platform doesn't export.
    if not os.environ.get('AURORA_MCP_AUTHZ_VERIFY_CMD'):
        fail('S01-S06 passed; gateway-level unauthorized AgentID MCP tool-call proof '
             'not configured. Set AURORA_MCP_AUTHZ_VERIFY_CMD to a real negative '
             'OAuth 403 test; never substitute in-app policy assertions.')
    cmd(['bash','-lc',os.environ['AURORA_MCP_AUTHZ_VERIFY_CMD']],timeout=240)
"""
AFTER_DENIAL="""    # A real platform AgentID token is minted from the existing Account Pod's
    # injected Secret-backed identity, then the gateway MUST allow an account
    # balance read and deny a read-only Card tool with HTTP 403. No shell
    # command override or environment-only policy assertion can satisfy this.
    cmd([sys.executable,'scripts/verify-aurora-agentid-denial.py','--run'],timeout=240)
"""


def transform(start, reconciler):
    pairs_start=[(TUNNEL_BLOCK,NEW_TUNNEL),(BEFORE_CHECK,AFTER_CHECK),
                 (BEFORE_AGENTS,AFTER_AGENTS)]
    for old,new in pairs_start:
        if new in start:
            continue
        if start.count(old)!=1:
            raise RuntimeError('Startup file differs from reviewed v9.5 anchor; do not overwrite')
        start=start.replace(old,new,1)
    if AFTER_DENIAL not in reconciler:
        if reconciler.count(BEFORE_DENIAL)!=1:
            raise RuntimeError('Reconciler security gate differs from reviewed v9.5 anchor; no changes made')
        reconciler=reconciler.replace(BEFORE_DENIAL,AFTER_DENIAL,1)
    return start,reconciler


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--apply',action='store_true')
    args=ap.parse_args()
    if not START.is_file() or not RECON.is_file():
        print('NOT VERIFIED: required project files missing',file=sys.stderr);return 1
    original=[START.read_text(),RECON.read_text()]
    try:
        targets=transform(*original)
    except RuntimeError as exc:
        print('NOT VERIFIED: '+str(exc),file=sys.stderr)
        return 1
    changed=[new!=old for old,new in zip(original,targets)]
    for path,different in zip((START,RECON),changed):
        print(str(path.relative_to(ROOT))+(': PATCH REQUIRED' if different else ': ALREADY PATCHED'))
    if not args.apply:
        print('READ-ONLY: use --apply after reviewing docs. No source or infrastructure mutations.')
        return 0
    if not any(changed):
        print('No source changes needed; no deployments or Git writes performed.')
        return 0
    # Check both files before the first write. Reject an unrelated dirty
    # source mutation that might be silently overwritten by this installer.
    for file,different in zip((START,RECON),changed):
        if different:
            original_bytes=file.read_bytes()
            mode=file.stat().st_mode & 0o777
            backup=ROOT/'agent-manager-support'/'.source-sync-backups'/('startup-'+file.name+'.before-v11')
            backup.parent.mkdir(parents=True,exist_ok=True)
            if backup.exists():
                raise RuntimeError('Backup already exists; do not overwrite '+backup.name)
            backup.write_bytes(original_bytes)
            backup.chmod(mode)
    for path,new,different in zip((START,RECON),targets,changed):
        if not different:continue
        mode=path.stat().st_mode & 0o777
        fd,temp=tempfile.mkstemp(dir=path.parent,prefix='.aurora-v11-')
        with os.fdopen(fd,'w') as out:out.write(new)
        os.chmod(temp,mode)
        os.replace(temp,path)
        print('UPDATED: '+str(path.relative_to(ROOT)))
    print('SOURCE ONLY: review diff, run local regression suite; do not publish/rebuild agents.')
    return 0

if __name__=='__main__':
    sys.exit(main())
