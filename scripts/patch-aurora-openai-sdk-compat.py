#!/usr/bin/env python3
"""Guarded, canonical-source-only fix for OpenAI Python SDK >=2.34 empty-key rejection.

Read-only by default.  --apply changes only agents/common.py and
agents/concierge.py; the existing source publisher is responsible for
regenerating six agent bundles and publishing Git changes with explicit consent.
No managed workload, gateway, Secret, credential, or Git remote is changed here.
"""
import argparse
import os
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parent.parent

# The upstream gateway credential remains in the separate API-Key header.
# This non-secret placeholder satisfies OpenAI SDK 2.34+ constructor validation.
OLD_KEY = "options={'api_key':'', 'base_url':url,"
NEW_KEY = "options={'api_key':'gateway-only-placeholder', 'base_url':url,"
HEADER_GUARD = "'default_headers':{header:key, 'Authorization':''}}"
MANAGED_GUARD = "if managed:\n        # Agent Manager's OpenAI-compatible gateway"

OLD_EXCEPTION = ("        if os.getenv('USE_LLM_PROVIDER','false').lower()=='true':\n"
                 "            raise HTTPException(503,'Governed model unavailable or rejected the request; no banking actions initiated') from exc")
NEW_EXCEPTION = ("        if os.getenv('USE_LLM_PROVIDER','false').lower()=='true':\n"
                 "            status = getattr(exc, 'status_code', None)\n"
                 "            status = status if type(status) is int and 100 <= status <= 599 else 'none'\n"
                 "            logging.getLogger('aurora.llm').error(\n"
                 "                'Governed LLM invocation failed: exception=%s HTTP=%s',\n"
                 "                type(exc).__name__, status)\n"
                 "            raise HTTPException(503,'Governed model unavailable or rejected the request; no banking actions initiated') from exc")
OLD_IMPORT = "import os,asyncio,json,httpx,uuid,re\n"
NEW_IMPORT = "import os,asyncio,json,httpx,uuid,re,logging\n"


class PatchError(RuntimeError):
    pass


def patch_common(source):
    if HEADER_GUARD not in source or MANAGED_GUARD not in source:
        raise PatchError('Managed header/branch contract changed; refusing an unsafe substitution')
    if source.count(OLD_KEY) == 1 and NEW_KEY not in source:
        return source.replace(OLD_KEY, NEW_KEY, 1), 'change'
    if source.count(NEW_KEY) == 1 and OLD_KEY not in source:
        return source, 'already'
    raise PatchError('OpenAI SDK initialization differs from reviewed Git baseline')


def patch_concierge(source):
    if source.count(OLD_EXCEPTION) == 1 and NEW_EXCEPTION not in source and source.count(OLD_IMPORT) == 1:
        return (source.replace(OLD_IMPORT, NEW_IMPORT, 1)
                      .replace(OLD_EXCEPTION, NEW_EXCEPTION, 1)), 'change'
    if source.count(NEW_EXCEPTION) == 1 and OLD_EXCEPTION not in source and source.count(NEW_IMPORT) == 1:
        return source, 'already'
    raise PatchError('Concierge error handler differs from reviewed Git baseline')


def is_dirty(rel):
    result = subprocess.run(['git', 'status', '--porcelain=v1', '--', rel],
                            capture_output=True, text=True, cwd=ROOT, timeout=12)
    if result.returncode:
        raise PatchError('Unable to verify Git worktree before changing canonical source')
    return bool(result.stdout.strip())


def atomic_replace(path, original, new):
    if new == original:
        return
    fd, tmp_name = tempfile.mkstemp(prefix='.aurora-sdk-fix-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as handle:
            handle.write(new.encode('utf-8'))
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(tmp_name, path.stat().st_mode & 0o777)
        os.replace(tmp_name, path)
    finally:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)


def run(apply=False):
    changes = []
    for rel, patcher in (('agents/common.py', patch_common),
                         ('agents/concierge.py', patch_concierge)):
        path = ROOT / rel
        if path.is_symlink() or not path.is_file():
            raise PatchError('Required canonical source missing or symlinked: ' + rel)
        original = path.read_text('utf-8')
        replacement, status = patcher(original)
        # Validate every source before changing either one.
        changes.append((rel, path, original, replacement, status))
    print('Review: canonical managed SDK placeholder and safe model-error classification.')
    for rel, _, _, _, status in changes:
        print('  %s: %s' % (rel, 'PATCH REQUIRED' if status == 'change' else 'ALREADY PATCHED'))
    if not apply:
        print('READ-ONLY: pass --apply to edit ONLY these two canonical Python source files.')
        print('No Git publication, rebuild, credential or Kubernetes changes made.')
        return 0
    changed = [row for row in changes if row[-1] == 'change']
    for rel, *_ in changed:
        if is_dirty(rel):
            raise PatchError(rel + ' already has worktree changes; review those before applying')
    for rel, path, original, replacement, status in changed:
        atomic_replace(path, original, replacement)
        print('UPDATED: ' + rel)
    print('SOURCE ONLY: review git diff; then explicitly use your existing source publisher.')
    print('No builds, Git commits, pushes, or deployments were started.')
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true', help='Explicitly modify only the two canonical source files')
    args = parser.parse_args(argv)
    try:
        return run(args.apply)
    except (PatchError, OSError, UnicodeError, subprocess.TimeoutExpired) as exc:
        print('NOT PATCHED: ' + str(exc), file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
