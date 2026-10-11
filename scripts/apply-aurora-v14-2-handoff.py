#!/usr/bin/env python3
"""Guarded v14.2 fix: coordinate owned MCP watchdog with existing tunnel publisher.

Read-only preview by default. --apply changes only publisher shell function,
watchdog source, adds focused tests, and appends a root README note. Does not
run network/Kubernetes/Git/Docker or touch state files/credentials/database.
"""
import argparse
import datetime as dt
from pathlib import Path
import re
import shutil
import sys

PACKAGE = Path(__file__).resolve().parents[1]
MARK = '# AURORA_V14_2_OWNED_MCP_HANDOFF'

# Only used within the EXISTING publisher's cleanup function. No port-forward
# is stopped at startup unless publisher already chose its established cleanup.
INJECTION = (
    '  ' + MARK + '\n'
    '  python3 "$(dirname "${BASH_SOURCE[0]}")/aurora-mcp-forward-guard-v14.py" handoff \\\n'
    '    || { echo "ERROR: Safe Aurora MCP watchdog handoff failed; refusing tunnel cleanup." >&2; return 1; }\n'
)


def transform_publisher(original):
    if MARK in original:
        if original.count(MARK) != 1 or original.count(INJECTION) != 1:
            raise RuntimeError('Existing v14.2 handoff is incomplete or has changed')
        return original
    match = list(re.finditer(r'(?m)^(stop_all\s*\(\)\s*\{|function\s+stop_all\s*\{)\s*$', original))
    if len(match) != 1:
        raise RuntimeError('Cannot uniquely identify tunnel publisher stop_all function; no edits made')
    # Avoid affecting other stop/restart orchestration. Inject at the beginning
    # of the existing function; existing controls still apply afterward.
    index = match[0].end()
    if index < len(original) and original[index] == '\n':
        index += 1
    return original[:index] + INJECTION + original[index:]


README = '''
### v14.2 — MCP tunnel cleanup ownership handoff

The original tunnel publisher and the v14 forwarding monitor now coordinate
before **publisher-owned cleanup**. When a stale tunnel needs replacing,
`stop_all()` first asks the monitor to stop and waits for it to exit. It then
releases only a watchdog-tracked `18101` Kubernetes port-forward after checking
the exact process identity. The publisher continues its existing cleanup and
port-conflict checks. Unrelated listeners are never terminated, and a disputed
process identity fails closed. **This changes only the local tunnel lifecycle**;
it does not rotate keys, change AgentID scopes, rebuild agents or reset data.

After installing, run focused/full source tests, then `./scripts/start-everything.sh
up`. Confirm `python3 scripts/aurora-mcp-forward-guard-v14.py status` and the
real gateway proof. A future Kubernetes pod replacement should now be handled
by the watcher, while a deliberate tunnel replacement hands that watcher off
to the publisher first. The complete end-to-end behavior still requires live
acceptance on the intended Mac/cluster; simulated source tests are not proof
of a live recovery.
'''


def install(repo, apply):
    repo = repo.resolve()
    if repo.name != 'demo-banking-agent':
        raise RuntimeError('Expected demo-banking-agent repository root')
    publisher = repo / 'scripts' / 'publish-mcp-secure-tunnel.sh'
    watchdog = repo / 'scripts' / 'aurora-mcp-forward-guard-v14.py'
    readme = repo / 'README.md'
    target_test = repo / 'tests' / 'test_aurora_mcp_handoff_v142.py'
    target_installer = repo / 'scripts' / 'apply-aurora-v14-2-handoff.py'
    source_guard = PACKAGE / 'scripts' / watchdog.name
    source_test = PACKAGE / 'tests' / target_test.name
    for p in (publisher, watchdog, readme, source_guard, source_test):
        if not p.is_file() or p.is_symlink():
            raise RuntimeError(f'Required reviewed source missing/invalid: {p.name}')
    # Compare against exact previously distributed watchdog base; if user
    # changed it independently, refuse rather than clobber their work.
    older_watchdog = PACKAGE / 'baseline' / 'aurora-mcp-forward-guard-v14.py'
    if not older_watchdog.is_file():
        raise RuntimeError('Missing packaged baseline for guarded overwrite')
    existing = watchdog.read_text()
    updated = source_guard.read_text()
    if existing not in (older_watchdog.read_text(), updated):
        raise RuntimeError('Installed v14 watchdog differs from reviewed source; refuse overwrite')
    pub_old = publisher.read_text()
    if '18101' not in pub_old or '18102' not in pub_old or 'kpf.pid' not in pub_old:
        raise RuntimeError('Publisher lifecycle does not match expected owned local ports')
    pub_new = transform_publisher(pub_old)
    old_readme = readme.read_text()
    new_readme = old_readme if '### v14.2 — MCP tunnel cleanup ownership handoff' in old_readme else old_readme.rstrip()+'\n\n'+README
    installer_text = (PACKAGE / 'scripts' / 'apply-aurora-v14-2-handoff.py').read_text()
    if target_installer.exists() and (not target_installer.is_file() or target_installer.is_symlink() or target_installer.read_text() != installer_text):
        raise RuntimeError('Existing v14.2 installer differs; refusing overwrite')
    data = [(publisher, pub_new), (watchdog, updated), (target_installer, installer_text), (target_test, source_test.read_text()), (readme, new_readme)]
    for path, after in data:
        before = path.read_text() if path.is_file() else None
        kind = 'UNCHANGED' if before == after else ('MODIFY' if before is not None else 'CREATE')
        print(f'{path.relative_to(repo)}: {kind}')
    if not apply:
        print('READ-ONLY PREVIEW. No Git, Docker, Kubernetes, tunnels, keys, or bank data changed.')
        return
    stamp = dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    backup_dir = Path.home()/'Downloads'/f'aurora-v14-2-backup-{stamp}'
    if any(path.is_file() and path.read_text() != after for path, after in data):
        backup_dir.mkdir(mode=0o700, parents=True, exist_ok=False)
    for path, after in data:
        if path.is_file() and path.read_text() == after:
            continue
        if path.is_symlink():
            raise RuntimeError(f'Symlink target refused: {path.name}')
        if path.is_file():
            shutil.copy2(path, backup_dir / path.name)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(after)
    print(f'SOURCE ONLY: applied. Backups: {backup_dir}')
    print('No startup or live network actions performed by installer.')


if __name__ == '__main__':
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--repo', type=Path, required=True)
    ap.add_argument('--apply', action='store_true')
    args = ap.parse_args()
    try:
        install(args.repo, args.apply)
    except (RuntimeError, OSError) as exc:
        print(f'REFUSED: {exc}', file=sys.stderr)
        sys.exit(1)
