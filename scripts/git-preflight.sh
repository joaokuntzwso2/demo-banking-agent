#!/usr/bin/env bash
# Run after git add, before git commit.
set -euo pipefail
if ! git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  echo "ERROR: run in an initialized Git repository." >&2; exit 2
fi
python3 - <<'PY_INNER'
from pathlib import Path
import subprocess, re, sys
staged = subprocess.check_output(['git','diff','--cached','--name-only','--diff-filter=ACMR','-z']).split(b'\0')
files = [s.decode('utf-8') for s in staged if s]
if not files:
    sys.exit('ERROR: no added/changed files are staged.')
problem=[]
scan_pattern = re.compile(rb'-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----|\bghp_[A-Za-z0-9]{36}\b|\bgithub_pat_[A-Za-z0-9_]{40,}\b|\bsk-[A-Za-z0-9_-]{30,}\b|\bAKIA[0-9A-Z]{16}\b')
for filename in files:
    f = Path(filename)
    name = f.name.lower()
    parts = {p.lower() for p in f.parts}
    if (name == '.env' or (name.startswith('.env.') and not name.endswith('.example'))
        or name.endswith(('.pyc','.pyo','.db','.sqlite','.sqlite3','.pem','.p12','.pfx','.jks','.keystore','.tfstate','.log','.zip'))
        or name in ('credentials.json','service-account.json','secrets.yaml','secrets.yml','secret.yaml','secret.yml')
        or parts.intersection({'__pycache__','.pytest_cache','node_modules','.venv','.git','.aws','.kube'})):
        problem.append(f'Forbidden staged path: {filename}')
        continue
    content = subprocess.check_output(['git','show', ':' + filename])
    if len(content) > 5_000_000:
        problem.append(f'Large staged file (>5 MB): {filename} ({len(content)} bytes)')
    if scan_pattern.search(content):
        problem.append(f'Potential credential in staged file: {filename}')
if problem:
    print('FAILED Git preflight:')
    for item in problem: print('  -', item)
    print('Unstage with: git restore --staged <path>')
    sys.exit(1)
print(f'PASS: {len(files)} staged source files; no forbidden paths or known token patterns found.')
print('Reminder: this is a basic check, not a substitute for a complete secret scanner.')
PY_INNER
