#!/usr/bin/env python3
"""Banco Aurora: diagnose Portal -> Concierge chat without modifying infrastructure.

The default customer query is NON-TRANSACTIONAL; Concierge may persist workflow
state. No account/card/Pix mutation, simulator reset, or approval is requested.
"""
import argparse
import http.cookiejar
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parent.parent
SUPPORT = ROOT / 'agent-manager-support'
CTX = 'k3d-amp-local'
PORTAL_NS = 'aurora-demo'
DEPLOY_NS = 'dp-default-banco-aurora-default-990774f5'
READ_ONLY_MESSAGE = 'What is my checking account balance?'
S01_MESSAGE = "I don't recognize the R$1,280 Orion Digital charge. Block my card and open a dispute."


def run(args, timeout=30):
    return subprocess.run(args, cwd=ROOT, stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE, text=True, timeout=timeout)


def private_values(path):
    if not path.is_file():
        raise RuntimeError('agent-manager-support/secrets.env is missing')
    values = {}
    for line in path.read_text().splitlines():
        if '=' in line and not line.lstrip().startswith('#'):
            key, val = line.split('=', 1)
            key = key.strip()
            if key in values:
                raise RuntimeError('Duplicate private environment key: ' + key)
            values[key] = val.strip().strip('"').strip("'")
    return values


def safe_detail(body, private):
    """Only emit an error category and a short scrubbed FastAPI detail."""
    try:
        obj = json.loads(body.decode('utf8', 'replace'))
        detail = obj.get('detail', '') if isinstance(obj, dict) else ''
        if not isinstance(detail, str):
            detail = type(detail).__name__
    except (ValueError, UnicodeError):
        detail = '<non-JSON response>'
    for value in private.values():
        if len(value) >= 6 and value in detail:
            detail = detail.replace(value, '[REDACTED]')
    detail = re.sub(r'(?i)(bearer\s+|(?:api[_-]?key|token|secret)\s*[:=]\s*)\S+',
                    r'\1[REDACTED]', detail)
    return detail[:180]


def request(opener, method, url, private, payload=None, headers=None, timeout=12):
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data,
                                 headers={'Content-Type': 'application/json', **(headers or {})},
                                 method=method)
    try:
        with opener.open(req, timeout=timeout) as response:
            raw = response.read(131072)
            try:
                obj = json.loads(raw)
            except (ValueError, UnicodeError):
                obj = None
            return response.status, obj, ''
    except urllib.error.HTTPError as exc:
        return exc.code, None, safe_detail(exc.read(4096), private)
    except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as exc:
        return None, None, type(exc).__name__


def start_port_forward(timeout=30):
    log = tempfile.TemporaryFile(mode='w+b')
    proc = subprocess.Popen(['kubectl', '--context', CTX, '-n', PORTAL_NS,
        'port-forward', 'svc/aurora-portal', ':8300', '--address', '127.0.0.1'],
        stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=True)
    deadline = time.monotonic() + timeout
    try:
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                raise RuntimeError('kubectl port-forward exited; check namespace, Service, and cluster context')
            log.seek(0)
            found = re.findall(rb'(?m)^Forwarding from 127\.0\.0\.1:(\d+) -> 8300\r?$', log.read(131072))
            ports = {int(x) for x in found if 1 <= int(x) <= 65535}
            if len(ports) == 1:
                return proc, log, ports.pop()
            if len(ports) > 1:
                raise RuntimeError('kubectl reported ambiguous loopback ports')
            time.sleep(.2)
        raise RuntimeError('Timed out discovering assigned local port')
    except BaseException:
        terminate_forward(proc, log)
        raise


def terminate_forward(proc, log):
    if proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)
    log.close()


def find_portal_pod():
    p = run(['kubectl', '--context', CTX, '-n', PORTAL_NS,
             'get', 'pods', '-o', 'json'])
    if p.returncode:
        raise RuntimeError('Cannot read Portal pod inventory (kubectl failed)')
    try:
        items = json.loads(p.stdout)['items']
    except (KeyError, ValueError, TypeError):
        raise RuntimeError('Unexpected Kubernetes Portal pod inventory')
    matches = []
    for pod in items:
        meta, status = pod.get('metadata') or {}, pod.get('status') or {}
        name = meta.get('name', '')
        if (not name.startswith('aurora-portal-') or meta.get('deletionTimestamp')
            or status.get('phase') != 'Running'):
            continue
        containers = pod.get('spec', {}).get('containers') or []
        if len(containers) == 1 and status.get('containerStatuses') and all(
                s.get('ready') for s in status['containerStatuses']):
            matches.append((name, containers[0]['name']))
    if len(matches) != 1:
        raise RuntimeError(f'Expected one Ready Portal pod, found {len(matches)}')
    return matches[0]


# Executes ONLY in the existing Portal pod. Reads keys from process environment,
# never puts credential values in subprocess argv, stdout or a file.
POD_PROBE = r'''
import json,os,uuid,httpx
from urllib.parse import urlsplit
url=os.getenv('CONCIERGE_URL','').rstrip('/')
key=os.getenv('CONCIERGE_API_KEY','')
call_key=os.getenv('CONCIERGE_CALL_KEY','')
result={'status':None,'error':'','agent':'','actions':[],'specialists':[], 'detail':''}
if not url or not key or not call_key:
    result['error']='Missing BFF secret configuration'
else:
    parsed=urlsplit(url)
    headers={'X-API-Key':key,'X-Agent-Call-Key':call_key}
    endpoint=url
    if parsed.hostname=='default-default.am-gateway.localhost' and parsed.scheme=='http' and parsed.port==19080:
        endpoint='http://gateway-default.openchoreo-data-plane.svc.cluster.local:19080'+parsed.path.rstrip('/')
        headers['Host']=parsed.netloc
    payload={'customer_id':'C001','workflow_id':uuid.uuid4().hex,
        'language':'en','message':'What is my checking account balance?',
        'history':[]}
    try:
        with httpx.Client(timeout=75,trust_env=False) as client:
            res=client.post(endpoint+'/chat',json=payload,headers=headers)
            result['status']=res.status_code
            try:
                value=res.json()
                if isinstance(value,dict):
                    result['agent']=value.get('agent','')
                    result['actions']=[a.get('action') for a in value.get('next_actions',[]) if isinstance(a,dict)]
                    data=value.get('data') or {}
                    result['specialists']=data.get('invoked_agents',[]) if isinstance(data,dict) else []
                    detail=value.get('detail','')
                    if isinstance(detail,str):
                        if 'Governed model unavailable' in detail:
                            result['detail']='Governed model unavailable'
                        elif 'Banking execution unavailable' in detail:
                            result['detail']='Banking execution unavailable'
                        elif 'Banking assistant is temporarily unavailable' in detail:
                            result['detail']='Banking assistant unavailable'
                        else:
                            result['detail']='<redacted backend error>'
            except ValueError:
                result['detail']='<non-JSON response>'
    except Exception as exc:
        result['error']=type(exc).__name__
# Only whitelist fields; never output complete response (could contain customer data).
result['detail']=result['detail'][:120] if isinstance(result['detail'],str) else ''
print('AURORA_CHAT_DIAG='+json.dumps(result,sort_keys=True))
'''


def direct_gateway_probe():
    pod, container = find_portal_pod()
    p = run(['kubectl','--context',CTX,'-n',PORTAL_NS,'exec','pod/'+pod,
            '-c',container,'--','python','-c',POD_PROBE], timeout=100)
    if p.returncode:
        return {'status':None,'error':'Portal pod diagnostic exec failed (no credentials exposed)'}
    for line in reversed(p.stdout.splitlines()):
        if line.startswith('AURORA_CHAT_DIAG='):
            try:
                return json.loads(line[len('AURORA_CHAT_DIAG='):])
            except ValueError:
                break
    return {'status':None,'error':'Portal pod did not return recognized diagnostic JSON'}


def format_result(label, status, detail='', obj=None):
    print(f'{label}: HTTP {status if status is not None else "no response"}' +
          (f' ({detail})' if detail else ''))
    if isinstance(obj, dict) and status == 200:
        print('  agent=', str(obj.get('agent','?'))[:30],
              ' actions=', [x.get('action') for x in obj.get('next_actions', [])
                            if isinstance(x,dict)],
              ' specialists=', (obj.get('data') or {}).get('invoked_agents', []))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--s01', action='store_true', help='Reproduce first write-intent prompt WITHOUT approving it or resetting data; creates pending approvals')
    parser.add_argument('--skip-direct', action='store_true', help='Skip direct in-pod Concierge check after a failed Portal chat')
    args = parser.parse_args()
    private = private_values(SUPPORT/'secrets.env')
    code = private.get('DEMO_ACCESS_CODE') or os.getenv('DEMO_ACCESS_CODE')
    if not code:
        raise RuntimeError('DEMO_ACCESS_CODE missing from private secrets.env or environment')
    proc, log, port = start_port_forward()
    try:
        origin = f'http://127.0.0.1:{port}'
        print('Owned loopback port-forward active (remote Portal Service port 8300).')
        opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
        status, login, detail = request(opener, 'POST', origin+'/api/login', private,
                                        payload={'access_code':code})
        format_result('Portal login', status, detail)
        if status != 200 or not isinstance(login,dict) or not login.get('csrf_token'):
            print('STOP: Portal login not successful; no chat request made.')
            return 2
        headers = {'X-CSRF-Token':login['csrf_token']}
        status, state, detail = request(opener, 'GET', origin+'/api/state', private)
        format_result('Portal banking state GET', status, detail)
        if status != 200:
            print('STOP: Core read path unhealthy; no chat request made.')
            return 2
        if args.s01:
            print('S01 mode: creates pending approvals but does NOT approve or reset anything.')
        message = S01_MESSAGE if args.s01 else READ_ONLY_MESSAGE
        status, chat, detail = request(opener,'POST',origin+'/api/chat',private,
            payload={'message':message,'language':'en'},headers=headers,timeout=100)
        format_result('Portal POST /api/chat', status, detail, chat)
        if status != 200 and not args.skip_direct:
            print('Checking Concierge directly FROM the Portal pod, using existing pod-side credentials...')
            result = direct_gateway_probe()
            format_result('Direct Portal-pod to Concierge POST /chat',
                          result.get('status'), result.get('detail') or result.get('error'))
            print('  gateway agent=',result.get('agent') or '?',
                  ' actions=',result.get('actions',[]),
                  ' specialists=',result.get('specialists',[]))
        if status == 200:
            print('DIAGNOSIS: Non-transactional Portal chat succeeded.' if not args.s01 else
                  'DIAGNOSIS: Portal S01 chat returned HTTP 200. Inspect pending action types above.')
            print('If S01-S06 still fail, run --s01 only if pending-approval side effects are acceptable.')
            return 0
        print('DIAGNOSIS: Portal chat failed. Compare direct Concierge result to isolate BFF vs gateway/agent.')
        print('Review sanitized Kubernetes logs for the matching request; do not paste secrets or raw env.')
        return 1
    finally:
        terminate_forward(proc, log)


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (OSError,RuntimeError,ValueError,subprocess.TimeoutExpired) as exc:
        print('DIAGNOSTIC FAILED: '+str(exc),file=sys.stderr)
        sys.exit(2)
