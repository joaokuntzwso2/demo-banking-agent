#!/usr/bin/env python3
"""Guarded, source-only installation of early macOS dependency preflight.

Preview by default. --apply changes only tracked source/test/docs. Never runs
startup, Git operations, installers, Docker/Kubernetes, or credentials logic.
"""
import argparse
from datetime import datetime, timezone
import os
from pathlib import Path
import shutil
import sys
import tempfile

PACKAGE = Path(__file__).resolve().parents[1]
MARK = '# AURORA_V15_MACOS_DEPENDENCY_PREFLIGHT'
ANCHOR = 'export PATH="$HOME/.local/bin:$PATH"\n'
INSERT = (MARK + '\n'
          'command -v python3 >/dev/null 2>&1 \\\n'
          '  || die "Missing prerequisite: python3 (install Python 3.11 or 3.12 before running startup)"\n'
          'python3 scripts/aurora-macos-preflight-v15.py --action "$ACTION" \\\n'
          "  || die 'macOS dependency preflight failed; no startup operations attempted'\n")
NOTE = '''
### v15 — fail-fast macOS toolchain preflight

`./scripts/start-everything.sh up` now runs a **read-only, offline**
prerequisite gate immediately after selecting the local PATH and *before*
source synchronization, Colima, Docker, Kubernetes, Agent Manager, tunnel
rotation, managed builds, or stateful scenario execution. The same gate also
runs for `agents` and `check`. A `status` query gets a lightweight check and
remains available for troubleshooting a broken toolchain.

It requires macOS, the tested host Python **3.11 or 3.12** (both accepted;
managed agents and debug containers may use other interpreters), a functioning
`pip`/`venv`/SSL Python installation, Git, Docker CLI + Compose v2 + Buildx,
Colima, k3d v5+, kubectl, cloudflared, WSO2 **amctl 1.0.0**, curl,
OpenSSL/LibreSSL, lsof and shell/process tools. It checks required repo
entrypoints too. Missing, unusable and incompatible items are reported
**individually with remediation hints**; there are no installations or
credential/network probes performed by preflight. The existing later gates
still validate Docker daemon availability, cluster readiness, OAuth login,
Git revision, registered MCP gateway, agent permissions and S01–S06.

For an offline standalone dependency report (without starting anything):

```bash
python3 scripts/aurora-macos-preflight-v15.py --action up
```

The preflight is intentionally strict: the Mac must have installed tools,
including `amctl`, **before** invoking the one-command startup. It does not
silently install or change tools, bypass role authorization, rotate secrets,
reset PostgreSQL, or rebuild agents.
'''


def transform_start(original):
    if MARK in original:
        if original.count(MARK) != 1 or INSERT not in original:
            raise RuntimeError('Partial or unknown v15 preflight patch; refusing to overwrite')
        return original
    if original.count(ANCHOR) != 1:
        raise RuntimeError('Expected startup PATH anchor not unique; refuse unknown source')
    if 'scripts/aurora-full-reconcile.py' not in original or 'Stage 0/6' not in original:
        raise RuntimeError('Not the reviewed Banco Aurora startup orchestration')
    if 'AURORA_V14_MCP_FORWARD_GUARD' not in original:
        raise RuntimeError('v14 MCP security gates missing; update/verify v14.2 first')
    changed = original.replace(ANCHOR, ANCHOR + INSERT, 1)
    assert changed.index(MARK) < changed.index('need python3')
    assert changed.index(MARK) < changed.index('Stage 0/6')
    return changed


def plan(repo):
    if repo.name != 'demo-banking-agent':
        raise RuntimeError('Expected demo-banking-agent repository root')
    start = repo / 'scripts/start-everything.sh'
    readme = repo / 'README.md'
    if any(not p.is_file() or p.is_symlink() for p in (start, readme)):
        raise RuntimeError('Expected regular startup and README source files')
    updated = transform_start(start.read_text())
    old_readme = readme.read_text()
    if '### v15 — fail-fast macOS toolchain preflight' in old_readme:
        revised_readme = old_readme
    else:
        revised_readme = old_readme.rstrip() + '\n\n' + NOTE
    assets = [
        (start, updated),
        (readme, revised_readme),
        (repo/'scripts/aurora-macos-preflight-v15.py',
         (PACKAGE/'scripts/aurora-macos-preflight-v15.py').read_text()),
        (repo/'scripts/apply-aurora-macos-preflight-v15.py',
         (PACKAGE/'scripts/apply-aurora-macos-preflight-v15.py').read_text()),
        (repo/'tests/test_aurora_macos_preflight_v15.py',
         (PACKAGE/'tests/test_aurora_macos_preflight_v15.py').read_text()),
    ]
    for target, content in assets:
        if target.is_symlink():
            raise RuntimeError('Refusing symlink target ' + str(target.relative_to(repo)))
        if target.exists() and not target.is_file():
            raise RuntimeError('Refusing non-file target ' + str(target.relative_to(repo)))
        if target.exists() and target.read_text() != content and target not in (start, readme):
            raise RuntimeError('Existing new-release file differs: ' + str(target.relative_to(repo)))
    return assets


def install(repo, apply=False):
    repo = repo.resolve()
    assets = plan(repo)  # Validate everything before first write.
    for path, content in assets:
        state = ('UNCHANGED' if path.exists() and path.read_text() == content
                 else 'MODIFY' if path.exists() else 'CREATE')
        print(str(path.relative_to(repo)) + ': ' + state)
    if not apply:
        print('READ-ONLY PREVIEW. No changes to source, Git, network or runtime.')
        return
    changes = [(p, value) for p, value in assets if not p.is_file() or p.read_text() != value]
    if not changes:
        print('ALREADY INSTALLED: no changes required.')
        return
    backup = (repo/'agent-manager-support'/'.source-sync-backups'/
              ('macos-preflight-v15-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')))
    backup.mkdir(mode=0o700, parents=True, exist_ok=False)
    for p, content in changes:
        if p.exists():
            dst = backup/p.relative_to(repo)
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(p, dst)
    # Atomic file updates; preserve execute bit on existing scripts.
    applied = []
    try:
        for p, content in changes:
            p.parent.mkdir(parents=True, exist_ok=True)
            mode = p.stat().st_mode & 0o777 if p.exists() else 0o644
            fd, tmp = tempfile.mkstemp(prefix='.aurora-v15-', dir=p.parent)
            try:
                with os.fdopen(fd, 'w', encoding='utf-8') as handle:
                    handle.write(content)
                os.chmod(tmp, mode)
                os.replace(tmp, p)
            finally:
                if os.path.exists(tmp):
                    os.unlink(tmp)
            applied.append(p)
    except Exception:
        # Restore existing files; remove only newly created targets on failure.
        for p in reversed(applied):
            saved = backup/p.relative_to(repo)
            if saved.exists():
                shutil.copy2(saved, p)
            else:
                p.unlink(missing_ok=True)
        raise
    print('SOURCE INSTALLED. Backup: ' + str(backup))
    print('No application/runtime/credential/network changes were made.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', type=Path, required=True)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    try:
        install(args.repo, args.apply)
    except (OSError, RuntimeError, ValueError, AssertionError) as e:
        print('INSTALL REFUSED: ' + str(e), file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
