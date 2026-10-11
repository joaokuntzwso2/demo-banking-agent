#!/usr/bin/env python3
"""Release hygiene checks; use --history for real Git-history Gitleaks scan."""
from __future__ import annotations
import argparse
from pathlib import Path
import re
import shutil
import subprocess
import sys

ROOT=Path(__file__).resolve().parent.parent
PRIVATE_NAMES={'secrets.env','support.env','gateway-keys.env','.env','id_rsa','id_ed25519'}
KEY_EXTS={'.pem','.p12','.pfx','.key'}
KEY_PATTERNS=(
 ('private-key', re.compile(rb'-----BEGIN (?:OPENSSH |RSA |EC |DSA )?PRIVATE KEY-----')),
 ('github-token', re.compile(rb'gh[pousr]_[A-Za-z0-9_]{20,}')),
 ('aws-access-id',re.compile(rb'AKIA[0-9A-Z]{16}')),
 ('openai-like-secret',re.compile(rb'\bsk-(?:proj-)?[A-Za-z0-9_-]{24,}\b')),
)

def git(*args):
    r=subprocess.run(['git',*args],cwd=ROOT,capture_output=True,check=False)
    if r.returncode:
        raise RuntimeError('git query failed: '+ ' '.join(args))
    return r.stdout

def audit(history=False):
    errors=[]; warnings=[]
    if not (ROOT/'.git').exists():
        errors.append('Not a repository checkout')
        return errors,warnings
    # Include staged and tracked files: --cached and worktree, not just README-based guesses.
    paths=[Path(x.decode('utf-8','replace')) for x in git('ls-files','-z').split(b'\0') if x]
    for path in paths:
        if path.name.lower() in PRIVATE_NAMES or path.suffix.lower() in KEY_EXTS:
            errors.append(f'Tracked private-key/secret filename: {path}')
        full=ROOT/path
        if not full.is_file() or full.stat().st_size>2_000_000:
            continue
        data=full.read_bytes()
        if b'\0' in data:
            continue
        for kind,reg in KEY_PATTERNS:
            for match in reg.finditer(data):
                if kind == 'openai-like-secret' and path.parts[0] == 'tests' and match.group(0) == b'sk-PROVIDED-BY-USER-' + b'FOR-TESTS-ONLY-SECRET':
                    continue  # Explicit synthetic unit-test fixture; not a credential.
                errors.append(f'Potential {kind} content: {path} (value concealed)')
                break
    dockerignore=ROOT/'.dockerignore'
    if not dockerignore.is_file():
        errors.append('Missing .dockerignore')
    else:
        rules=dockerignore.read_text().splitlines()
        for required in ('agent-manager-support/','**/.env','**/secrets.env','**/.mcp-public-tunnel/'):
            if required not in rules:
                errors.append('Docker build exclusion missing: '+required)
    if not (ROOT/'README.md').is_file():
        errors.append('Root README.md missing')
    extras=[p for p in ROOT.glob('README-*.md') if p.is_file()]
    extras += [p for p in (ROOT/'agent-manager-support/README.md',ROOT/'docs/LLM-AUDIT-README.md') if p.is_file()]
    if extras:
        errors.append(f'Duplicate/intermediate README files remain: {len(extras)}')
    for rel in paths:
        if re.search(r'(^|/)requirements[^/]*\.txt$',str(rel)):
            full=ROOT/rel
            if full.is_file():
                lines=[s.strip() for s in full.read_text(errors='replace').splitlines()]
                loose=[l for l in lines if l and not l.startswith(('#','-','git+','https://')) and '==' not in l and '@' not in l]
                if loose:
                    warnings.append(f'Unpinned dependency specs in {rel}; lock against tested runtime before claiming reproducibility')
    if history:
        if not shutil.which('gitleaks'):
            errors.append('Gitleaks not installed: cannot certify full history. Install with brew install gitleaks')
        else:
            r=subprocess.run(['gitleaks','git','--log-opts=--all','--redact','--no-banner','.'],cwd=ROOT,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
            if r.returncode:
                errors.append(f'Gitleaks history scan nonzero (exit {r.returncode}); inspect locally with redacted report')
    return errors,warnings

def main():
    ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('--history',action='store_true');a=ap.parse_args()
    try: errors,warnings=audit(a.history)
    except (OSError,RuntimeError) as exc:
        print('RELEASE AUDIT ERROR:',exc,file=sys.stderr);sys.exit(2)
    print(f'RELEASE_AUDIT: errors={len(errors)}, warnings={len(warnings)}, history_scan={"requested" if a.history else "not-run"}')
    for x in errors: print('ERROR:',x)
    for x in warnings: print('WARN:',x)
    if errors: sys.exit(1)
    print('Current source hygiene checks passed; this does NOT certify Docker layers, remote Git history or deployed workloads.')
if __name__=='__main__':main()
