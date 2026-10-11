#!/usr/bin/env python3
"""Compare Concierge's governed LLM HTTP access from its Kubernetes Pod network.

Default: read-only preflight.  --smoke: adds one official-Python ephemeral
container to the *existing Concierge Pod* (shared network namespace), then
sends one small billable model request using the already bound Kubernetes
Secret. No agent changes, provider changes, key rotation, NetworkPolicies or
repository modifications. No credential, endpoint URL, response body or raw
kubectl output is printed.

The diagnostic container is NOT the main image and does NOT inherit main's
proxy environment or Python SDK. HTTP 200 proves Pod-level network/auth path,
not the exact AsyncOpenAI client behavior. The ephemeral container record
persists until the Pod is replaced.
"""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
from urllib.parse import urlsplit

IMAGE = 'python:3.12-alpine'
MODEL_PROMPT = ('Return ONLY a JSON list of banking specialist roles: '
                'account,card,dispute,pix,wellness,risk (if available). '
                'Choose relevant independent specialists; never authorize financial operations. '
                'A request to speak to a person requires dispute. '
                'Hypothetical transfers require wellness, not Pix.')

class DiagnosticError(RuntimeError):
    pass


def load_module(path, name):
    if not path.is_file():
        raise DiagnosticError('Required earlier LLM diagnostics are missing; install v1 and v2.1 first')
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def kargs(mod, args, timeout=30, stdin=None):
    """Never pass payload or credentials as CLI args or subprocess env."""
    try:
        proc = subprocess.run(['kubectl', '--context', mod.CTX, *args],
                              input=stdin, capture_output=True, text=True, timeout=timeout,
                              check=False)
    except (subprocess.TimeoutExpired, OSError):
        raise DiagnosticError('Kubernetes diagnostic command unavailable or timed out') from None
    return proc


def safe_error(proc, scope):
    # Do not print raw stdout/stderr: admission and infrastructure messages may
    # disclose internal URLs or credentials.
    message = (proc.stderr + '\n' + proc.stdout).lower()
    if any(s in message for s in ('forbidden', 'unauthorized', 'permission denied')):
        return scope + ': Kubernetes RBAC/admission denied'
    if any(s in message for s in ('pull', 'image', 'back-off', 'imagepullbackoff')):
        return scope + ': debug image unavailable or blocked by image policy'
    if any(s in message for s in ('not found', 'notfound', 'does not exist')):
        return scope + ': target Pod or executable missing'
    if any(s in message for s in ('unable to upgrade', 'upgrade request')):
        return scope + ': kubectl exec streaming unavailable'
    return scope + ': Kubernetes operation failed; inspect local kubectl access'


def create_ephemeral(mod, namespace, pod_name):
    name = 'aurora-llm-http-' + str(int(time.time())) + '-' + str(os.getpid() % 10000)
    args = ['-n', namespace, 'debug', 'pod/' + pod_name,
            '--container=' + name, '--target=main', '--image=' + IMAGE,
            '--attach=false', '--', 'python3', '-c',
            'import time;time.sleep(420)']
    proc = kargs(mod, args, timeout=60)
    if proc.returncode:
        raise DiagnosticError(safe_error(proc, 'Ephemeral debug creation'))
    for _ in range(24):
        pod = mod.kget(['-n', namespace, 'get', 'pod', pod_name])
        statuses = pod.get('status', {}).get('ephemeralContainerStatuses', [])
        for state in statuses:
            if state.get('name') != name:
                continue
            if state.get('state', {}).get('running') is not None:
                return name
            if state.get('state', {}).get('terminated') is not None:
                raise DiagnosticError('Temporary diagnostic container terminated; check image/runtime policy')
            waiting = state.get('state', {}).get('waiting', {})
            if waiting.get('reason') in ('ImagePullBackOff', 'ErrImagePull', 'CreateContainerConfigError'):
                raise DiagnosticError('Temporary diagnostic image unavailable; no change to main container')
        time.sleep(2)
    raise DiagnosticError('Temporary diagnostic container did not start; Concierge remains unchanged')


# The worker is run using python3 -c in a separate, already running ephemeral
# container. URL and API key are fed via stdin over Kubernetes exec, NEVER as
# command-line args or environment variables, and are never printed.
WORKER = r'''
import json, socket, sys
from urllib.request import Request, build_opener, ProxyHandler
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
raw = json.loads(sys.stdin.read(32768))
u = urlsplit(raw['url'])
if (u.scheme != 'http' or not u.hostname or u.username or u.password or
    u.query or u.fragment or not u.hostname.endswith(('.svc.cluster.local', '.default-default'))):
    print(json.dumps({'kind': 'bad_config'})); sys.exit(0)
path = u.path.rstrip('/') + '/chat/completions'
if not path.startswith('/') or '//' in path:
    print(json.dumps({'kind': 'bad_config'})); sys.exit(0)
payload = json.dumps({'model': raw['model'], 'temperature': 0,
    'messages': [{'role': 'system', 'content': raw['system']},
                 {'role': 'user', 'content': 'hello'}]}).encode()
req = Request('http://' + u.netloc + path, data=payload,
    headers={'API-Key': raw['key'], 'Authorization': '',
             'Accept': 'application/json', 'Content-Type': 'application/json'},
    method='POST')
try:
    with build_opener(ProxyHandler({})).open(req, timeout=25) as response:
        code = response.status
        data = response.read(65536) if code == 200 else b''
        valid = False
        if code == 200:
            try:
                obj = json.loads(data)
                valid = isinstance(obj.get('choices'), list) and len(obj['choices']) > 0
            except (ValueError, TypeError, AttributeError):
                pass
        print(json.dumps({'kind': 'http', 'status': code, 'valid': valid}))
except HTTPError as ex:
    print(json.dumps({'kind': 'http', 'status': ex.code, 'valid': False}))
    ex.close()
except (URLError, TimeoutError, OSError) as ex:
    reason = getattr(ex, 'reason', ex)
    klass = type(reason).__name__
    print(json.dumps({'kind': 'transport',
                      'class': klass if klass in
                        ('TimeoutError', 'ConnectionRefusedError', 'gaierror',
                         'ConnectionResetError', 'PermissionError', 'OSError') else 'Other'}))
except Exception:
    print(json.dumps({'kind': 'internal'}))
'''


def run_worker(mod, namespace, pod, container, payload):
    serialized = json.dumps(payload, separators=(',', ':'))
    if len(serialized) > 32768:
        raise DiagnosticError('Request exceeds diagnostic limit')
    args = ['-n', namespace, 'exec', '-i', pod, '-c', container,
            '--', 'python3', '-c', WORKER]
    proc = kargs(mod, args, timeout=50, stdin=serialized)
    # The credential was never in argv, on disk, or a pod environment variable.
    if proc.returncode:
        raise DiagnosticError(safe_error(proc, 'In-Pod governed LLM HTTP request'))
    try:
        result = json.loads(proc.stdout.strip())
    except ValueError:
        raise DiagnosticError('In-Pod diagnostic returned an invalid result (raw output suppressed)') from None
    return validate_result(result)


def validate_result(obj):
    if not isinstance(obj, dict):
        raise DiagnosticError('Unexpected In-Pod diagnostic response')
    kind = obj.get('kind')
    if kind == 'http':
        code = obj.get('status')
        if type(code) is not int or not 100 <= code <= 599 or type(obj.get('valid')) is not bool:
            raise DiagnosticError('Malformed In-Pod HTTP result')
        return {'kind': 'http', 'status': code, 'valid': obj['valid']}
    if kind == 'transport':
        klass = obj.get('class')
        if klass not in ('TimeoutError', 'ConnectionRefusedError', 'gaierror',
                         'ConnectionResetError', 'PermissionError', 'OSError', 'Other'):
            raise DiagnosticError('Malformed In-Pod transport result')
        return {'kind': 'transport', 'class': klass}
    if kind in ('bad_config', 'internal'):
        return {'kind': kind}
    raise DiagnosticError('Unrecognized In-Pod result; raw content suppressed')


def explain(result):
    if result['kind'] == 'http':
        code = result['status']
        if code == 200 and result['valid']:
            return 'In-Pod HTTP 200 with completion: pod network, route, and injected key work. Investigate Concierge process, SDK, proxy env, or payload differences.'
        if code == 200:
            return 'In-Pod HTTP 200 without usable completion: inspect model response compatibility/guardrail.'
        if code in (401, 403):
            return 'In-Pod authentication/authorization failed: check gateway policies or Host/header handling for the pod caller.'
        if code in (400, 422):
            return 'In-Pod payload/model rejected: compare Concierge planning prompts and gateway policy.'
        if code == 404:
            return 'In-Pod gateway route not found: check DNS name, path, and virtual host handling.'
        if code == 429:
            return 'In-Pod model quota or rate limiting.'
        if code in (500, 502, 503, 504):
            return 'In-Pod gateway/upstream failed: check gateway runtime logs for this call.'
        return 'In-Pod HTTP error; inspect gateway access log without revealing credentials.'
    if result['kind'] == 'transport':
        return 'In-Pod transport failure (' + result['class'] + '): verify DNS, policy and route to TCP/22893.'
    return 'In-Pod runtime diagnostic could not build/execute request; no agent changes.'


def audit_proxy_keys(mod, namespace, container):
    names = ('HTTP_PROXY','HTTPS_PROXY','ALL_PROXY','NO_PROXY',
             'http_proxy','https_proxy','all_proxy','no_proxy')
    present = []
    for n in names:
        try:
            v = mod.read_env(namespace, container, n)
        except mod.AuditError as ex:
            # Only absent is accepted. Other errors must not be ignored.
            if 'expected exactly one injected value, found 0' in str(ex):
                continue
            raise DiagnosticError('Cannot safely audit proxy setting ' + n) from None
        if isinstance(v, str) and v:
            present.append(n)
    return present


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--smoke', action='store_true',
                        help='One possibly billable LLM request from an ephemeral Python container on Concierge Pod network')
    args = parser.parse_args(argv)
    try:
        here = Path(__file__).resolve().parent
        mod = load_module(here / 'diagnose-aurora-llm-path.py', 'aurora_network_diag_v1')
        httpmod = load_module(here / 'diagnose-aurora-llm-http.py', 'aurora_http_diag_v21')
        namespace, podname, main_container, _ = mod.select_pod(mod.kget(['get', 'pods', '-A']))
        url = mod.read_env(namespace, main_container, 'LLM_PROVIDER_URL')
        host, port, scheme, _ = mod.endpoint_info(url)
        if scheme != 'http':
            raise DiagnosticError('Expected in-cluster managed HTTP provider URL')
        httpmod.find_runtime_service(mod, host, port, namespace)
        # Read-only presence check, no secret bytes yet.
        mod.read_env(namespace, main_container, 'LLM_PROVIDER_KEY', is_key=True)
        proxy_names = audit_proxy_keys(mod, namespace, main_container)
        print('Concierge: Ready; managed LLM endpoint matches a ready Kubernetes Service.')
        print('Proxy configuration variable names declared: ' +
              (', '.join(proxy_names) if proxy_names else 'none') + '. Values concealed.')
        if not args.smoke:
            print('READ-ONLY: no model request or Pod changes. Use --smoke for one In-Pod authenticated HTTP request.')
            print('NOTE: --smoke adds an ephemeral debug container record; it persists until Concierge Pod replacement.')
            return 0
        model = httpmod.choose_model(mod, namespace, main_container)
        key = httpmod.read_model_key(mod, namespace, main_container)
        print('Starting a temporary Python diagnostic container in the existing Concierge Pod network (potential model charge).')
        name = create_ephemeral(mod, namespace, podname)
        result = run_worker(mod, namespace, podname, name,
                            {'url': url, 'key': key, 'model': model, 'system': MODEL_PROMPT})
        if result['kind'] == 'http':
            print('CONCIERGE_POD_MODEL_HTTP=%d; completion_valid=%s.' %
                  (result['status'], str(result['valid']).lower()))
        elif result['kind'] == 'transport':
            print('CONCIERGE_POD_MODEL_TRANSPORT=%s.' % result['class'])
        else:
            print('CONCIERGE_POD_MODEL_DIAGNOSTIC=%s.' % result['kind'])
        print('DIAGNOSIS: ' + explain(result))
        print('No secret, full URL, response body, raw environment, or raw kubectl output printed. No managed workloads changed.')
        return 0 if result.get('status') == 200 and result.get('valid') else 2
    except (DiagnosticError, Exception) as e:
        msg = str(e) if isinstance(e, DiagnosticError) else 'unexpected ' + type(e).__name__ + ' (details suppressed)'
        print('NOT VERIFIED: ' + msg, file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
