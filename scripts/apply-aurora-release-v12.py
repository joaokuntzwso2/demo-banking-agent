#!/usr/bin/env python3
"""Guarded, idempotent source-only release installer. No Git or runtime writes."""
from __future__ import annotations
import argparse
import ast
import datetime
import difflib
import os
from pathlib import Path
import re
import shutil
import sys

PAYLOAD = Path(__file__).resolve().parent.parent
ROOT = PAYLOAD.parent
DOCKER_RULES = '''
# Banco Aurora v12: local secrets and transient resources must not enter Docker builds
.git/
.github/
agent-manager-support/
**/.env
**/.env.*
**/*.env
**/secrets.env
**/support.env
**/gateway-keys.env
**/*.pem
**/*.key
**/*.p12
**/*.pfx
**/*credentials.json
**/.kube/
**/.aws/
**/.mcp-public-tunnel/
**/.source-sync-backups/
**/.venv*/
**/__pycache__/
**/*.pyc
**/*.log
**/*.pid
**/*.sqlite
**/*.sqlite3
**/*.db
**/.DS_Store
'''
GIT_RULES = '''
# Banco Aurora v12: local runtime, credentials, and release backups
agent-manager-support/secrets.env
agent-manager-support/support.env
agent-manager-support/gateway-keys.env
agent-manager-support/.source-sync-backups/
agent-manager-support/.venv*/
**/.mcp-public-tunnel/
**/secrets.env
**/gateway-keys.env
**/*.pem
**/*.p12
**/*.pfx
**/*credentials.json
.aurora-release-backups/
'''
# These are *readme* duplicates, not implementation, deployment, or runtime files.
KNOWN_DUPLICATE_READMES = (
    Path('agent-manager-support/README.md'),
    Path('docs/LLM-AUDIT-README.md'),
)
SAFE_ROLE_MARKER = '# Aurora v12 mandatory specialist role guard (fail closed).'


def add_rules(old: str, rules: str) -> str:
    lines = old.splitlines()
    new_rules = [line for line in rules.strip().splitlines() if line and not line.startswith('#')]
    missing = [line for line in new_rules if line not in lines]
    if not missing:
        return old
    return old.rstrip('\n') + '\n\n' + rules.strip() + '\n' if not old.endswith(rules.strip()+'\n') else old


def role_patch(source: str) -> str:
    if SAFE_ROLE_MARKER in source:
        return source
    tree = ast.parse(source)
    functions = [n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == 'mcp_tool']
    if len(functions) != 1:
        raise ValueError('Expected one top-level mcp_tool function; refuse source change')
    fn = functions[0]
    args = fn.args.posonlyargs + fn.args.args
    if len(args) < 1:
        raise ValueError('Cannot verify MCP tool-name parameter')
    name = args[0].arg
    if not name.isidentifier():
        raise ValueError('Unrecognized tool-name parameter')
    if not any(isinstance(x, ast.Name) and x.id == 'TOOLS_BY_ROLE' for x in ast.walk(tree)):
        raise ValueError('TOOLS_BY_ROLE missing: not the expected canonical code')
    if not any((isinstance(x, ast.Import) and any(alias.name == 'os' for alias in x.names)) or (isinstance(x, ast.ImportFrom) and x.module == 'os') or (isinstance(x, ast.Name) and x.id == 'os') for x in ast.walk(tree)):
        raise ValueError('os module missing: not the expected canonical code')
    # Insert after the docstring (if present), before first externally observable tool action.
    body = fn.body
    at = 1 if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) and isinstance(body[0].value.value, str) else 0
    if at >= len(body):
        raise ValueError('Empty mcp_tool implementation')
    line_no = body[at].lineno
    original = source.splitlines(keepends=True)
    indent = re.match(r'^(\s*)', original[line_no-1]).group(1)
    if not indent or '\t' in indent:
        raise ValueError('Unrecognized function indentation')
    guard = [
        f'{indent}{SAFE_ROLE_MARKER}\n',
        f"{indent}_aurora_role = os.getenv('AGENT_ROLE', '').strip()\n",
        f'{indent}_aurora_allowed = TOOLS_BY_ROLE.get(_aurora_role)\n',
        f'{indent}if _aurora_allowed is None or {name} not in _aurora_allowed:\n',
        f"{indent}    raise PermissionError('Agent role missing or MCP tool not authorized')\n",
    ]
    updated = ''.join(original[:line_no-1] + guard + original[line_no-1:])
    ast.parse(updated)
    return updated


def copies():
    payloads = {
        Path('README.md'): (PAYLOAD / 'README.md').read_bytes(),
        Path('scripts/apply-aurora-release-v12.py'): (PAYLOAD/'scripts/apply-aurora-release-v12.py').read_bytes(),
        Path('scripts/aurora-release-audit-v12.py'): (PAYLOAD/'scripts/aurora-release-audit-v12.py').read_bytes(),
        Path('scripts/retire-aurora-local-images-v12.py'): (PAYLOAD/'scripts/retire-aurora-local-images-v12.py').read_bytes(),
        Path('tests/test_aurora_release_v12.py'): (PAYLOAD/'tests/test_aurora_release_v12.py').read_bytes(),
        Path('.github/workflows/aurora-security.yml'): (PAYLOAD/'.github/workflows/aurora-security.yml').read_bytes(),
        Path('.github/dependabot.yml'): (PAYLOAD/'.github/dependabot.yml').read_bytes(),
    }
    for key in ('.dockerignore', '.gitignore'):
        target = ROOT/key
        if not target.is_file():
            raise ValueError(f'Missing repository root {key}; refusing to invent existing configuration')
        text = target.read_text(encoding='utf-8')
        payloads[Path(key)] = add_rules(text, DOCKER_RULES if key == '.dockerignore' else GIT_RULES).encode()
    canonical = ROOT/'agents/common.py'
    if not canonical.is_file():
        raise ValueError('Missing agents/common.py; need full checkout')
    payloads[Path('agents/common.py')] = role_patch(canonical.read_text(encoding='utf-8')).encode()
    reconciler = ROOT/'scripts/aurora-full-reconcile.py'
    if not reconciler.is_file():
        raise ValueError('Missing current v11 reconciler; refusing an unsafe partial update')
    original = reconciler.read_text(encoding='utf-8')
    if "inputs = ['Dockerfile','.dockerignore', 'requirements.txt'," in original:
        fixed = original
    elif "inputs = ['Dockerfile', 'requirements.txt'," in original:
        fixed = original.replace("inputs = ['Dockerfile', 'requirements.txt',",
                                 "inputs = ['Dockerfile','.dockerignore', 'requirements.txt',",1)
    else:
        raise ValueError('Unrecognized backend fingerprint contract; must include .dockerignore before rebuilding')
    ast.parse(fixed)
    payloads[Path('scripts/aurora-full-reconcile.py')] = fixed.encode()
    return payloads


def remove_candidates():
    # Current, root-scoped fix READMEs; do not indiscriminately delete other README sources.
    found = list(ROOT.glob('README-*.md'))
    found += [ROOT/path for path in KNOWN_DUPLICATE_READMES if (ROOT/path).is_file()]
    # Only public fix-note README files are deleted. Preserve all non-README docs.
    return sorted(set(found))


def run(apply: bool):
    if not (ROOT/'.git').exists() or not (ROOT/'scripts/start-everything.sh').is_file():
        raise ValueError('Run only from the real Banco Aurora git checkout with start-everything.sh')
    changed = []
    plans = copies()
    for rel, content in plans.items():
        dest = ROOT/rel
        if dest.exists() and dest.is_symlink():
            raise ValueError(f'Refusing symlink target {rel}')
        if not dest.exists() or dest.read_bytes() != content:
            changed.append((rel, content))
    stale_readmes = remove_candidates()
    if any(path.is_symlink() for path in stale_readmes):
        raise ValueError('Refusing to remove a symlinked README')
    print('Release v12 source-only plan (no Docker, Git publication, deployment or data reset):')
    print(f'  Update/create {len(changed)} file(s)')
    for rel, _ in changed:
        print(f'    {rel}')
    print(f'  Remove {len(stale_readmes)} redundant README file(s)')
    for path in stale_readmes:
        print(f'    {path.relative_to(ROOT)}')
    if not apply:
        print('READ-ONLY. Use --apply after checking plan; backup outside checkout is created.')
        return
    if not changed and not stale_readmes:
        print('ALREADY APPLIED: no file modifications')
        return
    backup_root = Path.home()/'Downloads'/('aurora-release-v12-backup-' + datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ'))
    backup_root.mkdir(mode=0o700, parents=True, exist_ok=False)
    all_target_paths = [ROOT/rel for rel,_ in changed] + stale_readmes
    for path in all_target_paths:
        if path.is_file():
            back = backup_root/path.relative_to(ROOT)
            back.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, back)
    # All payload generation and validation happened before the first mutation.
    for rel, content in changed:
        dest = ROOT/rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_name(dest.name + '.v12-tmp')
        tmp.write_bytes(content)
        if rel.as_posix().startswith('scripts/'):
            tmp.chmod(0o755)
        os.replace(tmp, dest)
    for path in stale_readmes:
        path.unlink()
    print(f'Applied. Backups are in {backup_root} (not in the Git checkout).')
    print('NOTE: after editing agents/common.py, regenerate/publish managed source deliberately; six active Pods are unchanged.')
    print('DO NOT remove live Docker/k3d images until new images and workloads are verified.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--repo', required=True, help='Absolute or relative path to the Banco Aurora Git checkout')
    args = parser.parse_args()
    global ROOT
    ROOT = Path(args.repo).expanduser().resolve()
    try:
        run(args.apply)
    except (ValueError, SyntaxError, OSError) as exc:
        print(f'REFUSED: {type(exc).__name__}: {exc}', file=sys.stderr)
        sys.exit(2)

if __name__ == '__main__':
    main()
