#!/usr/bin/env python3
"""Safe source reconciliation for six Git-built Banco Aurora managed agents.

Read-only by default. Explicit AURORA_AGENT_SOURCE_PUBLISH=1 is needed before
regenerating build folders or pushing source to a remote repository.
Never commits runtime configuration, local secrets or unrelated changes.
"""
import datetime
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile

ROOT = Path(__file__).resolve().parent.parent
SUPPORT = ROOT / 'agent-manager-support'
STATE = SUPPORT / '.source-sync-state.json'
BRANCH = os.getenv('AURORA_GIT_REF', 'main')
REPO = os.getenv('AURORA_GIT_REPO', 'https://github.com/joaokuntzwso2/demo-banking-agent')
APP = Path('deployment/agent-manager/apps')
SOURCES = ('agents', 'banking_common', 'knowledge', 'requirements.txt',
           'deployment/agent-manager/scripts/prepare-apps.py', str(APP))
ROLES = ('account', 'card', 'dispute', 'pix', 'wellness', 'concierge')
ALLOW = {'.py', '.json', '.md', '.txt', '.yaml', '.yml', '.toml'}
TOKEN = re.compile(rb'(?:\bsk-[A-Za-z0-9_-]{20,}\b|\bgh[pousr]_[A-Za-z0-9_]{20,}\b|\bAKIA[0-9A-Z]{16}\b|-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----)')


def fail(msg):
    raise RuntimeError(msg)


def run(args, *, cwd=None, check=True, timeout=90):
    p = subprocess.run(args, cwd=cwd or ROOT, capture_output=True, text=True, timeout=timeout)
    if check and p.returncode:
        fail(f'{args[0]} operation failed: {(p.stderr or p.stdout).strip()[:900]}')
    return p


def git(*args, check=True, timeout=90):
    return run(['git', *args], check=check, timeout=timeout).stdout.strip()


def canonical_remote(url):
    # Match HTTPS and SSH GitHub references without weakening remote identity checks.
    url=url.strip()
    if url.startswith('git@github.com:'):
        url='https://github.com/'+url.split(':',1)[1]
    if url.startswith('ssh://git@github.com/'):
        url='https://github.com/'+url.split('github.com/',1)[1]
    if url.startswith('https://github.com/'):
        return url.lower().removesuffix('.git').rstrip('/')
    return url.rstrip('/')


def remote_sha():
    current=git('remote','get-url','origin')
    if canonical_remote(current)!=canonical_remote(REPO):
        fail(f'origin remote does not match configured AURORA_GIT_REPO. Refusing automated push.\norigin: {current}\nconfigured: {REPO}')
    result=git('ls-remote','origin',f'refs/heads/{BRANCH}')
    if not result:
        fail(f'Remote branch {BRANCH} not found on origin')
    sha=result.split()[0]
    if not re.fullmatch('[0-9a-f]{40}',sha):
        fail('Invalid Git remote SHA')
    return sha


def snapshot_generated():
    """Generate agent bundles in an isolated temporary tree, never in user checkout."""
    tmp=tempfile.TemporaryDirectory(prefix='aurora-generated-')
    path=Path(tmp.name)
    try:
        for name in ('agents','banking_common','knowledge','mcp_server'):
            shutil.copytree(ROOT/name,path/name,ignore=shutil.ignore_patterns(
                '__pycache__','*.pyc','*.pyo','.env','.env.*','*.pem','*.key','*.p12','*.pfx', '.DS_Store', '.pytest_cache', '.mypy_cache', '.ruff_cache','*.log','*.db','*.sqlite*'))
        shutil.copy2(ROOT/'requirements.txt',path/'requirements.txt')
        runner=Path('deployment/agent-manager/scripts/prepare-apps.py')
        (path/runner).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT/runner,path/runner)
        run([sys.executable,str(path/runner)],cwd=path,timeout=90)
        generated=path/APP
        for role in ROLES:
            app = generated / f'aurora-{role}'
            if not (app / 'main.py').is_file():
                fail(f'Generated {role} agent is missing main.py')
            spec_file = app / 'openapi.json'
            if not spec_file.is_file():
                fail(f'Generated {role} agent is missing openapi.json; generator and reconciler contracts disagree')
            try:
                spec = json.loads(spec_file.read_text())
            except (OSError, ValueError) as exc:
                fail(f'Generated {role} openapi.json is unreadable/invalid: {exc}')
            required = {'/health', '/chat'} | ({'/approve', '/workflow/{workflow_id}'} if role == 'concierge' else {'/execute'})
            paths = spec.get('paths') if isinstance(spec, dict) else None
            if (not isinstance(spec, dict) or not str(spec.get('openapi', '')).startswith('3.')
                or not isinstance(spec.get('info'), dict) or not isinstance(paths, dict)
                or not required.issubset(paths)):
                fail(f'Generated {role} OpenAPI is missing a valid 3.x contract or required routes')
        return tmp,generated
    except Exception:
        tmp.cleanup()
        raise


def entries(root):
    if not root.exists():return {}
    found={}
    for p in root.rglob('*'):
        if p.is_symlink():fail(f'Symlink in generated app tree is not allowed: {p}')
        if p.is_file() and '__pycache__' not in p.parts and p.suffix not in ('.pyc','.pyo'):
            found[str(p.relative_to(root))]=p
    return found


def scan_generated(want):
    for name, p in entries(want).items():
        logical=Path(name)
        if logical.suffix.lower() not in ALLOW or any(part.startswith('.') for part in logical.parts):
            fail(f'Generated agent app has non-source or sensitive file: {name}')
        data=p.read_bytes()
        if len(data)>2_000_000 or TOKEN.search(data):
            fail(f'Generated agent app contains oversized file or possible credential: {name}')


def generate_diff(want):
    local=entries(ROOT/APP)
    generated=entries(want)
    names=set(local)|set(generated)
    return sorted(n for n in names if n not in local or n not in generated
                  or local[n].read_bytes()!=generated[n].read_bytes())


def dirty_sources():
    p=run(['git','status','--porcelain=v1','--untracked-files=all','--',*SOURCES])
    return [line for line in p.stdout.splitlines() if line]


def state():
    if not STATE.is_file():return {}
    try:return json.loads(STATE.read_text())
    except (OSError,ValueError):return {}


def write_state(obj):
    STATE.parent.mkdir(parents=True,exist_ok=True)
    STATE.write_text(json.dumps(obj,sort_keys=True,indent=2)+'\n')
    STATE.chmod(0o600)


def maybe_resume_push(local,remote):
    s=state()
    if s.get('commit') != local or s.get('previous') != remote or s.get('branch') != BRANCH:
        return False
    print('[aurora-source] Resuming previously committed source publication (no new commit).',flush=True)
    push_verify(local)
    return True


def push_verify(expected):
    run(['git','push','origin',f'HEAD:refs/heads/{BRANCH}'],timeout=120)
    if remote_sha()!=expected:
        fail('Push reported success, but remote SHA does not match local HEAD')
    print(f'[aurora-source] Remote branch {BRANCH} verified at {expected[:12]}',flush=True)


def backup_apps():
    existing=ROOT/APP
    if not existing.exists():return None
    backup=SUPPORT/'.source-sync-backups'
    backup.mkdir(exist_ok=True,parents=True)
    when=datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    output=backup/f'agent-apps-{when}.tar.gz'
    with tarfile.open(output,'w:gz') as tar:
        tar.add(existing,arcname=str(APP))
    output.chmod(0o600)
    print(f'[aurora-source] Backed up generated agent folders locally: {output.relative_to(ROOT)}',flush=True)
    return output


def staged_files():
    p=run(['git','diff','--cached','--name-only','-z','--diff-filter=ACMR'])
    return [x for x in p.stdout.split('\x00') if x]


def inspect_staged():
    files=staged_files()
    for name in files:
        path=Path(name)
        if not any(name==prefix or name.startswith(prefix+'/') for prefix in SOURCES):
            fail(f'Unexpected staged path: {name}; aborting source-only commit')
        if path.suffix.lower() not in ALLOW or any(part.startswith('.') for part in path.parts):
            fail(f'Unexpected staged source filename: {name}; possible private file')
        obj=git('ls-files','--stage','--',name)
        if not obj.startswith('100644 ') and not obj.startswith('100755 '):
            fail(f'Refusing nonregular Git file: {name}')
        p=subprocess.run(['git','show',f':{name}'],cwd=ROOT,capture_output=True,timeout=20)
        if p.returncode or len(p.stdout)>2_000_000:
            fail(f'Cannot safely inspect staged source {name}')
        if TOKEN.search(p.stdout):
            fail(f'Possible credential in staged source {name}. Nothing will be pushed.')
    run(['git','diff','--cached','--check'])
    return files


def sync_and_push(want, remote):
    if git('diff','--cached','--name-only'):
        fail('Git index already has staged changes. Refusing to include user-staged files in automated commit.')
    current=git('rev-parse','HEAD')
    if current != remote:
        if maybe_resume_push(current,remote): return
        fail('Local HEAD and remote branch differ. Review branch state before publishing source (no force push).')
    # Regenerate ONLY the six managed agent app folders. Leave upstream/external-risk alone.
    backup_apps()
    target=ROOT/APP
    target.mkdir(parents=True,exist_ok=True)
    allowed_names={f'aurora-{role}' for role in ROLES}
    extras=[child.name for child in target.iterdir() if child.name not in allowed_names]
    if extras:
        fail(f'Unexpected content in managed apps directory; refusing destructive regeneration: {extras}')
    for child in target.iterdir():
        if child.is_symlink():fail('Refusing to modify symlinked generated app folder')
        if child.is_dir():shutil.rmtree(child)
        else:child.unlink()
    for child in want.iterdir():
        if not child.is_dir():fail(f'Unexpected non-directory in generated build tree: {child.name}')
        shutil.copytree(child,target/child.name)
    try:
        run(['git','add','-A','--',*SOURCES])
        files=inspect_staged()
    except Exception:
        # The index was checked to be empty before our staging operation.
        # Reset only our scoped paths so a rejected push never leaves staged secrets.
        run(['git','reset','-q','--',*SOURCES],check=False)
        raise
    if not files:
        print('[aurora-source] Git-managed agent inputs already match remote commit.',flush=True)
        return
    print(f'[aurora-source] Publishing {len(files)} reviewed-path source files; unrelated changes remain untouched.',flush=True)
    run(['git','commit','-m','chore(aurora): sync managed agent build inputs'],timeout=120)
    created=git('rev-parse','HEAD')
    write_state({'branch':BRANCH,'previous':remote,'commit':created})
    push_verify(created)


def main():
    if len(sys.argv)>2 or (len(sys.argv)==2 and sys.argv[1] not in ('ensure','check')):
        fail('Usage: sync-managed-agent-source.py [ensure|check]')
    action=sys.argv[1] if len(sys.argv)==2 else 'ensure'
    if Path(git('rev-parse','--show-toplevel')).resolve()!=ROOT.resolve():
        fail('Script must run from this repository checkout')
    branch=git('branch','--show-current')
    if branch != BRANCH:
        fail(f'Current branch {branch!r} differs from required build branch {BRANCH!r}. Check out the correct branch.')
    remote=remote_sha()
    local=git('rev-parse','HEAD')
    with_temp,want=snapshot_generated()
    try:
        scan_generated(want)
        diff=generate_diff(want)
        dirty=dirty_sources()
        if local==remote and not diff and not dirty:
            print(f'[aurora-source] Six generated app bundles match canonical source. Git {BRANCH} at {local[:12]}.',flush=True)
            return
        print('[aurora-source] Agent source requires synchronization:',flush=True)
        if diff:
            print(f'[aurora-source] Generated app differences ({len(diff)}): '+', '.join(diff[:12]),flush=True)
        if dirty:
            for entry in dirty[:20]:print('  '+entry,flush=True)
        if action=='check':
            fail('Read-only source check failed. Rerun with AURORA_AGENT_SOURCE_PUBLISH=1 to opt into source-only Git commit/push.')
        if os.getenv('AURORA_AGENT_SOURCE_PUBLISH')!='1':
            fail('Git-built agents cannot use uncommitted local source. To explicitly allow a source-only commit and push, run:\n'
                 'AURORA_AGENT_SOURCE_PUBLISH=1 ./scripts/start-everything.sh up\n'
                 'The publisher will NOT stage secrets, local config, or unrelated changes.')
        sync_and_push(want,remote)
        local=git('rev-parse','HEAD')
        if remote_sha()!=local or generate_diff(want) or dirty_sources():
            fail('Source sync verification failed; refusing to continue to managed builds')
        print('[aurora-source] Verified published Git source is in sync with the six local bundles.',flush=True)
    finally:
        with_temp.cleanup()


if __name__ == '__main__':
    try:main()
    except Exception as exc:
        print(f'ERROR: {exc}',file=sys.stderr)
        sys.exit(1)
