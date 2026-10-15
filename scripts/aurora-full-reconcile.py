#!/usr/bin/env python3
"""Banco Aurora: reconcile Agent Manager v1.0.0 managed agents and governance.
No automatic deletion, secret logging, or claim of end-to-end readiness without checks.
"""
import base64
import binascii
import hmac
import json
import getpass
import hashlib
import os
from pathlib import Path
import re
import secrets
import subprocess
import sys
import time
import urllib.parse

ROOT = Path(__file__).resolve().parent.parent
SUPPORT = ROOT / 'agent-manager-support'
STATE = SUPPORT / 'full-automation-state.json'
ORG = os.environ.get('AURORA_AMP_ORG', 'default')
ENV = os.environ.get('AURORA_AMP_ENV', 'default')
PROJECT = os.environ.get('AURORA_AMP_PROJECT', 'banco-aurora')
PROXY = os.environ.get('AURORA_MCP_PROXY', 'aurora-banking')
LLM_PROVIDER = os.environ.get('AURORA_LLM_PROVIDER', 'aurora-openai')
LLM_GATEWAY = os.environ.get('AURORA_LLM_GATEWAY', 'api-platform-default-default')
# All six FastAPI agents mount their HTTP routes at the application root.
AGENT_BASE_PATH = '/'
REF = os.environ.get('AURORA_GIT_REF', 'main')
REPO = os.environ.get('AURORA_GIT_REPO', 'https://github.com/joaokuntzwso2/demo-banking-agent')
BUILD_TIMEOUT = int(os.environ.get('AURORA_BUILD_TIMEOUT', '1200'))
# Wait for an implicit build created by the platform before requesting our own.
BUILD_DISCOVERY_TIMEOUT = int(os.environ.get('AURORA_BUILD_DISCOVERY_TIMEOUT', '30'))
BUILD_DISCOVERY_INTERVAL = int(os.environ.get('AURORA_BUILD_DISCOVERY_INTERVAL', '3'))
BUILD_LOCK_RETRY_DELAY = float(os.environ.get('AURORA_BUILD_LOCK_RETRY_DELAY', '2'))
if BUILD_LOCK_RETRY_DELAY < 0 or BUILD_LOCK_RETRY_DELAY > 30:
    raise ValueError('AURORA_BUILD_LOCK_RETRY_DELAY must be between 0 and 30')
if BUILD_DISCOVERY_TIMEOUT < 0 or BUILD_DISCOVERY_INTERVAL <= 0:
    raise ValueError('Invalid AURORA_BUILD_DISCOVERY_TIMEOUT/INTERVAL')
CORE_SERVICE_URL = 'http://aurora-core.aurora-demo.svc.cluster.local:8100'
ROLES = ('account', 'card', 'dispute', 'pix', 'wellness')
AGENTS = ROLES + ('concierge',)


def fail(message):
    raise RuntimeError(message)


def log(message):
    print(f'[aurora-agents] {message}', flush=True)


def redacted_cli_error(output, args):
    """Surface useful amctl errors without exposing --env-secret arguments.

    The CLI's error may quote the exact flag value or print its command line.
    Remove values before selecting diagnostics and never print full argv.
    """
    sanitized = output
    for index, flag in enumerate(args):
        if flag == '--env-secret' and index + 1 < len(args):
            pair = args[index + 1]
            if '=' in pair:
                key, value = pair.split('=', 1)
                if value:
                    sanitized = sanitized.replace(value, '[REDACTED]')
                sanitized = sanitized.replace(pair, key + '=[REDACTED]')
    # Prefer specific validation messages over the lengthy usage banner.
    lines = [line.strip() for line in sanitized.splitlines() if line.strip()]
    relevant = [line for line in lines if re.search(
        r'(?i)(error|invalid|unknown flag|required|must |missing|cannot|unsupported|expects|openapi|flag provided|usage error)', line)]
    # Drop Cobra usage lines that only repeat the original invocation.
    relevant = [line for line in relevant if not line.lower().startswith('usage:')]
    chosen = relevant[:8] if relevant else lines[:4]
    return (' | '.join(chosen) or 'amctl returned an empty error response')[:1600]



def safe_executive_scenario_error(stdout, stderr):
    """Show the exception tail; redact values from local private config files.

    This is diagnostics only; it does not change scenario pass/fail or retries.
    """
    output = (stdout or '').rstrip() + '\n' + (stderr or '').rstrip()
    for filename in ('secrets.env', 'support.env'):
        path = SUPPORT / filename
        if not path.is_file():
            continue
        for line in path.read_text(errors='replace').splitlines():
            if '=' not in line or line.lstrip().startswith('#'):
                continue
            key, value = line.split('=', 1)
            key, value = key.strip(), value.strip().strip('"').strip("'")
            if (len(value) >= 8 and
                re.search(r'(?:KEY|SECRET|TOKEN|PASSWORD|CREDENTIAL|AUTH)', key, re.I)):
                output = output.replace(value, '[REDACTED:' + key + ']')
    return '\n'.join(output.strip().splitlines()[-75:])[-7000:]


def cmd(args, *, input_text=None, optional=False, hide=False, timeout=120):
    p = subprocess.run(args, text=True, input=input_text, capture_output=True, timeout=timeout,
                       cwd=ROOT)
    if p.returncode and not optional:
        detail = p.stderr.strip() or p.stdout.strip()
        if hide:
            detail = redacted_cli_error(detail, args)
        elif any(str(arg).endswith('/run-executive-scenarios.py') for arg in args):
            # The root exception is at the END of long Python tracebacks.
            # Redact private config values before showing the tail.
            detail = safe_executive_scenario_error(p.stdout, p.stderr)
        else:
            detail = detail[:1000]
        hint = ('; CLI exit 2 indicates a flag/argument validation problem'
                if p.returncode == 2 and args[:2] == ['amctl', 'agent'] else '')
        # Never print the full argument vector, which can include secrets.
        safe_command = ' '.join(args[:3]) if hide else f'{args[0]} {args[1] if len(args)>1 else ""}'
        fail(f'Command failed (exit {p.returncode}): {safe_command}{hint}: {detail}')
    return p


def read_kv(path):
    d = {}
    if path.exists():
        for line in path.read_text().splitlines():
            if not line.strip() or line.lstrip().startswith('#') or '=' not in line:
                continue
            k, v = line.split('=', 1)
            d[k.strip()] = v.strip().strip('"')
    return d


def update_kv(path, updates):
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = path.read_text().splitlines() if path.exists() else []
    previous = {x.split('=',1)[0].strip() for x in lines if '=' in x and not x.startswith('#')}
    out = []
    for line in lines:
        if '=' in line and not line.lstrip().startswith('#'):
            key = line.split('=',1)[0].strip()
            if key in updates:
                line = f'{key}={updates[key]}'
        out.append(line)
    for k,v in updates.items():
        if k not in previous:
            out.append(f'{k}={v}')
    path.write_text('\n'.join(out)+'\n')
    path.chmod(0o600)


def state_get():
    if not STATE.exists():
        return {}
    return json.loads(STATE.read_text())


def state_save(d):
    STATE.write_text(json.dumps(d, indent=2, sort_keys=True)+'\n')
    STATE.chmod(0o600)


def api(path, method='GET', data=None, allow_404=False):
    args = ['amctl','api',path]
    if method != 'GET':
        args.extend(['-X',method,'--input','-'])
    p = cmd(args, input_text=json.dumps(data) if data is not None else None, optional=True)
    if p.returncode:
        detail = (p.stderr+' '+p.stdout).strip()
        if allow_404 and re.search(r'\b404\b|NOT_FOUND|not found',detail,re.I):
            return None
        fail(f'Agent Manager {method} {path}: {detail[:700]}')
    if not p.stdout.strip():
        return {}
    try:
        result=json.loads(p.stdout)
    except json.JSONDecodeError:
        fail(f'Agent Manager {path} returned non-JSON')
    return result.get('data',result) if isinstance(result,dict) else result


def collection(obj, names):
    if isinstance(obj,list):
        return obj
    if isinstance(obj,dict):
        for k in (*names,'data','items','results'):
            if k in obj:
                return collection(obj[k], names)
    return []


def backend_fingerprint():
    """Fingerprint source inputs actually used by the support image/manifests.

    Do not include ephemeral tunnel files, runtime credentials or generated
    portal credentials. They would cause a rebuild on every successful run.
    """
    inputs = ['Dockerfile','.dockerignore', 'requirements.txt',
              'agent-manager-support/k8s/support.yaml',
              'agent-manager-support/k8s/portal.yaml']
    for directory in ('mock_core','mcp_server','banking_common',
                      'demo_portal','agents','knowledge'):
        base = ROOT / directory
        if base.exists():
            inputs.extend(str(x.relative_to(ROOT)) for x in base.rglob('*')
                          if x.is_file() and not x.is_symlink()
                          and '__pycache__' not in x.parts
                          and x.suffix not in ('.pyc','.pyo','.log','.db','.sqlite','.sqlite3'))
    h=hashlib.sha256()
    for name in sorted(set(inputs)):
        path=ROOT/name
        if not path.is_file():
            fail(f'Cannot fingerprint missing backing-service source: {name}')
        h.update(name.encode()+b'\0')
        h.update(hashlib.sha256(path.read_bytes()).digest())
    cfg=read_kv(SUPPORT/'support.env')
    for k in ('AURORA_IMAGE','IMAGE_DELIVERY','KUBE_CONTEXT','KUBE_NAMESPACE','MCP_ALLOWED_HOSTS'):
        h.update(k.encode()+b'='+cfg.get(k,'').encode()+b'\0')
    return h.hexdigest()

def service_secrets():
    return read_kv(SUPPORT / 'secrets.env')


def get_git_commit():
    sha = cmd(['git','rev-parse','HEAD']).stdout.strip()
    if not re.fullmatch('[a-f0-9]{40}',sha):
        fail('Unable to resolve local Git SHA')
    # Managed builds only see a pushed Git ref, not the workstation checkout.
    remote = cmd(['git','ls-remote',REPO,f'refs/heads/{REF}'],timeout=45).stdout.strip()
    if not remote or remote.split()[0] != sha:
        fail('Git HEAD is not published at configured remote branch. Commit/push source, then rerun; refusing to deploy stale code.')
    dirty = cmd(['git','status','--porcelain','--', 'agents','banking_common','knowledge',
                 'deployment/agent-manager/apps']).stdout.strip()
    if dirty:
        fail('Agent sources/app folders have uncommitted changes; run prepare-apps.py and commit/push them first.')
    return sha


def current_amp_environment_uuid():
    # Reuse the publisher's strict parser; never infer a name from a UUID.
    if str(SUPPORT) not in sys.path:
        sys.path.insert(0, str(SUPPORT))
    from publish_proxy import resolve_environment_uuid
    return resolve_environment_uuid(api(f'/orgs/{ORG}/environments'), ENV)


def prepare():
    cfg=SUPPORT/'support.env'
    if not cfg.exists():
        template=SUPPORT/'support.env.example'
        if not template.exists(): fail('Missing agent-manager-support/support.env.example')
        cfg.write_text(template.read_text())
    context=cmd(['kubectl','config','current-context']).stdout.strip()
    if context != 'k3d-amp-local':
        fail(f'Refusing to deploy to unexpected Kubernetes context {context!r} (expected k3d-amp-local)')
    portal_enabled=read_kv(cfg).get('PORTAL_ENABLED','false')
    live_uuid=current_amp_environment_uuid()
    cached_uuid=read_kv(cfg).get('AMP_ENVIRONMENT_UUID','').strip()
    if cached_uuid and cached_uuid != live_uuid:
        log('Stale environment UUID detected after Agent Manager reinstall; refreshing from live control plane.')
    update_kv(cfg, {'DEPLOY_TARGET':'kubernetes','KUBE_CONTEXT':context,
                    'K3D_CLUSTER':'amp-local','IMAGE_DELIVERY':'k3d','AMP_PUBLISH':'true',
                    'AMP_ORG':ORG,'AMP_ENVIRONMENT_HANDLE':ENV,
                    'AMP_ENVIRONMENT_UUID':live_uuid, 'PORTAL_ENABLED':portal_enabled})
    secrets_path=SUPPORT/'secrets.env'
    existing=read_kv(secrets_path)
    required=('SPECIALIST_CALL_KEY','SPECIALIST_EXECUTION_KEY')
    new={k:secrets.token_hex(32) for k in required if not existing.get(k)}
    if new:
        update_kv(secrets_path,new)
        log('Created persistent private specialist credentials (values never printed).')
    log('Configuration ready; existing persistent database and support secrets retained.')


def verify_proxy():
    proxy=api(f'/orgs/{ORG}/mcp-proxies/{PROXY}')
    if proxy.get('id') != PROXY: fail('Incorrect or missing MCP Proxy')
    eps=proxy.get('endpoints') or []
    if len(eps)!=1: fail('Expected exactly one managed MCP endpoint')
    ep=eps[0]
    up=((ep.get('upstream') or {}).get('main') or {})
    upstream=up.get('url','')
    if not upstream.startswith('https://') or not urllib.parse.urlsplit(upstream).hostname:
        fail('MCP Proxy does not have an HTTPS upstream')
    if (up.get('auth') or {}).get('header')!='X-Aurora-Origin-Key':
        fail('MCP upstream origin authentication absent')
    tunnel_state=SUPPORT/'.mcp-public-tunnel'/'url'
    if tunnel_state.is_file():
        expected=tunnel_state.read_text().strip()+'/mcp'
        if upstream!=expected:
            fail('Deployed MCP Proxy upstream does not match the local live HTTPS tunnel. '
                 'Rerun scripts/publish-mcp-secure-tunnel.sh up before deploying agents.')
    expected_env_uuid=current_amp_environment_uuid()
    assignments=ep.get('environments') or []
    if len(assignments) != 1 or assignments[0].get('environmentUuid') != expected_env_uuid:
        fail('MCP Proxy is not assigned to the currently registered Agent Manager environment UUID. '
             'Rerun ./scripts/start-everything.sh up to reconcile the mapping.')
    status=[e.get('deploymentStatus') for e in assignments]
    # Endpoint rotations can trigger an asynchronous gateway reconciliation.
    # Wait for the actual deployment state, never equate HTTP PUT 200 with ready.
    if 'Deployed' not in status:
        for _ in range(24):
            time.sleep(3)
            refreshed=api(f'/orgs/{ORG}/mcp-proxies/{PROXY}')
            fresh_eps=refreshed.get('endpoints') or []
            if len(fresh_eps)!=1:fail('Proxy endpoint count changed during gateway rollout')
            ep=fresh_eps[0]
            refreshed_envs=ep.get('environments') or []
            if len(refreshed_envs)!=1 or refreshed_envs[0].get('environmentUuid')!=expected_env_uuid:
                fail('Proxy environment mapping changed during gateway rollout')
            status=[e.get('deploymentStatus') for e in refreshed_envs]
            current=(((ep.get('upstream') or {}).get('main') or {}).get('url'))
            if current!=upstream:fail('Gateway proxy upstream changed unexpectedly during rollout')
            if 'Deployed' in status:break
        else:fail(f'MCP gateway did not become Deployed after update (statuses={status})')
    tools={t.get('name') for t in ((ep.get('capabilities') or {}).get('tools') or [])}
    scopes=collection(api(f'/orgs/{ORG}/mcp-proxies/{PROXY}/scopes'),('scopes',))
    if len(tools)!=15 or len(scopes)!=15:
        fail(f'Expected 15 MCP tools and scopes; got {len(tools)} tools/{len(scopes)} scopes')
    actual={t for s in scopes for t in s.get('tools',[])}
    if actual != tools or any(len(s.get('tools') or [])!=1 for s in scopes):
        fail('Missing or overly broad MCP tool-to-scope mapping')
    log(f'MCP Proxy Deployed; HTTPS origin auth, {len(tools)} tools and {len(scopes)} one-tool scopes verified.')


def _project_cli_json(result, command):
    """Parse a successful amctl --json response; never mistake errors for absence."""
    if result.returncode:
        fail(f'Cannot inspect {command} (exit {result.returncode}); '
             'project creation is blocked until the read-only query works')
    try:
        value=json.loads(result.stdout)
    except (TypeError, ValueError):
        fail(f'{command} returned non-JSON; refusing to guess whether project exists')
    if not isinstance(value,dict) or (value.get('status') not in (None,'ok')):
        fail(f'{command} returned an unrecognized or error envelope; refusing to create')
    return value.get('data',value)


def _project_inventory():
    """Check complete org-scoped project inventory; never infer absence from a partial page.

    AMP v1.0.0 can incorrectly return HTTP 500/INTERNAL_ERROR for a missing
    project's detail GET. Its authenticated list endpoint reports `total`,
    `offset`, and `projects`, which together can prove an exact name is absent.
    """
    p=cmd(['amctl','project','list','--org',ORG,'--json'],optional=True)
    if p.returncode:
        fail(f'Cannot inspect amctl project list (exit {p.returncode}); project creation blocked')
    try:
        envelope=json.loads(p.stdout)
    except (TypeError, ValueError):
        fail('amctl project list returned non-JSON; refusing project mutation')
    if not isinstance(envelope,dict) or envelope.get('status') not in (None,'ok'):
        fail('amctl project list returned an invalid/error envelope')
    if envelope.get('org',ORG)!=ORG:
        fail(f'amctl project list returned a different organization; expected {ORG}')
    payload=envelope.get('data',envelope)
    if not isinstance(payload,dict) or not isinstance(payload.get('projects'),list):
        fail('Project list lacks data.projects[]; cannot establish inventory')
    projects=payload['projects']
    if any(not isinstance(x,dict) or not isinstance(x.get('name'),str) or not x['name']
           for x in projects):
        fail('Project list contains malformed project names')
    names=[p['name'] for p in projects]
    if len(names)!=len(set(names)):
        fail('Project list contains duplicate project names')
    total=payload.get('total')
    if total is None and isinstance(payload.get('pagination'),dict):
        total=payload['pagination'].get('total')
    offset=payload.get('offset',0)
    if isinstance(payload.get('pagination'),dict):
        offset=payload['pagination'].get('offset',offset)
    if type(total) is not int or total<0 or type(offset) is not int or offset!=0:
        fail('Project inventory lacks valid total/offset; refusing to infer absence')
    if total!=len(projects):
        fail(f'Project inventory incomplete ({len(projects)}/{total}); '
             'refusing project mutations or unverified reuse')
    return PROJECT in names


def _project_error_response(result):
    """Return stable amctl error code and safe status hints, never raw CLI output.

    amctl v1.0 uses exit 1 for multiple kinds of failures. Only explicit
    not-found responses can establish absence. Authentication and network
    failures are not proof that creating a project is safe.
    """
    parts = [result.stdout or '', result.stderr or '']
    code = ''
    message = ''
    for part in parts:
        try:
            payload = json.loads(part.strip())
        except (ValueError, TypeError):
            continue
        if not isinstance(payload, dict):
            continue
        error = payload.get('error')
        if isinstance(error, dict):
            code = str(error.get('code') or '')
            message = str(error.get('message') or '')
            break
        if payload.get('status') == 'error' or payload.get('code'):
            code = str(payload.get('code') or '')
            message = str(payload.get('message') or payload.get('reason') or '')
            break
    if not message:
        # CLI output can be plain text instead of JSON. We only extract stable
        # error classes; this text must never be printed as it could be verbose.
        message = '\n'.join(parts)
    return code, message


def _explicit_project_not_found(result):
    code, message = _project_error_response(result)
    normalized = re.sub(r'[^A-Z0-9]+', '_', code.upper()).strip('_')
    if normalized in ('NOT_FOUND', 'PROJECT_NOT_FOUND', 'RESOURCE_NOT_FOUND',
                      'HTTP_404', '404'):
        return True
    # Require an unmistakable HTTP status or a project-specific not-found.
    if re.search(r'(?i)\b(?:HTTP(?:/\S+)?\s*|status(?:\s+code)?\s*[=:]?\s*)404\b', message):
        return True
    if re.search(r'(?i)\bproject\b.{0,160}\b(?:not[ _-]?found|does not exist)\b', message):
        return True
    return False


def _known_project_payload(payload):
    """Validate a project GET against the exact requested slug and org."""
    if not isinstance(payload, dict):
        fail('Project detail returned a non-object; refusing project mutation')
    if payload.get('status') == 'error' or payload.get('code') in ('ERROR', 'BAD_REQUEST'):
        fail('Project detail returned an error object; refusing project mutation')
    payload = payload.get('data', payload)
    if isinstance(payload, dict) and isinstance(payload.get('project'), dict):
        payload = payload['project']
    if not isinstance(payload, dict) or payload.get('name') != PROJECT:
        fail(f'Project detail did not confirm exact name {PROJECT!r}; refusing creation')
    return True


def _project_detail_exists():
    """Use two independent read paths; create only after explicit 404.

    Some v1.0.0 amctl project get errors are not classified by shell exit code.
    The documented low-level `amctl api` command is an authenticated GET of
    the exact org/project path and provides a useful independent result.
    """
    p=cmd(['amctl','project','get',PROJECT,'--org',ORG,'--json'],optional=True)
    if p.returncode == 0:
        payload=_project_cli_json(p,'amctl project get')
        return _known_project_payload(payload)
    if _explicit_project_not_found(p):
        log('Project detail explicitly returned not-found; checking direct API before creation.')
    else:
        code, _ = _project_error_response(p)
        log(f'amctl project get failed (exit {p.returncode}, code={code or "unclassified"}); '
            'verifying exact project through authenticated API.')
    path=f'/orgs/{ORG}/projects/{PROJECT}'
    result=cmd(['amctl','api',path],optional=True)
    if result.returncode:
        if _explicit_project_not_found(result):
            log(f'Exact project API confirms {PROJECT!r} does not exist.')
            return False
        code, message=_project_error_response(result)
        status_hint='permission/authentication' if re.search(
            r'(?i)\b(401|403|unauthorized|forbidden|token|permission)\b', code+' '+message
        ) else 'server/transport or unknown'
        fail(f'Project lookup could not establish existence: amctl project get exit {p.returncode}; '
             f'direct API exit {result.returncode}, code={code or "unclassified"} '
             f'({status_hint}). Refusing project creation. '
             f'Run: amctl project list --org {ORG} --json and '
             f'amctl api {path}')
    try:
        value=json.loads(result.stdout)
    except (TypeError, ValueError):
        fail('Project direct API returned non-JSON; refusing project creation')
    return _known_project_payload(value)


def ensure_project():
    """Safely create/reuse project from two independently fetched complete lists.

    Do NOT require a GET on a non-existent project: AMP v1.0.0 incorrectly
    returns HTTP 500/INTERNAL_ERROR for that case. A successful and complete
    authenticated org-scoped project listing is authoritative; double-check
    absence immediately before creation to narrow the reconciliation race.
    """
    if _project_inventory():
        log(f'Project exists: {PROJECT} (verified by complete inventory)')
        return
    log(f'{PROJECT}: absent from complete organization project inventory; '
        'rechecking before creation.')
    if _project_inventory():
        log(f'Project exists: {PROJECT} (appeared during recheck; no create needed)')
        return
    args=['amctl','project','create',PROJECT,'--org',ORG,
          '--display-name','Banco Aurora Banking Agents',
          '--description','Six synthetic governed banking agents']
    p=cmd(args,optional=True)
    if p.returncode:
        detail=(p.stderr or '')+'\n'+(p.stdout or '')
        if not re.search(r'(?i)(\b409\b|\bconflict\b)',detail):
            fail(f'Project create failed (exit {p.returncode}); '
                 'no retry or second creation attempted')
        # Creation raced another writer. Only accept when the exact project is
        # now present in a complete authenticated org inventory.
        if not _project_inventory():
            fail('Project creation returned 409, but inventory does not confirm '
                 'the exact project; no second create requested')
        log(f'Project exists: {PROJECT} (concurrent creation verified)')
        return
    # Successful create may become visible asynchronously. Do not call GET on
    # an absent object or send a second create while waiting for list updates.
    for attempt in range(10):
        if _project_inventory():
            log(f'Created project {PROJECT} (verified by complete inventory)')
            return
        if attempt<9:
            time.sleep(2)
    fail(f'Project creation returned success but {PROJECT!r} did not appear in '
         'a complete project inventory; no duplicate creation attempted')


def agent_config(role, creds):
    common = {'WSO2_MODE':'true','AGENT_VERSION':'executive-v4'}
    secret={}
    if role=='concierge':
        common.update({'AGENT_ROLE':'concierge',
                       'BANK_CORE_URL':CORE_SERVICE_URL})
        for k in ('BANK_CORE_READ_KEY','BANK_APPROVAL_KEY','CONCIERGE_CALL_KEY',
                  'CONCIERGE_APPROVAL_KEY','SPECIALIST_CALL_KEY','SPECIALIST_EXECUTION_KEY'):
            secret[k]=creds.get(k,'')
        for specialist in ROLES:
            k='AGENT_'+specialist.upper()+'_API_KEY'
            secret[k]=creds.get(k,'')
    else:
        common.update({'AGENT_ROLE':role,'MCP_AUTH_MODE':'agentid',
                       'USE_LLM_PROVIDER':'true','REQUIRE_GOVERNED_LLM':'true'})
        secret['SPECIALIST_CALL_KEY']=creds.get('SPECIALIST_CALL_KEY','')
        if role in ('card','dispute','pix'):
            secret['SPECIALIST_EXECUTION_KEY']=creds.get('SPECIALIST_EXECUTION_KEY','')
    if role=='concierge':
        common['USE_LLM_PROVIDER']='true'
        common['REQUIRE_GOVERNED_LLM']='true'
    if any(not v for v in secret.values()):
        fail('Supporting-services secrets are missing. Stage 2 must complete before creating agents.')
    return common, secret


def agent_json(name):
    p=cmd(['amctl','agent','get',name,'--project',PROJECT,'--org',ORG,'--json'],optional=True)
    if p.returncode:
        return None
    try:
        obj=json.loads(p.stdout)
        if obj.get('status')=='error':return None
        return obj.get('data',obj)
    except ValueError:
        fail('Agent Manager CLI returned non-JSON agent state')


def verify_agent_openapi(name):
    """Fail before making an Agent Manager API mutation if build input is absent."""
    root = ROOT / 'deployment' / 'agent-manager' / 'apps' / name
    spec = root / 'openapi.json'
    if not (root / 'main.py').is_file() or not spec.is_file():
        fail(f'{name}: published build folder must contain main.py and openapi.json; '
             'run scripts/sync-managed-agent-source.py check')
    try:
        obj = json.loads(spec.read_text())
    except (OSError, ValueError) as exc:
        fail(f'{name}: openapi.json is not valid JSON: {exc}')
    if (not isinstance(obj,dict) or not str(obj.get('openapi','')).startswith('3.')
        or not isinstance(obj.get('info'),dict)
        or not isinstance(obj.get('paths'),dict) or not obj['paths']):
        fail(f'{name}: openapi.json is not a valid OpenAPI 3 document with paths')
    return spec


def check_create_cli_flags():
    """Read the installed CLI's actual flags once, before secrets are passed."""
    p=cmd(['amctl','agent','create','--help'])
    for required in ('--llm-provider','--llm-url-env','--llm-api-key-env',
                     '--openapi-spec','--base-path','--repo-path','--env-secret',
                     '--subtype','--build-type','--run-command'):
        if required not in (p.stdout+p.stderr):
            fail(f'Installed amctl agent create does not advertise {required}; '
                 'check `amctl version` and `amctl agent create --help`')



def llm_provider_inventory():
    """Use the documented amctl provider inventory; never retrieve credentials."""
    p = cmd(['amctl', 'llm-provider', 'list', '--org', ORG, '--json'])
    try:
        parsed = json.loads(p.stdout)
    except ValueError:
        fail('LLM provider inventory is not valid JSON; check amctl version')
    data = parsed.get('data', parsed) if isinstance(parsed, dict) else None
    if not isinstance(data, dict) or not isinstance(data.get('providers'), list):
        fail('LLM provider list response is missing data.providers[]; cannot safely reconcile')
    providers = data['providers']
    offset, total = data.get('offset', 0), data.get('total', len(providers))
    if type(offset) is not int or type(total) is not int or offset != 0 or total > len(providers):
        fail(f'LLM provider inventory is incomplete (offset={offset}, total={total}, '
             f'returned={len(providers)}); refusing duplicate provider creation')
    return providers


def provider_template(record):
    value = record.get('template') or record.get('providerTemplate') or record.get('templateId')
    if isinstance(value, dict):
        return value.get('id') or value.get('name') or value.get('handle')
    return value


def ensure_llm_provider():
    """Create once with an interactive OpenAI key, never save or log it.

    The provider belongs to the organization and must be exposed on an
    EGRESS-capable gateway. `amctl` consumes the key from stdin.
    """
    providers = llm_provider_inventory()
    matches = [r for r in providers if isinstance(r, dict) and
               (r.get('id') == LLM_PROVIDER or r.get('handle') == LLM_PROVIDER)]
    if len(matches) > 1:
        fail(f'Ambiguous provider {LLM_PROVIDER!r}; refusing to alter its configuration')
    if matches:
        template = str(provider_template(matches[0]) or '').lower()
        if template != 'openai':
            fail(f'Existing provider {LLM_PROVIDER!r} is not verifiably an OpenAI provider (template={template!r}).')
        status = str(matches[0].get('status') or '').lower()
        if status in ('not deployed','undeployed','inactive','failed','error'):
            fail(f'Existing OpenAI provider {LLM_PROVIDER!r} is {status}; deploy it to an EGRESS/BOTH AI Gateway before continuing')
        log(f'Reusing existing organization-level OpenAI LLM provider {LLM_PROVIDER}.')
        return
    help_output = cmd(['amctl','llm-provider','create','--help']).stdout
    for flag in ('--template','--api-key-stdin','--gateways'):
        if flag not in help_output:
            fail(f'Installed amctl does not support {flag}; cannot safely create OpenAI provider')
    if not sys.stdin.isatty():
        fail('OpenAI provider is missing. Run this in an interactive terminal to enter the OpenAI API key securely.')
    log(f'Creating OpenAI provider {LLM_PROVIDER} for gateway {LLM_GATEWAY}.')
    log('OpenAI API key is used ONCE to create the provider; it is not written to Git or agent secrets.')
    secret = getpass.getpass('OpenAI API key (input hidden): ')
    if not secret or '\n' in secret or '\r' in secret or len(secret) < 20:
        fail('No valid OpenAI API key supplied; provider not created')
    args=['amctl','llm-provider','create',LLM_PROVIDER,'--org',ORG,
          '--display-name','Banco Aurora OpenAI','--description',
          'Shared governed OpenAI provider for six Banco Aurora agents',
          '--template','openai','--version','v1.0',
          '--context','/aurora-openai','--gateways',LLM_GATEWAY,
          '--api-key-stdin']
    try:
        proc=subprocess.run(args, input=secret+'\n', text=True, capture_output=True,
                            timeout=180, cwd=ROOT)
        if proc.returncode:
            detail=(proc.stderr.strip() or proc.stdout.strip()).replace(secret, '[REDACTED]')
            fail(f'OpenAI provider creation failed (exit {proc.returncode}): {detail[:900]}')
    finally:
        secret = ''
    matches=[r for r in llm_provider_inventory() if isinstance(r,dict) and
             (r.get('id') == LLM_PROVIDER or r.get('handle') == LLM_PROVIDER)]
    if len(matches)!=1 or str(provider_template(matches[0]) or '').lower()!='openai':
        fail('CLI returned success, but the registered OpenAI provider could not be verified; do not retry blindly.')
    log(f'OpenAI provider {LLM_PROVIDER} created and verified. No API key retained locally.')


def llm_binding_payload():
    """WSO2 model-configs is for type=llm (NOT MCP tool configurations)."""
    return {'name':LLM_PROVIDER,
            'description':'Governed OpenAI LLM binding for Banco Aurora',
            'type':'llm',
            'envMappings':{ENV:{'providerName':LLM_PROVIDER}},
            'environmentVariables':[{'name':'LLM_PROVIDER_URL','key':'url'},
                                    {'name':'LLM_PROVIDER_KEY','key':'apikey'}]}


def llm_config_detail(path, summary, agent):
    """Never infer an LLM provider from a *configuration name*.

    WSO2's config list is summary-only: names are often template-derived
    ("openai"), whereas providerName is the org provider handle
    ("aurora-openai"). Resolve each model config by its stable UUID.
    """
    uuid = summary.get('uuid') or summary.get('id')
    if not isinstance(uuid, str) or not uuid.strip():
        fail(f'{agent}: LLM model configuration has no UUID; cannot prove its provider')
    detail_path = path + '/' + urllib.parse.quote(uuid, safe='')
    obj = api(detail_path, allow_404=True)
    if not isinstance(obj, dict):
        fail(f'{agent}: LLM configuration detail unavailable for UUID {uuid}; '
             'refusing to replace an unverified binding')
    if obj.get('uuid') and obj['uuid'] != uuid:
        fail(f'{agent}: LLM configuration UUID changed during verification')
    if obj.get('type') != 'llm':
        fail(f'{agent}: expected LLM configuration for UUID {uuid}')
    if obj.get('agentId') not in (None, agent) or obj.get('projectName') not in (None, PROJECT):
        fail(f'{agent}: LLM detail UUID {uuid} belongs to another agent/project; refusing reuse')
    return detail_path, obj


def llm_env_mapping(obj, agent):
    """Read exactly one supported environment mapping from a *detail* GET.

    Installed AMP 1.0.0 (amctl 21b1adb2e) returns:
      envMappings[env].configuration.{providerName,providerUuid,proxyUuid,status}
    Other builds/DTOs expose:
      envModelConfig[env].llmProxy.{providerName,providerUuid,proxyUuid}
    The two response families are *not* interchangeable with request payloads.
    An empty/missing mapping is unverified; never infer it from a config name.
    """
    modern = obj.get('envMappings')
    dto = obj.get('envModelConfig')
    if modern is not None and not isinstance(modern, dict):
        fail(f'{agent}: invalid envMappings detail shape (expected object)')
    if dto is not None and not isinstance(dto, dict):
        fail(f'{agent}: invalid envModelConfig detail shape (expected object)')
    if modern and dto:
        fail(f'{agent}: both envMappings and envModelConfig contain bindings; '
             'cannot safely select an API contract')
    family = 'envMappings' if modern is not None else 'envModelConfig'
    envs = modern if modern is not None else dto
    if not envs:
        return None
    target = ENV.casefold()
    cfg = read_kv(SUPPORT / 'support.env')
    env_uuid = cfg.get('AMP_ENVIRONMENT_UUID', '').strip().casefold()
    recognized = {target}
    if env_uuid:
        recognized.add(env_uuid)
    matches = []
    for key, value in envs.items():
        if not isinstance(key, str) or not isinstance(value, dict):
            fail(f'{agent}: invalid {family} entry; inspect response without overwriting')
        inner_name = value.get('environmentName')
        if inner_name is not None and (not isinstance(inner_name, str) or not inner_name.strip()):
            fail(f'{agent}: invalid environmentName in {family}[{key!r}]')
        key_matches = key.strip().casefold() in recognized
        name_matches = isinstance(inner_name, str) and inner_name.strip().casefold() == target
        if key_matches and inner_name and not name_matches:
            fail(f'{agent}: conflicting {family} key {key!r} and environmentName '
                 f'{inner_name!r}; refusing a potentially wrong environment')
        if key_matches or name_matches:
            matches.append((key, value))
    if len(matches) > 1:
        fail(f'{agent}: multiple {family} mappings refer to {ENV!r}; no automatic selection')
    if not matches:
        fail(f'{agent}: existing LLM configuration has no {ENV!r} environment mapping '
             f'(available {family} keys: {sorted(envs)}); refusing to replace another environment')
    entry = matches[0][1]
    if family == 'envMappings':
        configuration = entry.get('configuration')
        if not isinstance(configuration, dict):
            fail(f'{agent}: {family}[{ENV!r}].configuration missing; provider unverified')
    else:
        configuration = entry.get('llmProxy')
        if not isinstance(configuration, dict):
            fail(f'{agent}: {family}[{ENV!r}].llmProxy missing; provider unverified')
    return configuration


def llm_bound_provider(obj, agent, expected_provider_uuid=None):
    """Validate the resolved provider identity, status and proxy identity."""
    configuration = llm_env_mapping(obj, agent)
    if configuration is None:
        fail(f'{agent}: LLM config UUID {obj.get("uuid")!r} has no environment mapping '
             f'for {ENV!r}; no automatic provider reassignment')
    name = configuration.get('providerName')
    if not isinstance(name, str) or not name.strip():
        fail(f'{agent}: environment {ENV!r} has no verified providerName')
    provider_uuid = configuration.get('providerUuid')
    if provider_uuid is not None and (not isinstance(provider_uuid, str) or not provider_uuid.strip()):
        fail(f'{agent}: invalid providerUuid in LLM mapping')
    if expected_provider_uuid and provider_uuid is not None and provider_uuid != expected_provider_uuid:
        fail(f'{agent}: provider UUID mismatch for {name!r}: mapping {provider_uuid}, '
             f'org registry {expected_provider_uuid}; refusing provider substitution')
    proxy_uuid = configuration.get('proxyUuid')
    if not isinstance(proxy_uuid, str) or not proxy_uuid.strip():
        fail(f'{agent}: provider mapping has no proxyUuid; cannot prove association')
    status = configuration.get('status')
    if status is not None and str(status).casefold() != 'active':
        fail(f'{agent}: LLM provider mapping status is {status!r}, not active')
    return name


def repair_unmapped_openai_config(detail_path, obj, agent):
    """Fail closed on missing mappings; never replace provider on name alone.

    An apparently empty configuration may be intentionally unbound, or a
    partially persisted binding. A PUT without a source-trusted provider identity
    is not safe. Creation of a *new* config is handled by bind_llm instead.
    """
    if llm_env_mapping(obj, agent) is None:
        fail(f'{agent}: existing model config {obj.get("uuid")!r} '
             'has no environment mapping. Inspect it before explicitly attaching '
             'a provider; no guessed PUT or delete will be performed')
    return obj


def verify_llm_env_vars(detail_path, obj, agent, expected_provider_uuid=None):
    """Reconcile only safe runtime variable names; never change provider refs.

    The official v1.0.0 platform-hosted guide supports editing these names
    without rebuilding/redeploying the agent. Only the two known injection keys
    can be edited automatically, with strict read-before/write/read-after.
    """
    expected = {'url': 'LLM_PROVIDER_URL', 'apikey': 'LLM_PROVIDER_KEY'}

    def get_mappings(record):
        values = record.get('environmentVariables')
        if not isinstance(values, list):
            fail(f'{agent}: LLM environmentVariables missing/invalid in detail response')
        by_key = {}
        seen_names = set()
        for v in values:
            if not isinstance(v, dict) or not isinstance(v.get('key'), str) or not isinstance(v.get('name'), str):
                fail(f'{agent}: invalid environmentVariables entry; refusing update')
            key, name = v['key'], v['name']
            if not key.strip() or not name.strip() or key in by_key or name in seen_names:
                fail(f'{agent}: duplicate/blank LLM environment variable key or name; refusing update')
            by_key[key] = name
            seen_names.add(name)
        return by_key

    present = get_mappings(obj)
    if present == expected:
        return
    if set(present) != set(expected):
        fail(f'{agent}: environment-variable keys {sorted(present)} differ from expected '
             f'{sorted(expected)}; custom/absent injection configuration requires review')
    if llm_bound_provider(obj, agent, expected_provider_uuid) != LLM_PROVIDER:
        fail(f'{agent}: provider mismatch; will not update environment-variable names')
    log(f'{agent}: safely updating two LLM injection variable names on verified '
        f'configuration UUID {obj.get("uuid")}; provider mapping will not be changed.')
    api(detail_path, 'PUT', {'environmentVariables':
        llm_binding_payload()['environmentVariables']})
    reread = api(detail_path, allow_404=True)
    if not isinstance(reread, dict) or reread.get('uuid') != obj.get('uuid') or reread.get('type') != 'llm':
        fail(f'{agent}: unable to verify LLM configuration identity after PUT')
    if llm_bound_provider(reread, agent, expected_provider_uuid) != LLM_PROVIDER:
        fail(f'{agent}: provider association changed unexpectedly after variable update')
    if get_mappings(reread) != expected:
        fail(f'{agent}: environment-variable names did not persist after PUT')
    log(f'{agent}: LLM injection variable names verified by independent GET.')


def llm_config_summaries(obj, agent):
    """Reject incomplete paginated inventories; never adopt the first row."""
    if not isinstance(obj, dict) or not isinstance(obj.get('configs'), list):
        fail(f'{agent}: model-config collection lacks configs[]; cannot reconcile')
    items = obj['configs']
    pagination = obj.get('pagination', {})
    if pagination is not None:
        if not isinstance(pagination, dict):
            fail(f'{agent}: invalid model-config pagination')
        offset = pagination.get('offset', 0)
        count = pagination.get('count', len(items))
        if type(offset) is not int or type(count) is not int or offset != 0 or count > len(items):
            fail(f'{agent}: incomplete model-config page ({len(items)} returned, '
                 f'count={count}, offset={offset}); refusing duplicate creation')
    for row in items:
        if not isinstance(row, dict) or not isinstance(row.get('uuid'), str) or not row['uuid']:
            fail(f'{agent}: model-config collection has a row without UUID')
    if len({x['uuid'] for x in items}) != len(items):
        fail(f'{agent}: duplicate model-config UUID in response')
    return [r for r in items if r.get('type') == 'llm']


def expected_llm_provider_uuid(agent):
    """Resolve stable provider identity from organization registry, not display name."""
    matches = [r for r in llm_provider_inventory() if isinstance(r, dict) and r.get('id') == LLM_PROVIDER]
    if len(matches) != 1:
        fail(f'{agent}: expected exactly one organization provider with id={LLM_PROVIDER!r}; '
             f'found {len(matches)}')
    if str(provider_template(matches[0]) or '').lower() != 'openai':
        fail(f'{agent}: org provider {LLM_PROVIDER!r} is not OpenAI')
    uuid = matches[0].get('uuid')
    if uuid is not None and (not isinstance(uuid, str) or not uuid.strip()):
        fail(f'{agent}: provider registry contains invalid UUID')
    return uuid


def bind_llm(agent):
    path = f'/orgs/{ORG}/projects/{PROJECT}/agents/{agent}/model-configs'
    provider_uuid = expected_llm_provider_uuid(agent)
    obj = api(path, allow_404=True)
    if obj is None:
        fail(f'Agent Manager has no model-configs endpoint for {agent}')
    llm = llm_config_summaries(obj, agent)
    if not llm:
        # New binding only: no existing config to overwrite or guess.
        api(path, 'POST', llm_binding_payload())
        log(f'{agent}: created new organization OpenAI LLM binding.')
        llm = llm_config_summaries(api(path), agent)
    if len(llm) != 1:
        fail(f'{agent}: expected exactly one LLM configuration, found {len(llm)}; '
             'refusing to select or replace another config')
    detail_path, detail = llm_config_detail(path, llm[0], agent)
    detail = repair_unmapped_openai_config(detail_path, detail, agent)
    bound = llm_bound_provider(detail, agent, provider_uuid)
    if bound != LLM_PROVIDER:
        fail(f'{agent}: existing LLM config UUID {detail.get("uuid")} references '
             f'provider {bound!r}, expected {LLM_PROVIDER!r}; no automatic replacement')
    verify_llm_env_vars(detail_path, detail, agent, provider_uuid)
    log(f'{agent}: reused verified {LLM_PROVIDER} provider and {ENV} environment '
        f'(config UUID {detail.get("uuid")}; display name {detail.get("name")!r}).')


def ensure_agent(name, role, sha, creds):
    existing=agent_json(name)
    if existing is not None:
        log(f'Agent exists: {name}')
        return False
    verify_agent_openapi(name)
    check_create_cli_flags()
    envvars, secret=agent_config(role,creds)
    args=['amctl','agent','create',name,'--org',ORG,'--project',PROJECT,
          '--display-name',f'Aurora {role.title()}',
          '--description',f'Banco Aurora synthetic {role} managed Custom API agent',
          '--subtype','custom-api','--provisioning','internal',
          '--repo-url',REPO,'--repo-branch',REF,
          '--repo-path',f'deployment/agent-manager/apps/{name}',
          '--build-type','buildpack','--language','python',
          '--language-version','3.12','--run-command','python main.py',
          '--port','8000','--base-path',AGENT_BASE_PATH,
          '--openapi-spec','openapi.json',
          '--llm-provider',LLM_PROVIDER,
          '--llm-url-env','LLM_PROVIDER_URL',
          '--llm-api-key-env','LLM_PROVIDER_KEY']
    for k,v in envvars.items():args.extend(['--env',f'{k}={v}'])
    for k,v in secret.items():args.extend(['--env-secret',f'{k}={v}'])
    # NOTE: amctl passes secrets as process args during creation on macOS.
    # Their values are not logged here, but could briefly be visible to local
    # users with process-inspection rights. Use managed secret references in
    # shared workstation or production environments.
    cmd(args,hide=True,timeout=180)
    log(f'Created agent {name} (secrets concealed).')
    return True


def status_label(obj):
    """Do not mistake the amctl --json envelope status='ok' for build success."""
    if not isinstance(obj,dict):return ''
    for k in ('buildStatus','deploymentStatus','phase','state'):
        value=obj.get(k)
        if isinstance(value,str):return value.lower()
    status=obj.get('status')
    if isinstance(status,str) and status.lower() not in ('ok','success'):
        return status.lower()
    for k in ('build','details','buildInfo'):
        if isinstance(obj.get(k),dict):
            nested=status_label(obj[k])
            if nested:return nested
    return status.lower() if isinstance(status,str) else ''


def latest_build(agent):
    """Read a trusted build inventory, never confuse a failed GET with zero builds."""
    p=cmd(['amctl','agent','build','list',agent,'--project',PROJECT,'--org',ORG,'--json'],optional=True)
    if p.returncode:
        fail(f'{agent}: cannot read Agent Manager build inventory (exit {p.returncode}); '
             'refusing an unverified duplicate build')
    try:
        parsed=json.loads(p.stdout)
    except ValueError:
        fail(f'Non-JSON build inventory for {agent}')
    data=parsed.get('data',parsed) if isinstance(parsed,dict) else parsed
    if isinstance(data,dict):
        builds=data.get('builds')
        if not isinstance(builds,list):
            fail(f'{agent}: build list lacks data.builds[]; refusing to assume none exist')
        total=data.get('total',len(builds))
        offset=data.get('offset',0)
        if type(total) is not int or type(offset) is not int or offset!=0 or total>len(builds):
            fail(f'{agent}: build list pagination incomplete (total={total}, '
                 f'offset={offset}, received={len(builds)})')
    elif isinstance(data,list):
        builds=data
    else:
        fail(f'{agent}: unexpected build inventory shape')
    if not builds:
        return None
    if any(not isinstance(b,dict) for b in builds):
        fail(f'{agent}: invalid build entry returned by Agent Manager')
    return sorted(builds,key=lambda b:b.get('startedAt','') or b.get('createdAt',''))[-1]


def build_commit_sha(build):
    """Return an *explicit* 40-character commit if the build inventory exposes it.

    Never infer a SHA from a build name, createdAt, branch, or a local state
    file. Ambiguous or inconsistent commit fields are untrusted.
    """
    if not isinstance(build, dict):
        return None
    fields = ('commit', 'commitSha', 'commitSHA', 'commitHash', 'gitCommit',
              'gitSha', 'sourceCommit')
    candidates = []
    for item in (build, build.get('source'), build.get('git'), build.get('build')):
        if isinstance(item, dict):
            candidates += [item[k] for k in fields if isinstance(item.get(k), str)
                           and re.fullmatch(r'[a-fA-F0-9]{40}', item[k])]
    values = {x.lower() for x in candidates}
    return next(iter(values)) if len(values) == 1 else None



def verified_build_revision(build, agent, desired_sha):
    """Resolve the deployed AMP 1.0.0 buildParameters.commitId contract.

    The server returns an abbreviated eight-character commitId in build-list
    responses. Trust it only if the source coordinates match this *agent* and
    Git resolves the abbreviation unambiguously to the expected published HEAD.
    Full-length commitSha/commitHash from older responses remain supported.
    Return None if no commit can be established; return an explicit mismatched
    revision for a definitively different build.
    """
    if not isinstance(build, dict):
        return None
    expected_sha = desired_sha.lower()
    if not re.fullmatch(r'[0-9a-f]{40}', expected_sha):
        fail(f'{agent}: expected local Git revision is not a full SHA-1')
    full_fields = ('commit', 'commitSha', 'commitSHA', 'commitHash', 'gitCommit',
                   'gitSha', 'sourceCommit')
    reported = []
    for entry in (build, build.get('source'), build.get('git'), build.get('build')):
        if not isinstance(entry, dict):
            continue
        for field in full_fields:
            value = entry.get(field)
            if isinstance(value, str) and re.fullmatch(r'[0-9a-fA-F]{40}', value):
                reported.append(value.lower())
    if len(set(reported)) > 1:
        fail(f'{agent}: build inventory provides conflicting full Git revisions')
    full_sha = reported[0] if reported else None

    params = build.get('buildParameters')
    if params is None:
        return full_sha
    if not isinstance(params, dict):
        fail(f'{agent}: buildParameters is not an object; build identity unverified')
    short = params.get('commitId')
    if short is None:
        return full_sha
    if not isinstance(short, str) or not re.fullmatch(r'[0-9a-fA-F]{8,40}', short):
        fail(f'{agent}: invalid buildParameters.commitId; refusing ambiguous build identity')
    short = short.lower()
    # The v1.0.0 backend includes these immutable source coordinates in the
    # build record. They must match our intended image input, not just the SHA.
    app_path = f'/deployment/agent-manager/apps/{agent}'
    observed = (params.get('repoUrl'), params.get('branch'), params.get('appPath'))
    expected = (REPO, REF, app_path)
    def normalized_repo(url):
        if not isinstance(url, str):
            return None
        return url.rstrip('/').removesuffix('.git')
    if (normalized_repo(observed[0]) != normalized_repo(expected[0]) or
            observed[1:] != expected[1:]):
        fail(f'{agent}: managed build source coordinates do not match '
             'the desired repository, branch, and agent path; refusing reuse')
    if full_sha and not full_sha.startswith(short):
        fail(f'{agent}: inconsistent buildParameters.commitId and full build SHA')
    if not expected_sha.startswith(short):
        # A different prefix definitively identifies another revision. Existing
        # completed builds may then be followed by a new build as designed.
        return full_sha or short
    if full_sha and full_sha != expected_sha:
        fail(f'{agent}: build revision conflicts with desired Git HEAD')
    if len(short) < 40:
        # Git verifies uniqueness within the source repository. Never equate
        # prefixes on a string comparison alone, even at eight characters.
        try:
            check = cmd(['git', 'rev-parse', '--verify', '--quiet',
                         f'{short}^{{commit}}'], optional=True, timeout=30)
        except subprocess.TimeoutExpired:
            fail(f'{agent}: local Git commit-prefix verification timed out')
        if check.returncode or check.stdout.strip().lower() != expected_sha:
            fail(f'{agent}: shortened Agent Manager commitId {short!r} does '
                 'not resolve uniquely to the intended local Git revision')
    return expected_sha


def resume_existing_build(name, sha, initial):
    """Poll a verified in-flight build without issuing a duplicate create.

    Only resume when the *existing* build explicitly identifies the desired
    Git commit. Pin its build identity where available. A missing/mismatched
    commit and an unexpectedly replaced build fail closed.
    """
    expected_sha = sha.lower()
    first_sha = verified_build_revision(initial, name, sha)
    if first_sha != expected_sha:
        fail(f'{name}: queued/building managed build commit cannot be verified '
             f'against {sha[:12]}; refusing a duplicate build request')
    identity = (initial.get('buildId') or initial.get('buildName') or initial.get('name') or initial.get('uuid'))
    deadline = time.monotonic() + BUILD_TIMEOUT
    current = initial
    last_status = None
    log(f'{name}: resuming monitoring of existing managed build for {sha[:12]} '
        '(no new build request).')
    while time.monotonic() < deadline:
        if not isinstance(current, dict):
            fail(f'{name}: existing build disappeared from inventory; '
                 'refusing to create a duplicate')
        reported_id = (current.get('buildId') or current.get('buildName') or current.get('name') or current.get('uuid'))
        reported_sha = verified_build_revision(current, name, sha)
        if reported_sha is not None and reported_sha != expected_sha:
            fail(f'{name}: existing build inventory switched to a different '
                 'Git commit while monitoring; no new build requested')
        if identity and reported_id != identity:
            fail(f'{name}: build inventory identity changed from {identity!r} '
                 f'to {reported_id!r}; refusing to verify another build')
        if not identity and reported_sha != expected_sha:
            fail(f'{name}: build inventory lost verified commit identity; '
                 'refusing to assume success')
        label = status_label(current)
        if label != last_status:
            log(f'{name}: existing build {identity or sha[:12]} status={label or "unknown"}.')
            last_status = label
        if label in ('success', 'successful', 'succeeded', 'completed'):
            state = state_get()
            state.setdefault('built', {})[name] = sha
            state_save(state)
            log(f'{name}: existing managed build succeeded; reusing {sha[:12]}.')
            return
        if label in ('failed', 'failure', 'error', 'cancelled', 'canceled'):
            fail(f'{name}: existing managed build failed ({label}); '
                 'inspect build logs, no replacement was requested')
        if label not in ('queued', 'pending', 'building', 'running', 'in_progress',
                         'in-progress', 'starting', 'submitted', 'initializing'):
            fail(f'{name}: unrecognized managed build status {label!r}; '
                 'refusing a speculative rebuild')
        time.sleep(12)
        try:
            current = latest_build(name)
        except subprocess.TimeoutExpired:
            # Transient read timeout: do not issue another build. Attempt to
            # resume inventory polling on the next iteration, within deadline.
            log(f'{name}: existing build status GET timed out; retrying read-only.')
            current = initial
    fail(f'{name}: existing build is still {last_status!r} after '
         f'{BUILD_TIMEOUT}s; no duplicate build requested')


def build_in_progress_conflict(error):
    """Only the observed AMP v1.0.0 build-lock 409 is recoverable.

    Generic 409 conflicts can mean duplicate names or provider collisions and
    must never be retried as though they were transient build locks.
    """
    return (isinstance(error, RuntimeError) and
            re.search(r'\bHTTP\s*409\b', str(error), re.I) is not None and
            'a build is already in progress for this agent' in str(error).lower())


def wait_for_existing_build(name, sha, *, discovery_timeout=None):
    """Read-only discovery + verification of a platform-owned build.

    A fresh agent created with `amctl agent create` can have an asynchronous
    build not yet present in the build list. Never write a duplicate build
    while an inventory entry is expected but not yet visible.
    """
    timeout = BUILD_DISCOVERY_TIMEOUT if discovery_timeout is None else discovery_timeout
    deadline = time.monotonic() + timeout
    while True:
        try:
            found = latest_build(name)
        except subprocess.TimeoutExpired:
            fail(f'{name}: managed build inventory timed out during read-only '
                 'discovery; no new build or configuration write attempted')
        if found is not None:
            # Never call build_agent() here: an additional inventory read that
            # momentarily returns empty could otherwise trigger a new POST.
            # For build-lock recovery we allow READ-ONLY verification and
            # resumption only, with no path capable of creating a build.
            label = status_label(found)
            remote_sha = verified_build_revision(found, name, sha)
            if remote_sha != sha.lower():
                fail(f'{name}: build-lock recovery found an unverified or '
                     'different Git revision; no configuration retry')
            if label in ('queued', 'pending', 'building', 'running',
                         'in_progress', 'in-progress', 'starting',
                         'submitted', 'initializing'):
                resume_existing_build(name, sha, found)
                return True
            if label in ('completed', 'success', 'successful', 'succeeded'):
                state = state_get()
                if state.get('built', {}).get(name) != sha:
                    state.setdefault('built', {})[name] = sha
                    state_save(state)
                return True
            fail(f'{name}: build-lock recovery found build status {label!r}; '
                 'no new build or configuration retry')
        if time.monotonic() >= deadline:
            return False
        time.sleep(min(BUILD_DISCOVERY_INTERVAL, max(0, deadline-time.monotonic())))


def reconcile_with_build_lock(name, sha, action, *, operation):
    """Retry *only* the observed build-lock 409, never any other error.

    Every operation retry begins again with a GET; the caller must implement
    idempotent read/compare/write/read-back reconciliation. No POST/PUT is
    replayed blindly and the already-running build must prove its SHA.
    """
    for attempt in range(3):
        try:
            return operation()
        except RuntimeError as exc:
            if not build_in_progress_conflict(exc):
                raise
            if attempt == 2:
                fail(f'{name}: {action} hit repeated build-lock HTTP 409 after '
                     'verified build checks; no further writes requested')
            log(f'{name}: {action} blocked by an active build; '
                'checking its identity before retrying configuration.')
            # A 409 does not prove what job is running; discovery must return
            # a build with a matching SHA and source coordinates or fail shut.
            if not wait_for_existing_build(name, sha):
                fail(f'{name}: build-lock HTTP 409 but no build appeared in '
                     'read-only inventory; cannot safely retry configuration')
            # Build completion and lock release can be observed at slightly
            # different times; retry is bounded and always follows a fresh GET.
            if BUILD_LOCK_RETRY_DELAY:
                time.sleep(BUILD_LOCK_RETRY_DELAY)
            # operation() restarts from its GET, to distinguish a concurrent
            # successful write from an actually missing configuration.
    fail(f'{name}: exhausted guarded build-lock reconciliation')


def build_agent(name, sha, force=False):
    """Reuse a verified build or fail closed before an unnecessary rebuild.

    In particular, a local missing .state file cannot justify submitting a
    second managed build if Agent Manager already reports a successful build.
    """
    log(f'{name}: checking whether a verified build already exists for Git {sha[:12]}...')
    state=state_get()
    old=state.get('built',{}).get(name)
    try:
        found=latest_build(name)
    except subprocess.TimeoutExpired:
        fail(f'{name}: build inventory read timed out; refusing to submit a '
             'build that may already exist. Retry the read-only inventory.')
    if found is None and force:
        # Agent creation can initiate an automatic build asynchronously.
        # Observe bounded inventory propagation before issuing build create.
        log(f'{name}: newly created agent; checking for an automatic managed build.')
        deadline = time.monotonic() + BUILD_DISCOVERY_TIMEOUT
        while found is None and time.monotonic() < deadline:
            time.sleep(min(BUILD_DISCOVERY_INTERVAL, max(0, deadline - time.monotonic())))
            try:
                found = latest_build(name)
            except subprocess.TimeoutExpired:
                fail(f'{name}: build inventory timed out after agent creation; '
                     'no duplicate build requested')
    if found:
        label=status_label(found)
        remote_sha=verified_build_revision(found, name, sha)
        completed=('succeeded','success','successful','completed')
        if remote_sha and remote_sha != sha.lower():
            if label not in completed:
                fail(f'{name}: an existing managed build for commit {remote_sha[:12]} '
                     f'is in state {label!r}; refusing to start another build')
            # Completed older revision; a new build is justified.
        elif label in completed:
            if remote_sha == sha.lower() or (old == sha and remote_sha is None):
                if old != sha:
                    state.setdefault('built',{})[name]=sha
                    state_save(state)
                log(f'Reusing successful managed build for {name} at Git SHA {sha[:12]}.')
                return
            fail(f'{name}: Agent Manager has a successful build but its commit '
                 'cannot be verified against the desired Git SHA; no duplicate '
                 'build requested. Inspect `amctl agent build list --json` and '
                 'the private full-automation-state.json record.')
        elif label in ('queued','pending','building','running','in_progress',
                       'in-progress','starting','submitted','initializing'):
            resume_existing_build(name, sha, found)
            return
        elif label in ('failed','failure','error','cancelled','canceled'):
            fail(f'{name}: latest managed build is {label!r}; manual review required '
                 'before requesting another build')
        else:
            fail(f'{name}: existing managed build is {label or "unknown"!r}; '
                 'will not submit a duplicate while build status is unresolved')
    elif old == sha and not force:
        fail(f'{name}: local build state says {sha[:12]}, but no remote managed '
             'build can be confirmed. Refusing a speculative rebuild.')
    previous_build_name = (found or {}).get('buildId') or (found or {}).get('buildName') or (found or {}).get('name') or (found or {}).get('uuid')
    log(f'{name}: requesting managed build at Git {sha[:12]} (command timeout: 180s).')
    try:
        # Use the configured Git branch's latest commit. Avoid a direct remote
        # fetch by commit ID, which is not guaranteed to be accepted by Git.
        cmd(['amctl','agent','build','create',name,'--project',PROJECT,'--org',ORG],
            timeout=180)
    except RuntimeError as exc:
        if not build_in_progress_conflict(exc):
            raise
        log(f'{name}: build-create returned build-lock 409; reconciling the '
            'existing build instead of issuing another create.')
        if not wait_for_existing_build(name, sha):
            fail(f'{name}: build-create returned HTTP 409, but no existing '
                 'build was observable; refusing a duplicate')
        return
    except subprocess.TimeoutExpired:
        fail(f'{name}: amctl build-create command timed out after 180s. '
             f'Check: amctl agent build list {name} --project {PROJECT} --org {ORG} '
             'before retrying; the server might have accepted the request.')
    log(f'{name}: build request returned; checking managed build status '
        f'(timeout: {BUILD_TIMEOUT}s).')
    deadline=time.monotonic()+BUILD_TIMEOUT
    last_state=None
    last_report=-1.0
    started=time.monotonic()
    while time.monotonic()<deadline:
        try:
            b=latest_build(name)
        except subprocess.TimeoutExpired:
            log(f'{name}: build-status request timed out; will retry.')
            b=None
        label=status_label(b) if b else ''
        elapsed=int(time.monotonic()-started)
        build_name=(b or {}).get('buildName') or (b or {}).get('name') or 'not listed yet'
        display_state=label or ('not listed yet' if b is None else 'status unavailable')
        if display_state!=last_state or elapsed-last_report>=60:
            log(f'{name}: managed build {build_name}, status={display_state}, '
                f'elapsed={elapsed}s/{BUILD_TIMEOUT}s.')
            last_state=display_state
            last_report=elapsed
        current_build_name = ((b or {}).get('buildId') or (b or {}).get('buildName') or (b or {}).get('name') or (b or {}).get('uuid'))
        current_sha = verified_build_revision(b, name, sha)
        if current_sha and current_sha != sha.lower():
            # A completed older build can stay at the top of the list until the
            # server registers our asynchronous build request.
            time.sleep(12)
            continue
        # A changing buildName/buildId proves a different job, not that it
        # built the intended commit. Require the Git revision to be verified.
        is_new_build = current_sha == sha.lower()
        if not is_new_build:
            time.sleep(12)
            continue
        if label in ('success','successful','succeeded','completed'):
            state=state_get()
            state.setdefault('built',{})[name]=sha
            state_save(state)
            log(f'Build succeeded: {name}')
            return
        if label in ('failed','failure','error','cancelled','canceled'):
            fail(f'Agent {name} build failed ({label}). '
                 f'Inspect: amctl agent build logs {name} --project {PROJECT} --org {ORG}')
        time.sleep(12)
    fail(f'Timed out waiting for build {name} after {BUILD_TIMEOUT}s '
         f'(last state: {last_state or "unknown"}). '
         f'Inspect: amctl agent build list {name} --project {PROJECT} --org {ORG} --json ; '
         f'amctl agent build logs {name} --project {PROJECT} --org {ORG}')


def tool_config_paths(agent):
    # NEVER post an MCP tool configuration into /model-configs:
    # Agent Manager v1.0.0 treats that endpoint as LLM-only ("Type must be llm").
    # Probe only dedicated tool/MCP configuration collections.
    prefix=f'/orgs/{ORG}/projects/{PROJECT}/agents/{agent}'
    return [prefix+'/tool-configurations',prefix+'/tool-configs',
            prefix+'/mcp-configurations',prefix+'/mcp-configs']


def config_records(obj):
    return collection(obj,('modelConfigurations','modelConfigs','toolConfigurations',
                           'toolConfigs','configurations','configs'))


def mcp_binding_matches(entry):
    if not isinstance(entry,dict):return False
    envs=entry.get('envMappings') or {}
    providers=[v.get('providerName') for v in envs.values() if isinstance(v,dict)] if isinstance(envs,dict) else []
    return entry.get('name')==PROXY or entry.get('mcpProxyId')==PROXY or PROXY in providers


def mcp_binding_payload():
    # Based on WSO2 agent-manager-service/models.CreateAgentModelConfigRequest.
    return {'name':PROXY,'description':'Banco Aurora managed banking MCP tools',
            'type':'mcp', 'envMappings':{ENV:{'providerName':PROXY}},
            'environmentVariables':[
                {'name':'MCP_SERVER_URL','key':'url'},
                {'name':'MCP_PROXY_API_KEY','key':'apikey'}]}


def verify_mcp_tool_config_detail(path, summary, agent):
    """Read the complete v1.0.0 MCP config and reconcile only its env names.

    List items intentionally contain only uuid/name/type; do not mistake them
    for a full AgentModelConfigResponse. The detail endpoint supplies
    environmentVariables; a verified Banco Aurora binding may be updated via
    UpdateAgentModelConfigRequest with environmentVariables only.
    """
    uuid = summary.get('uuid') or summary.get('id')
    if not isinstance(uuid, str) or not uuid.strip():
        fail(f'{agent}: MCP tool config list entry has no stable UUID; '
             'cannot verify injected variables without its detail record')
    detail_path = path + '/' + urllib.parse.quote(uuid, safe='')

    def fetch_detail():
        obj = api(detail_path, allow_404=True)
        if not isinstance(obj, dict):
            fail(f'{agent}: MCP tool config detail GET is unavailable at {detail_path}; '
                 'refusing to infer injection from the list summary')
        if obj.get('uuid') and obj['uuid'] != uuid:
            fail(f'{agent}: MCP tool config detail UUID does not match list entry')
        if obj.get('type') != 'mcp' or not mcp_binding_matches(obj):
            fail(f'{agent}: MCP tool config detail references an unexpected proxy/type')
        return obj

    def mappings(obj):
        variables = obj.get('environmentVariables')
        if not isinstance(variables, list):
            fail(f'{agent}: MCP tool config detail omitted environmentVariables; '
                 'cannot verify runtime injection')
        return {(v.get('key'), v.get('name')) for v in variables if isinstance(v, dict)}

    detail=fetch_detail()
    expected={('url','MCP_SERVER_URL'),('apikey','MCP_PROXY_API_KEY')}
    missing=expected-mappings(detail)
    if missing:
        # Only ever mutate a record whose UUID, type and named target proxy
        # have been independently verified. UpdateAgentModelConfigRequest
        # supports environmentVariables alone; do not overwrite envMappings.
        api(detail_path,'PUT',{'environmentVariables':mcp_binding_payload()['environmentVariables']})
        detail=fetch_detail()
        missing=expected-mappings(detail)
        if missing:
            fail(f'{agent}: MCP environment variable update was not persisted: '
                 + ', '.join(f'{key} -> {name}' for key,name in sorted(missing)))
        log(f'{agent}: repaired MCP environment-variable names (verified by read-back).')
    return detail


def bind_tool(agent):
    found=[]
    for path in tool_config_paths(agent):
        result=api(path,allow_404=True)
        if result is not None:
            found.append((path,result))
    if len(found)!=1:
        fail(f'Cannot safely identify Agent Manager tool-configuration REST collection for {agent} '
             f'(found {len(found)} candidates). No speculative writes made. '
             'Confirm the installed Agent Manager 1.0.0 API schema before proceeding.')
    path,obj=found[0]
    matching=[r for r in config_records(obj) if mcp_binding_matches(r)]
    if len(matching)>1:fail(f'Ambiguous MCP configurations for {agent}')
    if not matching:
        api(path,'POST',mcp_binding_payload())
        log(f'Created declarative MCP tool configuration for {agent}.')
    current=config_records(api(path))
    matching=[r for r in current if mcp_binding_matches(r)]
    if len(matching)!=1:
        fail(f'Cannot confirm exactly one MCP tool config for {agent} after reconciliation')
    # Deliberately verify via its dedicated detail endpoint, *not* the list
    # entry, which intentionally omits environmentVariables in v1.0.0.
    verify_mcp_tool_config_detail(path, matching[0], agent)
    log(f'Agent {agent}: governed MCP tool binding and injection names verified.')


def runtime_deployment(name):
    """Fetch the real AMP v1.0.0 environment-keyed deployment map."""
    from aurora_runtime_contract import deployment_entry
    response = api(f'/orgs/{ORG}/projects/{PROJECT}/agents/{name}/deployments')
    return deployment_entry(response, name, ENV)


def runtime_pod_ready(name):
    """Check the managed Service and startup TCP probe; never edit Kubernetes."""
    from aurora_runtime_contract import service_port, pod_readiness
    services = cmd(['kubectl','--context','k3d-amp-local','get','svc','-A','-o','json'])
    obj = json.loads(services.stdout)
    found = [s for s in obj.get('items', [])
             if s.get('metadata', {}).get('name') == name
             and s.get('metadata', {}).get('namespace','').startswith('dp-default-banco-aurora-default-')]
    if len(found) != 1:
        fail(f'{name}: no uniquely identified managed Service in k3d; cannot verify readiness')
    service_port(found[0], name)
    namespace=found[0]['metadata']['namespace']
    pods=cmd(['kubectl','--context','k3d-amp-local','-n',namespace,'get','pods','-o','json'])
    ready, details, port = pod_readiness(json.loads(pods.stdout).get('items',[]),name)
    return ready and port in (None,'8000'),f'{details}; PORT={port or "not-explicit"}'


def runtime_ready(name):
    response=runtime_deployment(name)
    if response['status'] != 'active':
        return False, 'Agent Manager deployment status=' + response['status']
    return runtime_pod_ready(name)


def wait_runtime_ready(name, timeout=360):
    deadline=time.monotonic()+timeout
    last=''
    while True:
        ready, details=runtime_ready(name)
        if ready:
            log(f'{name}: deployment active and Kubernetes main container Ready on port 8000.')
            return
        if details != last:
            log(f'{name}: awaiting platform readiness: {details}')
            last=details
        if time.monotonic()>=deadline:
            fail(f'{name}: deployment failed to reach active + Kubernetes Ready within {timeout}s: '
                 f'{details}. Inspect startup probe, runtime PORT and pod events.')
        time.sleep(10)


def deploy_agent(name, envvars=None):
    """Reconcile runtime port without rebuild or credential changes.

    `amctl deploy --env` merges variables in AMP 1.0.0. Never pass secrets.
    A local state fingerprint permits subsequent healthy/no-change reuse.
    """
    from aurora_runtime_contract import EXPECTED_PORT
    envvars = dict(envvars or {})
    if 'PORT' in envvars and envvars['PORT'] != str(EXPECTED_PORT):
        fail(f'{name}: conflicting PORT override; expected {EXPECTED_PORT}')
    envvars['PORT']=str(EXPECTED_PORT)
    sha = get_git_commit()  # Already verified source in managed_agents; fail if it drifted.
    build = latest_build(name)
    if not build or verified_build_revision(build,name,sha) != sha or status_label(build) not in ('completed','success','successful','succeeded'):
        fail(f'{name}: cannot select a completed verified build for deployment')
    buildname=build.get('buildName')
    if not isinstance(buildname,str) or not buildname:
        fail(f'{name}: latest successful build lacks buildName')
    fingerprint=hashlib.sha256(json.dumps({'sha':sha,'build':buildname,'envvars':envvars},sort_keys=True).encode()).hexdigest()
    state=state_get()
    if state.get('runtime_deployments',{}).get(name) == fingerprint:
        ready,_=runtime_ready(name)
        if ready:
            log(f'{name}: existing deployment active/Ready with matching configuration; skipped redeploy.')
            return
    args=['amctl','agent','deploy',name,'--project',PROJECT,'--org',ORG,
          '--build-name',buildname,'--yes']
    for k,v in sorted(envvars.items()):
        args.extend(['--env',f'{k}={v}'])
    cmd(args,timeout=240)
    wait_runtime_ready(name)
    state=state_get()
    state.setdefault('runtime_deployments',{})[name]=fingerprint
    state_save(state)
    log(f'Deployment verified: {name}; active/Ready. Build reused and secrets unchanged.')


def agent_role_assignees(response, *, role_name):
    """Parse the AMP v1.0.0 GET role-assignment response, never POST shape.

    `thundersvc.AgentRoleAssignments` has `agents` and `groups` (both with
    omitempty); `assignments` is ONLY the POST /assignments/add request field.
    `{}` is a legitimate zero-assignee response from Go's `omitempty` JSON.
    Unknown layouts must not cause an accidental duplicate grant.
    """
    if not isinstance(response, dict):
        fail(f'AgentID {role_name}: role assignments GET did not return an object')
    if 'assignments' in response:
        fail(f'AgentID {role_name}: GET returned an unexpected assignments request shape; '
             'inspect raw API response; no role changes')
    if set(response) - {'agents', 'groups'}:
        fail(f'AgentID {role_name}: unfamiliar assignment response fields; '
             'inspect raw API response; no role changes')
    agents = response.get('agents', [])
    groups = response.get('groups', [])
    if not isinstance(agents, list) or not isinstance(groups, list):
        fail(f'AgentID {role_name}: agents/groups are not arrays; no role changes')
    if not all(isinstance(g, dict) and isinstance(g.get('id'), str)
               and g['id'] for g in groups):
        fail(f'AgentID {role_name}: malformed group assignment record; no role changes')
    for entry in agents:
        if (not isinstance(entry, dict) or
                not isinstance(entry.get('id'), str) or not entry['id'] or
                entry.get('type') != 'agent'):
            fail(f'AgentID {role_name}: malformed agent assignment record; '
                 'no role changes')
    identities = [a['id'] for a in agents]
    if len(identities) != len(set(identities)):
        fail(f'AgentID {role_name}: duplicate agent assignments; no role changes')
    return set(identities)


def ensure_identity_roles(*, read_only=False):
    """Reconcile direct AgentID grants; verify using AMP 1.0.0 GET semantics.

    `check` is read-only. Only managed-agent provisioning may add an absent
    grant, and only after every specialist identity and role has been uniquely
    resolved. No remove/reassign/write against an unfamiliar API shape.
    """
    base=f'/orgs/{ORG}/environments/{ENV}/agent-identities'
    roles=collection(api(base+'/roles'),('roles',))
    agents=collection(api(base+'/agents'),('agents',))
    targets=[]
    for role in ROLES:
        name='aurora-'+role
        rs=[r for r in roles if isinstance(r,dict) and r.get('name')==name]
        ais=[a for a in agents if isinstance(a,dict) and
             (a.get('name')==name or a.get('agentName')==name or
              a.get('displayName') in (name,'Aurora '+role.title())) and
             a.get('projectName', PROJECT)==PROJECT]
        if len(rs)!=1 or len(ais)!=1:
            fail(f'Cannot uniquely match AgentID role and identity for {name} '
                 f'(roles={len(rs)}, identities={len(ais)}); no grants changed')
        r=rs[0]; a=ais[0]
        rid=r.get('id') or r.get('roleId')
        # A control-plane agent id is not the environment Thunder identity id.
        # The v1.0.0 API and official guide explicitly provide thunderAgentId.
        agentid=a.get('thunderAgentId')
        if not isinstance(rid,str) or not rid or not isinstance(agentid,str) or not agentid:
            fail(f'AgentID {name}: missing role id or thunderAgentId; '
                 'refusing to infer identity from a control-plane id')
        targets.append((name,rid,agentid))
    if len({rid for _,rid,_ in targets})!=len(targets):
        fail('AgentID: different Banco Aurora roles resolve to the same role ID; no grants changed')
    if len({aid for _,_,aid in targets})!=len(targets):
        fail('AgentID: different Banco Aurora specialists resolve to the same Thunder identity ID; no grants changed')

    # Preflight ALL five assignment collections before the first POST. A
    # truncated or unexpected response later in the list must not produce a
    # partially written set of grants from this run.
    planned=[]
    for name,rid,agentid in targets:
        path=base+'/roles/'+urllib.parse.quote(rid,safe='')+'/assignments'
        current=agent_role_assignees(api(path), role_name=name)
        planned.append((name,path,agentid,agentid in current))

    for name,path,agentid,already_granted in planned:
        if not already_granted:
            if read_only:
                fail(f'AgentID assignment missing: {name}; check is read-only. '
                     'Run start-everything.sh agents to reconcile after inspection')
            payload={'assignments':[{'id':agentid,'type':'agent'}]}
            try:
                api(path+'/add','POST',payload)
            except RuntimeError as ex:
                # Another provisioner can add the same grant between GET and
                # POST. A 409 may be accepted ONLY after verifying the exact
                # role+Thunder agent identity by an independent GET.
                if not re.search(r'(?i)\b409\b|CONFLICT',str(ex)):
                    raise
                if agentid not in agent_role_assignees(api(path),role_name=name):
                    raise
                log(f'AgentID {name}: concurrent direct grant verified after 409.')
        # Thunder read-after-write propagation may lag. Re-read only; never
        # repeat POST because this would risk a duplicate assignment.
        for attempt in range(3):
            latest=agent_role_assignees(api(path),role_name=name)
            if agentid in latest:
                log(f'AgentID {name}: direct role assignment verified '
                    f'({"reused" if already_granted else "reconciled"}).')
                break
            if attempt < 2:
                time.sleep(1)
        else:
            fail(f'AgentID assignment not verified after read-back: {name}; '
                 'no duplicate grant request made. Inspect the role assignments GET')


def gateway_key_paths(agent):
    prefix=f'/orgs/{ORG}/projects/{PROJECT}/agents/{agent}'
    return [prefix+'/environments/'+ENV+'/api-keys',
            prefix+'/environments/'+ENV+'/apikeys',
            prefix+'/api-keys?environment='+ENV,
            prefix+'/api-keys',
            prefix+'/apikeys']


def provision_gateway_key(name):
    """Only create one-time keys via a read-verified endpoint, never invent them."""
    secrets_data=service_secrets()
    key_name='CONCIERGE_API_KEY' if name=='aurora-concierge' else 'AGENT_'+name.removeprefix('aurora-').upper()+'_API_KEY'
    if secrets_data.get(key_name):
        log(f'Reusing locally recorded gateway API key for {name}.')
        return secrets_data[key_name]
    valid=[]
    for path in gateway_key_paths(name):
        result=api(path,allow_404=True)
        if result is not None:
            valid.append(path)
    if len(valid)!=1:
        fail(f'No unambiguous read-verified gateway API-key API for {name} ({len(valid)} candidate routes). '
             'The v1.0.0 credentials API must be confirmed before automatically provisioning a key; '
             'existing credentials cannot be recovered through GET.')
    payload={'name':'aurora-'+name.removeprefix('aurora-')+'-demo',
             'displayName':'Banco Aurora internal demo '+name}
    # Creation returns full API key ONLY ONCE. Never echo it to logs.
    created=api(valid[0],'POST',payload)
    if not isinstance(created,dict):fail(f'Unexpected gateway API key response for {name}')
    key=created.get('apiKey') or created.get('key')
    if not isinstance(key,str) or len(key)<16:
        fail(f'Gateway key creation for {name} returned no usable secret; do not retry blindly '
             '(inspect existing key records before requesting another).')
    update_kv(SUPPORT/'secrets.env',{key_name:key})
    log(f'Stored newly issued gateway credential for {name} in private secrets.env.')
    return key


def resolve_invoke_url(name):
    """Source endpoint from verified *active* deployment, not agent GET config."""
    from aurora_runtime_contract import invoke_url
    obj=api(f'/orgs/{ORG}/projects/{PROJECT}/agents/{name}/deployments')
    url=invoke_url(obj,name,ENV,require_active=True)
    ready, details=runtime_pod_ready(name)
    if not ready:
        fail(f'{name}: deployment URL exists but Kubernetes workload is not Ready: {details}')
    return url


def configure_portal():
    # Only invoke after all agents have been deployed and both invoke URL and
    # platform-generated gateway key have been saved.
    url=resolve_invoke_url('aurora-concierge')
    key=provision_gateway_key('aurora-concierge')
    update_kv(SUPPORT/'secrets.env',{'CONCIERGE_URL':url,'CONCIERGE_API_KEY':key})
    update_kv(SUPPORT/'support.env',{'PORTAL_ENABLED':'true'})
    log('Reconcile Portal with verified Concierge endpoint and private gateway key.')
    cmd(['bash','agent-manager-support/start.sh','support'],timeout=900,hide=True)
    cmd(['kubectl','--context','k3d-amp-local','-n','aurora-demo','rollout','status',
         'deployment/aurora-portal','--timeout=180s'])
    # Fail closed if the BFF still holds the wrong URL/key or cannot route to
    # the authenticated, already-deployed customer-facing Concierge.
    cmd(['python3','scripts/verify-aurora-portal.py'], timeout=90, hide=True)
    log('Portal deployment and private Concierge gateway connectivity verified.')


def portal_forward_local_port(output):
    """Extract ONLY kubectl's loopback port-forward to Portal service port 8300.

    The output is read solely from the new kubectl subprocess's own log lines,
    starting after its launch offset; an unrelated listener is never adopted.
    """
    if not isinstance(output, bytes):
        fail('Unexpected kubectl port-forward output encoding')
    ports = [int(x) for x in re.findall(
        rb'(?m)^Forwarding from 127\.0\.0\.1:([0-9]{1,5}) -> 8300\r?$', output)]
    ports = [p for p in ports if 1 <= p <= 65535]
    if len(set(ports)) > 1:
        fail('kubectl emitted conflicting Portal loopback port-forward addresses')
    return ports[0] if ports else None


def await_portal_forward(proc, logfile, launch_offset, timeout=30):
    """Get the dynamically assigned loopback port from OUR kubectl process.

    Never guess or reuse another process's port, even if an existing SSH tunnel
    is already listening on 8300 or an old Portal port-forward remains running.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            fail('Portal port-forward exited unexpectedly; inspect '
                 'agent-manager-support/portal-forward.log')
        with logfile.open('rb') as reader:
            reader.seek(launch_offset)
            new_output = reader.read(131072)
        chosen = portal_forward_local_port(new_output)
        if chosen is not None:
            return chosen
        time.sleep(.2)
    fail('kubectl did not publish its assigned loopback Portal forwarding port '
         'within 30 seconds; inspect agent-manager-support/portal-forward.log')


def verify_scenarios():
    """Required S01-S06 exercises using our own exclusive loopback forwarding.

    Kubectl's :8300 notation chooses an available local port automatically,
    while the remote Portal service remains on port 8300. This intentionally
    avoids interrupting unrelated SSH tunnels or older port-forward processes.
    """
    import urllib.request
    log('Starting loopback-only Portal port-forward with an automatic local port for S01-S06.')
    logfile = SUPPORT / 'portal-forward.log'
    proc = None
    try:
        with logfile.open('ab') as out:
            launch_offset = out.tell()
            proc = subprocess.Popen(
                ['kubectl', '--context', 'k3d-amp-local', '-n', 'aurora-demo',
                 'port-forward', 'svc/aurora-portal', ':8300', '--address', '127.0.0.1'],
                stdin=subprocess.DEVNULL, stdout=out, stderr=out,
                start_new_session=True)
        local_port = await_portal_forward(proc, logfile, launch_offset)
        portal_url = f'http://127.0.0.1:{local_port}'
        log(f'Portal scenario forwarding confirmed on loopback port {local_port} '
            '(remote service port 8300).')
        for _ in range(30):
            if proc.poll() is not None:
                fail('Portal port-forward exited while awaiting Portal HTTP readiness; '
                     'inspect agent-manager-support/portal-forward.log')
            try:
                with urllib.request.urlopen(portal_url + '/', timeout=2) as res:
                    if res.status == 200:
                        break
            except Exception:
                time.sleep(1)
        else:
            fail('Portal did not become reachable through our loopback port-forward.')
        e2e_python = SUPPORT / '.venv-e2e' / 'bin' / 'python'
        if not e2e_python.exists():
            cmd(['python3', '-m', 'venv', str(e2e_python.parent.parent)], timeout=90)
        p = cmd([str(e2e_python), '-c', 'import httpx'], optional=True, timeout=15)
        if p.returncode:
            cmd([str(e2e_python), '-m', 'pip', 'install', 'httpx>=0.27,<0.29'], timeout=120)
        cmd([str(e2e_python), 'scripts/run-executive-scenarios.py',
             '--portal', portal_url,
             '--report', str(SUPPORT / 'executive-scenario-report.json'),
             '--env-file', str(SUPPORT / 'secrets.env')], timeout=480)
        report = json.loads((SUPPORT / 'executive-scenario-report.json').read_text())
        targets = {f'S0{x}' for x in range(1, 7)}
        passing = {r.get('scenario') for r in report.get('scenarios', [])
                   if r.get('passed') is True}
        if not targets.issubset(passing):
            fail(f'Not all six stateful banking scenarios passed: {sorted(targets-passing)}')
        log('Six Portal → managed Concierge → specialists banking scenarios passed.')
    finally:
        if proc is not None and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()


def concierge_secret_sources(container, secret_get):
    """Resolve only Kubernetes Secret-backed environment names, never values from pod logs.

    Return a mapping key -> bytes. Reject any plaintext sensitive env entry,
    ambiguous secret sources, missing K8s Secret data, or malformed Secret refs.
    Works with explicit env.valueFrom.secretKeyRef and envFrom.secretRef.
    """
    if not isinstance(container, dict):
        fail('Concierge: main container spec is missing')
    required = {f'AGENT_{r.upper()}_API_KEY' for r in ROLES}
    required.update(('BANK_CORE_READ_KEY','BANK_APPROVAL_KEY','CONCIERGE_CALL_KEY',
                     'CONCIERGE_APPROVAL_KEY','SPECIALIST_CALL_KEY','SPECIALIST_EXECUTION_KEY'))
    collected = {}
    def append(key, value, source):
        if key not in required:
            return
        if key in collected:
            fail(f'Concierge: duplicate secret source for {key}; no deployment mutation')
        collected[key] = (value, source)
    def decoded(secret_name, key):
        if not isinstance(secret_name,str) or not secret_name or not isinstance(key,str) or not key:
            fail('Concierge: malformed Kubernetes Secret reference')
        obj = secret_get(secret_name)
        if not isinstance(obj,dict) or not isinstance(obj.get('data'),dict):
            fail(f'Concierge: referenced Kubernetes Secret {secret_name!r} has no data map')
        raw = obj['data'].get(key)
        if not isinstance(raw,str):
            fail(f'Concierge: referenced Secret {secret_name!r} is missing key {key!r}')
        try:
            return base64.b64decode(raw,validate=True)
        except (ValueError,binascii.Error):
            fail(f'Concierge: referenced Secret {secret_name!r} contains malformed encoded data')
    env = container.get('env') or []
    if not isinstance(env,list):
        fail('Concierge: Kubernetes container env is malformed')
    for entry in env:
        if not isinstance(entry,dict):
            fail('Concierge: malformed Kubernetes env variable')
        key=entry.get('name')
        if key not in required:
            continue
        if 'value' in entry:
            fail(f'Concierge: {key} is a plaintext Kubernetes env value, not a Secret reference')
        sr=((entry.get('valueFrom') or {}).get('secretKeyRef') or {})
        if not isinstance(sr,dict) or not sr.get('name') or not sr.get('key'):
            fail(f'Concierge: {key} is not backed by a Kubernetes Secret key reference')
        append(key,decoded(sr['name'],sr['key']),sr['name'])
    refs=container.get('envFrom') or []
    if not isinstance(refs,list):
        fail('Concierge: Kubernetes envFrom is malformed')
    for entry in refs:
        if not isinstance(entry,dict):
            fail('Concierge: malformed Kubernetes envFrom record')
        secret_ref=entry.get('secretRef')
        if secret_ref is None:
            continue
        if not isinstance(secret_ref,dict) or not secret_ref.get('name'):
            fail('Concierge: malformed envFrom Secret reference')
        name=secret_ref['name']
        obj=secret_get(name)
        if not isinstance(obj,dict) or not isinstance(obj.get('data'),dict):
            fail(f'Concierge: referenced Kubernetes Secret {name!r} has no data map')
        prefix=entry.get('prefix') or ''
        if not isinstance(prefix,str):
            fail('Concierge: envFrom prefix is malformed')
        for raw_key in obj['data']:
            logical_key=prefix+raw_key
            if logical_key in required:
                append(logical_key,decoded(name,raw_key),name)
    return collected



class PodPythonUnavailable(RuntimeError):
    """Only raised after OCI exec proves every allowed Python executable absent.

    This differs from forbidden exec, missing pods, container failures, Python
    exceptions and timeouts: those must remain fatal, even on minimal images.
    """


def exec_concierge_python_proof(namespace, pod_name, proof_code, nonce_hex, names):
    """Run a read-only env proof with a verified Python 3 executable in the pod.

    AMP buildpacks may provide python3 but no `python` alias. Only retry when
    Kubernetes explicitly says the attempted executable is absent; do not
    reinterpret RBAC errors, pod errors or an actual Python execution failure
    as an invitation to try a different command. No credential *values* enter
    kubectl arguments or stdout.
    """
    interpreters = (
        'python3', 'python3.12', 'python', '/usr/local/bin/python3',
        '/usr/bin/python3', '/opt/venv/bin/python3', '/venv/bin/python3',
    )
    for interpreter in interpreters:
        call = [
            'kubectl', '--context', 'k3d-amp-local', '-n', namespace,
            'exec', pod_name, '-c', 'main', '--', interpreter, '-c',
            proof_code, nonce_hex, *sorted(names),
        ]
        try:
            result = cmd(call, timeout=40, optional=True, hide=True)
        except subprocess.TimeoutExpired:
            fail('Concierge: in-pod Python secret verification timed out; '
                 'no credentials were changed')
        if getattr(result, 'returncode', 0) == 0:
            return result
        message = ((getattr(result, 'stderr', '') or '') + '\n' +
                   (getattr(result, 'stdout', '') or '')).lower()
        # OCI container exec errors prove *this executable* cannot start.
        # Avoid retrying ordinary Python failures, missing pods, auth failures,
        # policy denials, or unrelated "not found" errors.
        missing_binary = (
            'executable file not found in $path' in message and 'exec:' in message
        ) or (
            ('no such file or directory' in message or 'not found' in message)
            and ('exec: "' + interpreter.lower() + '"' in message
                 or 'stat ' + interpreter.lower() in message)
        )
        if missing_binary:
            continue
        fail('Concierge: in-pod Python secret verification failed for '
             + interpreter + ' (not an executable-not-found error); '
             'inspect the pod and kubectl permissions. No credentials changed')
    raise PodPythonUnavailable(
        'Concierge: none of the expected Python 3 executables is available '
        'inside the managed main container; inspect the image runtime PATH. '
        'Kubernetes Secret bindings were checked separately; no credentials changed')


def verify_concierge_managed_secrets(creds, *, require_runtime=False, strict_process=False):
    """Read-only proof of managed Concierge secret wiring for this exact cluster.

    The agent GET response never exposes secret mappings. Instead inspect the
    *effective Kubernetes pod spec* and Secret values in memory (no logging).
    Only the five gateway keys plus existing banking secrets are compared;
    the upstream OpenAI key is never read or passed to the Portal.
    """
    _, expected = agent_config('concierge',creds)
    services=cmd(['kubectl','--context','k3d-amp-local','get','svc','-A','-o','json'])
    try:
        svc=json.loads(services.stdout)
    except ValueError:
        fail('Concierge: Kubernetes returned invalid Service inventory')
    matches=[item for item in svc.get('items',[]) if isinstance(item,dict)
             and (item.get('metadata') or {}).get('name')=='aurora-concierge'
             and (item.get('metadata') or {}).get('namespace','').startswith('dp-'+ORG+'-'+PROJECT+'-'+ENV+'-')]
    if len(matches)!=1:
        fail('Concierge: expected one managed Kubernetes Service; cannot establish secret ownership')
    namespace=matches[0]['metadata']['namespace']
    pod_resp=cmd(['kubectl','--context','k3d-amp-local','-n',namespace,'get','pods','-o','json'])
    try:
        pods=json.loads(pod_resp.stdout).get('items',[])
    except (ValueError,AttributeError):
        fail('Concierge: Kubernetes returned invalid pod inventory')
    if not isinstance(pods,list):
        fail('Concierge: Kubernetes pod inventory has no items array')
    candidates=[]
    for pod in pods:
        if not isinstance(pod,dict):continue
        meta=pod.get('metadata') or {}
        if not isinstance(meta,dict) or not str(meta.get('name','')).startswith('aurora-concierge-') or meta.get('deletionTimestamp'):
            continue
        spec=pod.get('spec') or {}
        statuses=(pod.get('status') or {}).get('containerStatuses') or []
        container_list=spec.get('containers') or []
        main=[c for c in container_list if isinstance(c,dict) and c.get('name')=='main']
        if len(main)!=1:continue
        if (pod.get('status') or {}).get('phase')!='Running':continue
        ready=any(c.get('name')=='main' and c.get('ready') is True for c in statuses)
        candidates.append((ready,meta['name'],main[0]))
    if not candidates:
        fail('Concierge: no Running managed main pod to verify Secret references. '
             'Inspect Concierge pod startup before changing credentials')
    ready_candidates=[(pod_name,container) for ready,pod_name,container in candidates if ready]
    if len(ready_candidates)==1:
        pod_name,container=ready_candidates[0]
        selected_ready=True
    elif len(candidates)==1:
        selected_ready,pod_name,container=candidates[0]
    else:
        fail('Concierge: multiple possible managed pod revisions; wait for rollout to settle')
    cache={}
    def secret_get(name):
        if name not in cache:
            p=cmd(['kubectl','--context','k3d-amp-local','-n',namespace,'get','secret',name,'-o','json'])
            try:
                obj=json.loads(p.stdout)
            except ValueError:
                fail('Concierge: malformed Kubernetes Secret response; no changes made')
            # Guard against receiving a Secret from a different namespace.
            if (obj.get('metadata') or {}).get('namespace',namespace)!=namespace:
                fail('Concierge: Kubernetes Secret namespace mismatch')
            cache[name]=obj
        return cache[name]
    collected=concierge_secret_sources(container,secret_get)
    missing=sorted(set(expected)-set(collected))
    if missing:
        fail('Existing Concierge is missing actual Kubernetes Secret-backed env references: '
             +', '.join(missing)+'. Reconcile these through Agent Manager Deploy > '
             'Configurations and Secrets; amctl deploy --env only supports plaintext. '
             'No secrets were changed.')
    wrong=sorted(k for k,value in expected.items()
                 if not hmac.compare_digest(collected[k][0],value.encode('utf-8')))
    if wrong:
        fail('Existing Concierge managed Secret values differ from the private expected credentials: '
             +', '.join(wrong)+'. Do not rotate keys or copy them into deployment flags.')
    if require_runtime:
        # Kubernetes Secret values may have changed since the process started.
        # Prove that the *running process* received the matching values using
        # keyed digests. No key bytes cross stdout, argv, or the browser.
        if not selected_ready:
            fail('Concierge: main container is not Ready; cannot verify live secret injection')
        nonce=secrets.token_bytes(32)
        proof_code=(
            'import os,sys,json,hmac,hashlib\n'
            'nonce=bytes.fromhex(sys.argv[1])\n'
            'names=sys.argv[2:]\n'
            'print(json.dumps({k:hmac.new(nonce,os.environ[k].encode(),hashlib.sha256).hexdigest() '
            'if k in os.environ else None for k in names}))\n')
        try:
            process=exec_concierge_python_proof(
                namespace,pod_name,proof_code,nonce.hex(),expected)
        except PodPythonUnavailable:
            # AMP-managed containers may use minimal/distroless runtime images.
            # The verified Ready pod spec + actual Kubernetes Secret bytes
            # establish that Kubernetes was instructed to inject all 11 keys.
            # They do NOT prove that PID 1 currently uses those values; do not
            # claim an in-process proof. The downstream authenticated gateway
            # and end-to-end scenario checks still gate overall success.
            if strict_process:
                raise
            log('Concierge: all 11 Kubernetes Secret-backed variables match private '
                'credentials; main pod Ready. No executable is available for '
                'in-process verification (NOT proven). Continuing to mandatory '
                'Portal/gateway and end-to-end checks; no secrets changed.')
            return 'kubernetes-only'
        try:
            proof=json.loads(process.stdout)
        except ValueError:
            fail('Concierge: in-pod runtime proof returned invalid JSON; no credentials changed')
        if not isinstance(proof,dict) or set(proof)!=set(expected):
            fail('Concierge: in-pod runtime proof omitted expected variables')
        bad=[]
        for key,value in expected.items():
            digest=hmac.new(nonce,value.encode(),hashlib.sha256).hexdigest()
            if not hmac.compare_digest(proof.get(key) or '',digest):
                bad.append(key)
        if bad:
            fail('Concierge: running process secrets do not match saved gateway keys: '
                 +', '.join(sorted(bad))+'. No secret changes made.')
    log('Concierge: verified all 11 Secret-backed variables against private values'
        +(' and running process' if require_runtime else '')+'; no secrets logged or changed.')
    return 'process-verified' if require_runtime else 'kubernetes-only'

def managed_agents():
    verify_proxy()
    sha=get_git_commit()
    ensure_project()
    ensure_llm_provider()
    creds=service_secrets()
    for role in ROLES:
        name='aurora-'+role
        created=ensure_agent(name,role,sha,creds)
        # Agent creation may have started an asynchronous build. Do not POST
        # model or MCP config while the platform holds the build lock.
        build_agent(name,sha,force=created)
        reconcile_with_build_lock(name,sha,'LLM configuration',
                                  operation=lambda: bind_llm(name))
        reconcile_with_build_lock(name,sha,'MCP tool configuration',
                                  operation=lambda: bind_tool(name))
        # Recheck a concurrent build before the deployment mutation.
        if not wait_for_existing_build(name,sha,discovery_timeout=0):
            fail(f'{name}: cannot confirm remote managed build before deployment; '
                 'no deployment requested')
        reconcile_with_build_lock(
            name,sha,'deployment',
            operation=lambda name=name: deploy_agent(
                name,{'USE_LLM_PROVIDER':'true','REQUIRE_GOVERNED_LLM':'true'}))
        provision_gateway_key(name)
    ensure_identity_roles()
    # Keys issued during specialist creation were not in the original snapshot.
    creds=service_secrets()
    created=ensure_agent('aurora-concierge','concierge',sha,creds)
    build_agent('aurora-concierge',sha,force=created)
    reconcile_with_build_lock('aurora-concierge',sha,'LLM configuration',
                              operation=lambda: bind_llm('aurora-concierge'))
    # Agent GET does not show secret bindings. Defer secret verification until
    # the deployed Concierge pod is active/Ready, then validate its effective
    # Secret references and in-process values. This also handles initial
    # deployments with no previous Running pod without guessing or writing keys.
    urlvars={'BANK_CORE_URL':CORE_SERVICE_URL,
             **{f'AGENT_{role.upper()}_URL':resolve_invoke_url('aurora-'+role)
                for role in ROLES}}
    # Configuration may have initiated another build. Never deploy during a
    # verified in-progress build and do not blindly replay rejected requests.
    if not wait_for_existing_build('aurora-concierge',sha,discovery_timeout=0):
        fail('aurora-concierge: cannot confirm remote managed build before deployment; '
             'no deployment requested')
    reconcile_with_build_lock(
        'aurora-concierge',sha,'deployment',
        operation=lambda: deploy_agent(
            'aurora-concierge',{**urlvars,'USE_LLM_PROVIDER':'true',
                                'REQUIRE_GOVERNED_LLM':'true'}))
    # Verify *after* deployment too; a successful deployment request cannot
    # prove the platform actually injected the credentials at runtime.
    verify_concierge_managed_secrets(creds, require_runtime=True)
    configure_portal()
    log('Six agents and Portal deployed; strict end-to-end verification remains.')


def check():
    verify_proxy()
    providers=llm_provider_inventory()
    if not any(isinstance(r,dict) and (r.get('id')==LLM_PROVIDER or r.get('name')==LLM_PROVIDER)
               and str(provider_template(r) or '').lower()=='openai' for r in providers):
        fail('OpenAI LLM provider missing; provider must exist before readiness checks')
    cmd(['kubectl','--context','k3d-amp-local','-n','aurora-demo','rollout','status',
         'deployment/aurora-db','--timeout=60s'])
    for n in ('aurora-core','aurora-mcp'):
        cmd(['kubectl','--context','k3d-amp-local','-n','aurora-demo',
             'rollout','status',f'deployment/{n}','--timeout=60s'])
    for role in AGENTS:
        name='aurora-'+role
        if agent_json(name) is None:fail(f'Agent not registered: {name}')
        p=cmd(['amctl','agent','status',name,'--project',PROJECT,'--org',ORG],optional=True)
        if p.returncode:fail(f'Cannot confirm managed deployment: {name}')
    ensure_identity_roles(read_only=True)
    # Explicitly avoid calling this full end-to-end until portal and live
    # gateway authorization tests have been configured and executed.
    portal=cmd(['kubectl','--context','k3d-amp-local','-n','aurora-demo','get',
                'deployment/aurora-portal'],optional=True)
    if portal.returncode:
        fail('Portal not deployed. Configure valid Concierge gateway credential and activate PORTAL_ENABLED=true; never claim complete end-to-end readiness.')
    verify_scenarios()
    # A real platform AgentID token is minted from the existing Account Pod's
    # injected Secret-backed identity, then the gateway MUST allow an account
    # balance read and deny a read-only Card tool with HTTP 403. No shell
    # command override or environment-only policy assertion can satisfy this.
    cmd([sys.executable,'scripts/verify-aurora-agentid-denial.py','--run'],timeout=240)
    log('All 6 scenarios and gateway-level deny test passed.')


def main():
    action=sys.argv[1] if len(sys.argv)>1 else 'check'
    if action=='prepare':prepare()
    elif action=='backend-fingerprint':print(backend_fingerprint())
    elif action=='agents':managed_agents()
    elif action=='check-proxy':verify_proxy()
    elif action=='check':check()
    elif action=='status':
        log('Read-only status. See amctl agent status / kubectl get pods for live details.')
        verify_proxy()
    else:fail(f'Unknown command {action}')


if __name__=='__main__':
    try:
        main()
    except Exception as exc:
        print(f'ERROR: {exc}',file=sys.stderr)
        sys.exit(1)
