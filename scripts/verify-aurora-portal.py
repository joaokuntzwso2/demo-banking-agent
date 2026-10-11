#!/usr/bin/env python3
"""Read-only live verification: Portal BFF secret wiring and Concierge gateway reachability.

Never prints API keys, never reads Kubernetes Secret values, and never changes
Kubernetes resources. Run AFTER managed Concierge and Portal are deployed.
"""
import argparse
import hashlib
import hmac
import json
import pathlib
import secrets
import subprocess
import sys

ROOT=pathlib.Path(__file__).resolve().parent.parent
SUPPORT=ROOT/'agent-manager-support'
NAMESPACE='aurora-demo'
CONTEXT='k3d-amp-local'


def run(args,*,timeout=60):
    p=subprocess.run(args,cwd=ROOT,capture_output=True,text=True,timeout=timeout)
    if p.returncode:
        # Avoid dumping arbitrary CLI output that might include environment data.
        raise RuntimeError('Read-only command failed: '+ ' '.join(args[:5]) +
                           f' (exit {p.returncode}); inspect Kubernetes portal status')
    return p.stdout


def values(path):
    out={}
    for line in path.read_text().splitlines():
        if '=' in line and not line.lstrip().startswith('#'):
            k,v=line.split('=',1)
            if k in out:
                raise RuntimeError('Duplicate private configuration field: '+k)
            out[k]=v.strip().strip('"')
    return out


def pick_portal_pod(payload):
    items=payload.get('items')
    if not isinstance(items,list):
        raise RuntimeError('Kubernetes returned an invalid pod inventory')
    matches=[]
    for pod in items:
        meta=pod.get('metadata') or {}
        if not meta.get('name','').startswith('aurora-portal-') or meta.get('deletionTimestamp'):
            continue
        state=pod.get('status') or {}
        if state.get('phase') != 'Running':
            continue
        if not any(c.get('type')=='Ready' and c.get('status')=='True'
                   for c in state.get('conditions',[])):
            continue
        containers=(pod.get('spec') or {}).get('containers') or []
        statuses=state.get('containerStatuses') or []
        if len(containers) != 1 or len(statuses) != 1 or not statuses[0].get('ready'):
            continue
        matches.append((meta['name'],containers[0]['name']))
    if len(matches)!=1:
        raise RuntimeError(f'Expected one Ready Portal pod, found {len(matches)}; '
                           'inspect rollout before verifying customer-facing UI')
    return matches[0]


# This source code is supplied to `kubectl exec -- python -c`. It only reads
# the Portal process environment, reports keyed digests, and probes its private
# Gateway route. It never sends credentials to stdout, CLI args, or the browser.
IN_POD=r'''
import os,sys,json,hmac,hashlib
from urllib.parse import urlsplit
import httpx
nonce=bytes.fromhex(sys.argv[1])
def proof(name):
    val=os.environ.get(name,'')
    return hmac.new(nonce,val.encode(),hashlib.sha256).hexdigest() if val else ''
url=os.environ.get('CONCIERGE_URL','').rstrip('/')
key=os.environ.get('CONCIERGE_API_KEY','')
result={'url_proof':proof('CONCIERGE_URL'),'key_proof':proof('CONCIERGE_API_KEY'),
        'wso2_mode':os.environ.get('WSO2_MODE','').lower()=='true',
        'provider_key_in_portal':any(os.environ.get(x) for x in ('OPENAI_API_KEY','LLM_PROVIDER_KEY')),
        'gateway_http_status':None,'gateway_error':''}
if url and key:
    parsed=urlsplit(url)
    headers={'X-API-Key':key}
    target=url
    if parsed.hostname=='default-default.am-gateway.localhost' and parsed.scheme=='http' and parsed.port==19080:
        if parsed.path.rstrip('/')!='/aurora-concierge':
            result['gateway_error']='unexpected concierge endpoint path'
        else:
            target='http://gateway-default.openchoreo-data-plane.svc.cluster.local:19080'+parsed.path.rstrip('/')
            headers['Host']=parsed.netloc
    try:
        if not result['gateway_error']:
            with httpx.Client(timeout=12,trust_env=False,follow_redirects=False) as client:
                response=client.get(target+'/openapi.json',headers=headers)
                result['gateway_http_status']=response.status_code
    except Exception as exc:
        result['gateway_error']=type(exc).__name__
print(json.dumps(result,sort_keys=True))
'''


def audit_payload(payload, local, nonce):
    if payload.get('provider_key_in_portal'):
        raise RuntimeError('The Portal contains an upstream OpenAI provider key. Remove it from the BFF; only Agent Manager should own that credential.')
    if payload.get('wso2_mode') is not True:
        raise RuntimeError('Portal WSO2_MODE is not true')
    for field, reported in (('CONCIERGE_URL','url_proof'),('CONCIERGE_API_KEY','key_proof')):
        expected=local.get(field,'')
        if not expected:
            raise RuntimeError('Missing '+field+' in private agent-manager-support/secrets.env')
        digest=hmac.new(nonce,expected.encode(),hashlib.sha256).hexdigest()
        if not hmac.compare_digest(payload.get(reported,'') or '',digest):
            raise RuntimeError('Portal running environment does not match private '+field+ '; rerun portal reconciliation')
    if payload.get('gateway_http_status')!=200:
        raise RuntimeError('Portal cannot reach authenticated Concierge OpenAPI via its gateway '
                           f'(HTTP {payload.get("gateway_http_status")}, error={payload.get("gateway_error")!r}). '
                           'Check service routing, Host header and in-cluster DNS; do not rotate keys.')


def audit():
    private=SUPPORT/'secrets.env'
    if not private.is_file():
        raise RuntimeError('Private support/secrets.env missing; provision Concierge and Portal first')
    local=values(private)
    if not local.get('CONCIERGE_URL') or not local.get('CONCIERGE_API_KEY'):
        raise RuntimeError('Concierge gateway URL/key not yet provisioned in private secrets.env')
    # Verify that the BFF references the ACTUAL active Concierge deployment,
    # not merely a locally persisted URL from an old or failed rollout.
    from aurora_runtime_contract import invoke_url
    deployment=json.loads(run(['amctl','api',
        '/orgs/default/projects/banco-aurora/agents/aurora-concierge/deployments']))
    expected=invoke_url(deployment,'aurora-concierge','default',require_active=True)
    if local['CONCIERGE_URL'].rstrip('/') != expected:
        raise RuntimeError('Portal private Concierge URL differs from current active Agent Manager deployment')
    payload=json.loads(run(['kubectl','--context',CONTEXT,'-n',NAMESPACE,'get','pods','-o','json']))
    pod,container=pick_portal_pod(payload)
    nonce=secrets.token_bytes(32)
    script_output=run(['kubectl','--context',CONTEXT,'-n',NAMESPACE,'exec','pod/'+pod,
                       '-c',container,'--','python','-c',IN_POD,nonce.hex()],timeout=40)
    try:
        # AMP instrumentation can print non-secret init banners before the
        # final JSON result of an exec'd Python process.
        result=json.loads(script_output.strip().splitlines()[-1])
    except (json.JSONDecodeError,IndexError) as exc:
        raise RuntimeError('Portal returned unexpected verification output') from exc
    audit_payload(result,local,nonce)
    print('VERIFIED: Portal has private Concierge URL and gateway key; upstream OpenAI key is absent; authenticated gateway is reachable via HTTP 200.')

if __name__=='__main__':
    try:
        argparse.ArgumentParser(description=__doc__).parse_args()
        audit()
    except (OSError,RuntimeError,subprocess.TimeoutExpired,ValueError) as exc:
        print('PORTAL VERIFICATION FAILED: '+str(exc),file=sys.stderr)
        sys.exit(1)
