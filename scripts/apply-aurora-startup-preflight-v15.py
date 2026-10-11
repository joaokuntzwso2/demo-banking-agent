#!/usr/bin/env python3
"""Guarded, source-only installer for Banco Aurora v15 macOS/auth preflight.

Read-only preview by default. --apply updates the audited startup entrypoint,
adds one preflight module and its tests, and appends documentation. Refuses
unknown source changes; never accesses GitHub, kubectl, amctl, secrets or VMs.
"""
import argparse
import datetime as dt
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

PKG = Path(__file__).resolve().parents[1]
MARK_DEP = '# AURORA_V15_DEPENDENCY_PREFLIGHT'
MARK_UP = '# AURORA_V15_AMCTL_AUTH_UP'
MARK_CHECK = '# AURORA_V15_AMCTL_AUTH_CHECK'
MARK_AGENTS = '# AURORA_V15_AMCTL_AUTH_AGENTS'
MARK_STAGE3 = '# AURORA_V15_AMCTL_AUTH_STAGE3'
MARK_STAGE5 = '# AURORA_V15_AMCTL_AUTH_STAGE5'
MARKS = (MARK_DEP, MARK_UP, MARK_CHECK, MARK_AGENTS, MARK_STAGE3, MARK_STAGE5)

OLD_LOGIN = (
    '  if ! amctl context show >/dev/null 2>&1; then\n'
    "    log 'Agent Manager OAuth login required; opening official browser login.'\n"
    '    amctl login --url http://api.amp.localhost:8080\n'
    '  fi\n'
)

DOCS = '''
### v15 — macOS dependency preflight and amctl OAuth session recovery

For `up`, `agents`, and `check`, the startup script checks **before other
startup work** that it is running natively on Apple Silicon macOS with Python
3.11/3.12; verifies Git, Docker CLI, Colima, k3d, kubectl,
cloudflared, curl, OpenSSL, `amctl` **1.0.x**, Python pip/venv, essential
macOS utilities and required Banco Aurora source files. Docker Compose and
Buildx are optional for this managed-k3d startup. It lists all detected
missing/unusable dependencies and fails before starting or changing Colima,
Kubernetes, tunnels or managed agents. It does not auto-install dependencies.
The Docker **daemon** and Kubernetes **cluster** are intentionally checked
later because the initial preflight must work when Colima is stopped.

Once Agent Manager has started, the script confirms the local amctl instance
and issues an **authenticated read** of `/orgs`. `amctl context show` alone is
not an authentication test: an expired refresh token can remain stored in an
otherwise healthy-looking context. A missing/expired session initiates a
single official `amctl login --url http://api.amp.localhost:8080` browser
flow in an interactive terminal, and performs the same read afterward.
Unexpected HTTP 403/500/network errors do **not** cause silent relogin,
credential replacement or unsafe API retries. In noninteractive environments
it fails with the supported manual login command; do not bypass authentication
or persist CLI tokens in Git. The authenticated gate also runs before the MCP
tunnel/proxy work and agent reconciliation. All operations during the preflight
are read-only, except for the user's explicitly interactive OAuth login.

Run the preflight without modifying infrastructure:

```bash
python3 scripts/aurora-startup-preflight-v15.py dependencies --action up
# After local Agent Manager is reachable:
python3 scripts/aurora-startup-preflight-v15.py auth --action up
```

**Scope**: The guard prevents the observed stale-token failure at the
beginning of reconciliation. Expiration between two independent CLI calls
remains possible; non-idempotent write calls are never automatically replayed
by this patch. Re-enter the idempotent orchestrator after renewal if a session
expires during a very long running build.
'''


def replace_one(source, old, new, label):
    if source.count(old) != 1:
        raise RuntimeError(f'Startup anchor {label} not unique or not recognized; refuse modification')
    return source.replace(old, new, 1)


def transform_start(source):
    present = [m in source for m in MARKS]
    if all(present):
        # Verify fully formed installed blocks, not only marker presence.
        for marker in MARKS:
            if source.count(marker) != 1:
                raise RuntimeError('Duplicate v15 startup marker; refuse')
        if source.count('scripts/aurora-startup-preflight-v15.py auth') != 5 or \
           source.count('scripts/aurora-startup-preflight-v15.py dependencies') != 1:
            raise RuntimeError('Incomplete/modified v15 startup integration')
        return source
    if any(present):
        raise RuntimeError('Partial v15 startup integration detected; refuse automated patch')
    if 'AURORA_V14_MCP_FORWARD_GUARD' not in source or 'scripts/verify-aurora-tunnel-public.py' not in source:
        raise RuntimeError('Reviewed v14.x/v11 startup security integration not found')
    if OLD_LOGIN not in source:
        raise RuntimeError('Legacy context-only amctl login block differs; refuse to guess')
    source = replace_one(source, 'need python3\nneed kubectl\n',
        "need python3\n"
        "# AURORA_V15_DEPENDENCY_PREFLIGHT — BEFORE any infrastructure mutation\n"
        'if [[ "$ACTION" != status ]]; then\n'
        '  python3 scripts/aurora-startup-preflight-v15.py dependencies --action "$ACTION" \\\n'
        "    || die 'Mac dependency preflight failed; see missing/unsupported tools above'\n"
        'fi\n'
        'need kubectl\n', 'early dependencies')
    source = replace_one(source, OLD_LOGIN,
        '  ' + MARK_UP + '\n'
        '  python3 scripts/aurora-startup-preflight-v15.py auth --action up \\\n'
        "    || die 'Agent Manager login could not be verified before starting bank reconciliation'\n", 'up OAuth')
    source = replace_one(source,
        'if [[ "$ACTION" == check ]]; then\n  need amctl\n',
        'if [[ "$ACTION" == check ]]; then\n  need amctl\n'
        '  ' + MARK_CHECK + '\n'
        '  python3 scripts/aurora-startup-preflight-v15.py auth --action check \\\n'
        "    || die 'Agent Manager login must be verified before scenario checks'\n", 'check OAuth')
    source = replace_one(source,
        'if [[ "$ACTION" == agents ]]; then\n  need git; need cloudflared\n',
        'if [[ "$ACTION" == agents ]]; then\n  need git; need cloudflared\n'
        '  ' + MARK_AGENTS + '\n'
        '  python3 scripts/aurora-startup-preflight-v15.py auth --action agents \\\n'
        "    || die 'Agent Manager login must be verified before agent reconciliation'\n", 'agents OAuth')
    source = replace_one(source,
        "  log 'Stage 3/6: Validate real HTTPS MCP origin; recover expired Quick Tunnels.'\n",
        '  ' + MARK_STAGE3 + '\n'
        '  python3 scripts/aurora-startup-preflight-v15.py auth --action up \\\n'
        "    || die 'Agent Manager session invalid before tunnel/proxy reconciliation'\n"
        "  log 'Stage 3/6: Validate real HTTPS MCP origin; recover expired Quick Tunnels.'\n", 'pre-MCP OAuth')
    source = replace_one(source,
        "log 'Stage 5/6: Create, build, configure and deploy six managed agents.'\n",
        MARK_STAGE5 + '\n'
        'python3 scripts/aurora-startup-preflight-v15.py auth --action "$ACTION" \\\n'
        "  || die 'Agent Manager session invalid before agent reconciliation'\n"
        "log 'Stage 5/6: Create, build, configure and deploy six managed agents.'\n", 'pre-agent OAuth')
    if any(source.count(marker) != 1 for marker in MARKS):
        raise RuntimeError('v15 markers not inserted exactly once')
    return source


def install(repo, apply):
    repo = repo.resolve()
    if repo.name != 'demo-banking-agent':
        raise RuntimeError('Expected repository root demo-banking-agent')
    start = repo / 'scripts' / 'start-everything.sh'
    readme = repo / 'README.md'
    bundled = PKG / 'scripts' / 'aurora-startup-preflight-v15.py'
    testfile = PKG / 'tests' / 'test_aurora_startup_preflight_v15.py'
    targets = [(start, None), (readme, None),
               (repo / 'scripts' / bundled.name, bundled.read_text()),
               (repo / 'scripts' / Path(__file__).name, Path(__file__).read_text()),
               (repo / 'tests' / testfile.name, testfile.read_text())]
    for path, _ in targets:
        if path.is_symlink():
            raise RuntimeError(f'Symlink target forbidden: {path.name}')
    if not start.is_file() or not readme.is_file():
        raise RuntimeError('Expected reviewed startup script and README missing')
    targets[0] = (start, transform_start(start.read_text()))
    original_docs = readme.read_text()
    targets[1] = (readme, original_docs if '### v15 — macOS dependency preflight' in original_docs
                  else original_docs.rstrip() + '\n\n' + DOCS)
    for path, content in targets:
        existing = path.read_text() if path.is_file() else None
        if existing is not None and path not in (start, readme) and existing != content:
            raise RuntimeError(f'v15 target exists with unfamiliar content: {path.name}')
        print(f'{path.relative_to(repo)}: '
              + ('UNCHANGED' if existing == content else ('CREATE' if existing is None else 'MODIFY')))
    if not apply:
        print('READ-ONLY: no Git, secrets, Agent Manager, cluster, or tunnel changes')
        return
    with tempfile.TemporaryDirectory(prefix='aurora-v15-check-') as tmp:
        staged = Path(tmp)/'start-everything.sh'
        staged.write_text(targets[0][1])
        proc = subprocess.run(['bash','-n',str(staged)],capture_output=True,text=True)
        if proc.returncode:
            raise RuntimeError('Generated Bash failed syntax check; no files modified')
    changes = [(p,c) for p,c in targets if not p.is_file() or p.read_text() != c]
    if not changes:
        print('v15 already installed; no source changes')
        return
    dest = Path.home()/'Downloads'/('aurora-v15-source-backup-' + dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%SZ'))
    dest.mkdir(parents=True,exist_ok=False,mode=0o700)
    for path, content in changes:
        if path.is_file():
            # Avoid collisions between README and script names.
            safe = str(path.relative_to(repo)).replace('/','__')
            shutil.copy2(path,dest/safe)
    for path, content in changes:
        mode = (path.stat().st_mode & 0o777) if path.is_file() else 0o644
        path.parent.mkdir(parents=True,exist_ok=True)
        fd,tmp=tempfile.mkstemp(prefix='.aurora-v15-',dir=str(path.parent))
        try:
            with os.fdopen(fd,'w',encoding='utf-8') as f:
                f.write(content)
            os.chmod(tmp,mode)
            os.replace(tmp,path)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)
    print('SOURCE ONLY: installed. Backup location:',dest)
    print('No Git, runtime, environment, credentials or Kubernetes mutations occurred')


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo',type=Path,required=True)
    parser.add_argument('--apply',action='store_true')
    args=parser.parse_args()
    try:
        install(args.repo,args.apply)
    except (RuntimeError,OSError) as exc:
        print('INSTALL REFUSED:',str(exc),file=sys.stderr)
        sys.exit(1)
