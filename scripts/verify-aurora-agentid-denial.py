#!/usr/bin/env python3
"""Real, read-only native AgentID negative authorization proof for Banco Aurora.

Uses the *real* injected aurora-account client credentials to mint an OAuth
client_credentials token, then executes allowed get_account_balance and denied
get_card_status through the actual identity-secured MCP Gateway. A pass requires
HTTP 403 from the unauthorized tools/call, not an in-app assertion or 401.

--run attaches a temporary Python debug container to the existing Account Pod.
Values/tokens are passed by stdin only and never logged, stored or put in argv.
The ephemeral container record persists until replacement of the Account Pod.
"""
import argparse
import base64
import binascii
import json
import os
import re
import subprocess
import sys
import time
from urllib.parse import urlsplit

CTX = 'k3d-amp-local'
IMAGE = 'python:3.12-alpine'
REQ = ('AMP_AGENTID_CLIENT_ID', 'AMP_AGENTID_CLIENT_SECRET',
       'AMP_AGENTID_TOKEN_ENDPOINT', 'AMP_AGENTID_SCOPES', 'MCP_SERVER_URL')

class ProofError(RuntimeError): pass


def kubectl(args, *, stdin=None, timeout=40):
    try:
        return subprocess.run(['kubectl','--context',CTX,*args], input=stdin,
                              text=True, capture_output=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired):
        raise ProofError('Kubernetes query, stream or debug operation timed out/unavailable') from None


def kube_json(args):
    p = kubectl(args+['-o','json'])
    if p.returncode:
        raise ProofError('Kubernetes read failed; inspect RBAC and resource existence (raw output withheld)')
    try:
        data=json.loads(p.stdout)
    except ValueError:
        raise ProofError('Kubernetes returned malformed JSON') from None
    if not isinstance(data, dict):
        raise ProofError('Kubernetes returned unexpected resource layout')
    return data


def account_pod(pods):
    matches=[]
    for pod in pods.get('items', []):
        meta=pod.get('metadata') or {}
        status=pod.get('status') or {}
        name=meta.get('name','')
        namespace=meta.get('namespace','')
        if not (isinstance(name,str) and name.startswith('aurora-account-') and
                isinstance(namespace,str) and namespace.startswith('dp-default-banco-aurora-') and
                status.get('phase')=='Running' and not meta.get('deletionTimestamp')):
            continue
        containers=[x for x in (pod.get('spec') or {}).get('containers',[]) if x.get('name')=='main']
        ready=[x for x in status.get('containerStatuses',[]) if x.get('name')=='main' and x.get('ready') is True]
        if len(containers)==1 and len(ready)==1:
            matches.append((namespace,name,containers[0],pod))
    if len(matches)!=1:
        raise ProofError('Expected exactly one running, Ready aurora-account main container')
    return matches[0]


def read_env(namespace, container, key):
    """Read the injected environment only from existing workload references.

    The credential is accepted only from Kubernetes Secrets; config fields
    may be plain or ConfigMap references, which mirrors Agent Manager v1.0.0.
    """
    sensitive = key=='AMP_AGENTID_CLIENT_SECRET'
    found=[]
    explicit=[x for x in container.get('env',[]) if x.get('name')==key]
    if len(explicit)>1:
        raise ProofError('Ambiguous duplicate environment declaration: '+key)
    if explicit:
        entries=explicit
    else:
        entries=[]
        for e in container.get('envFrom',[]):
            prefix=e.get('prefix','')
            if not key.startswith(prefix):
                continue
            unprefixed=key[len(prefix):]
            for srkind,resource in (('secretRef','secret'),('configMapRef','configmap')):
                sr=e.get(srkind)
                if not isinstance(sr,dict) or not sr.get('name'):
                    continue
                obj=kube_json(['-n',namespace,'get',resource,sr['name']])
                source=(obj.get('data') or {}).get(unprefixed)
                if source is None:
                    continue
                if sensitive and resource!='secret':
                    raise ProofError('AgentID client secret is not stored in a Kubernetes Secret')
                if resource=='secret':
                    try:
                        source=base64.b64decode(source,validate=True).decode('utf-8')
                    except (binascii.Error, UnicodeError, ValueError):
                        raise ProofError('Malformed AgentID Secret data') from None
                found.append(source)
        if len(found)!=1:
            raise ProofError('Missing or ambiguous injected environment value: '+key)
        return found[0]
    entry=entries[0]
    if 'value' in entry:
        if sensitive:
            raise ProofError('AgentID client secret is exposed as plaintext Pod spec env')
        return entry['value']
    vf=entry.get('valueFrom') or {}
    src=vf.get('secretKeyRef')
    resource='secret'
    if src is None:
        src=vf.get('configMapKeyRef'); resource='configmap'
    if not isinstance(src,dict) or not src.get('name') or not src.get('key'):
        raise ProofError('Unrecognized AgentID env source: '+key)
    if sensitive and resource!='secret':
        raise ProofError('AgentID client secret must be Secret-backed')
    obj=kube_json(['-n',namespace,'get',resource,src['name']])
    val=(obj.get('data') or {}).get(src['key'])
    if not isinstance(val,str):
        raise ProofError('Injected environment reference key missing: '+key)
    if resource=='secret':
        try:
            val=base64.b64decode(val,validate=True).decode('utf-8')
        except (binascii.Error,UnicodeError,ValueError):
            raise ProofError('Malformed Kubernetes Secret value') from None
    return val


def verified_mcp_url(value):
    u=urlsplit(value)
    if (u.scheme not in ('http','https') or u.username or u.password or u.query or
        u.fragment or not u.hostname or not u.path.rstrip('/').endswith('/aurora-banking/mcp')):
        raise ProofError('MCP gateway URL is invalid or does not identify aurora-banking')
    # Value comes only from trusted injected environment, but refuse arbitrary
    # public or private destinations for this security diagnostic.
    if not (u.hostname.endswith('.svc.cluster.local') or
            u.hostname == 'default-default.gateway.localhost'):
        raise ProofError('Unrecognized in-cluster MCP gateway hostname')
    return value


def safe_k8s_error(p):
    message=(p.stderr+' '+p.stdout).lower()
    if any(s in message for s in ('forbidden', 'unauthorized', 'permission denied')):
        return 'Kubernetes RBAC/admission denied'
    if any(s in message for s in ('pull','imagepullbackoff','errimagepull')):
        return 'Ephemeral diagnostic image unavailable'
    return 'Kubernetes debug/exec operation failed (raw output withheld)'


def start_ephemeral(namespace, pod):
    # A warm verification may reuse an existing, running diagnostic container.
    # Never attach to an unrelated workload/debug image.
    current=kube_json(['-n',namespace,'get','pod',pod])
    expected={x.get('name') for x in (current.get('spec') or {}).get('ephemeralContainers',[])
              if x.get('image')==IMAGE and str(x.get('name','')).startswith('aurora-authz-')}
    ready=[x.get('name') for x in (current.get('status') or {}).get('ephemeralContainerStatuses',[])
           if x.get('name') in expected and (x.get('state') or {}).get('running') is not None]
    if len(ready)==1:
        return ready[0]
    name='aurora-authz-'+str(int(time.time()))+'-'+str(os.getpid()%10000)
    p=kubectl(['-n',namespace,'debug','pod/'+pod,
               '--container='+name,'--target=main','--image='+IMAGE,
               '--attach=false','--','python3','-c','import time;time.sleep(420)'],timeout=70)
    if p.returncode:
        raise ProofError(safe_k8s_error(p))
    for _ in range(24):
        obj=kube_json(['-n',namespace,'get','pod',pod])
        for item in (obj.get('status') or {}).get('ephemeralContainerStatuses',[]):
            if item.get('name') != name:continue
            if (item.get('state') or {}).get('running') is not None:
                return name
            if (item.get('state') or {}).get('terminated') is not None:
                raise ProofError('Temporary security diagnostic container terminated')
        time.sleep(2)
    raise ProofError('Ephemeral security diagnostic container did not become Ready')


WORKER = r'''
import base64, json, sys
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, build_opener, ProxyHandler
from urllib.error import HTTPError, URLError

cfg=json.loads(sys.stdin.read(65536))

def result(stage, status):
    print(json.dumps({'stage':stage,'http':status}))
    sys.exit(0)

# Trust only already Kubernetes-injected values validated by the host runner.
identity=cfg['identity']
token_url=identity['AMP_AGENTID_TOKEN_ENDPOINT']
mcp=identity['MCP_SERVER_URL']
scopes=identity['AMP_AGENTID_SCOPES']
for url in (token_url,mcp):
    parsed=urlsplit(url)
    if (parsed.scheme not in ('http','https') or not parsed.hostname or
        parsed.username or parsed.password or parsed.query or parsed.fragment):
        result('invalid_url',0)

opener=build_opener(ProxyHandler({}))
credentials=(identity['AMP_AGENTID_CLIENT_ID']+':'+identity['AMP_AGENTID_CLIENT_SECRET']).encode()
auth='Basic '+base64.b64encode(credentials).decode('ascii')
form=urlencode({'grant_type':'client_credentials','scope':scopes,'resource':mcp}).encode()
req=Request(token_url,data=form,headers={'Authorization':auth,'Content-Type':'application/x-www-form-urlencoded'},method='POST')
try:
    with opener.open(req,timeout=20) as r:
        token_result=json.loads(r.read(65536))
except HTTPError as e:
    result('token',e.code)
except (URLError,OSError,ValueError):
    result('token_transport',0)
token=token_result.get('access_token')
if not isinstance(token,str) or not token:
    result('token_invalid',0)

# Require granted scope evidence in the freshly minted token. Thunder usually
# issues JWTs. An opaque token is not sufficient for this strict proof.
try:
    parts=token.split('.')
    claims=json.loads(base64.urlsafe_b64decode(parts[1]+'='*(-len(parts[1])%4)))
    claimed=claims.get('scope',claims.get('scp'))
    token_scopes=set(claimed.split() if isinstance(claimed,str) else claimed)
except Exception:
    result('token_scope_unreadable',0)
if 'aurora-banking:balance-read' not in token_scopes:
    result('required_scope_missing',0)
if 'aurora-banking:card-read' in token_scopes:
    result('forbidden_scope_present',0)

headers={'Authorization':'Bearer '+token,'Accept':'application/json, text/event-stream',
         'Content-Type':'application/json','MCP-Protocol-Version':'2025-06-18'}

def rpc(data):
    req=Request(mcp,data=json.dumps(data).encode(),headers=headers,method='POST')
    try:
        with opener.open(req,timeout=35) as r:
            raw=r.read(65536)
            session=r.headers.get('Mcp-Session-Id')
            if session:
                headers['Mcp-Session-Id']=session
            return r.status,raw
    except HTTPError as e:
        e.close()
        return e.code,b''
    except (URLError,OSError):
        return 0,b''

def decoded(raw):
    try:
        text=raw.decode('utf-8')
        if text.startswith('data:') or '\ndata:' in text:
            data=[ln[5:].strip() for ln in text.splitlines() if ln.startswith('data:')]
            return json.loads(data[-1]) if data else None
        return json.loads(text)
    except (ValueError,UnicodeDecodeError):
        return None

status,body=rpc({'jsonrpc':'2.0','id':1,'method':'initialize','params':
                 {'protocolVersion':'2025-06-18','capabilities':{},
                  'clientInfo':{'name':'aurora-native-authz-proof','version':'1.0'}}})
if status!=200 or not isinstance(decoded(body),dict):
    result('initialize',status)
status,_=rpc({'jsonrpc':'2.0','method':'notifications/initialized'})
if status not in (200,202,204):
    result('initialized_notification',status)
status,body=rpc({'jsonrpc':'2.0','id':2,'method':'tools/call','params':
                 {'name':'get_account_balance','arguments':{'customer_id':'C001'}}})
answer=decoded(body)
if status!=200 or not isinstance(answer,dict) or not isinstance(answer.get('result'),dict):
    result('allowed_read',status)
if answer['result'].get('isError') is True:
    result('allowed_tool_error',status)
status,_=rpc({'jsonrpc':'2.0','id':3,'method':'tools/call','params':
               {'name':'get_card_status','arguments':{'customer_id':'C001'}}})
result('forbidden_tool',status)
'''


def validate_proof(data):
    if not isinstance(data,dict) or data.get('stage') not in ('invalid_url','token','token_transport',
      'token_invalid','token_scope_unreadable','required_scope_missing','forbidden_scope_present',
      'initialize','initialized_notification','allowed_read','allowed_tool_error','forbidden_tool'):
        raise ProofError('Malformed security proof result')
    if type(data.get('http')) is not int or not 0<=data['http']<=599:
        raise ProofError('Malformed HTTP status in security proof')
    if data['stage']!='forbidden_tool' or data['http']!=403:
        raise ProofError('Gateway deny not proven: stage='+data['stage']+', HTTP='+str(data['http'])+' (expected forbidden_tool HTTP=403)')
    return True


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run',action='store_true',help='execute OAuth and real gateway positive/negative read-only calls')
    args=parser.parse_args(argv)
    try:
        namespace,pod,container,_=account_pod(kube_json(['get','pods','-A']))
        env={key:read_env(namespace,container,key) for key in REQ}
        verified_mcp_url(env['MCP_SERVER_URL'])
        if any(not isinstance(v,str) or not v for v in env.values()):
            raise ProofError('Required injected AgentID environment configuration missing')
        print('AgentID Account: Ready; OAuth client secret Secret-backed; MCP gateway URL verified. No values printed.')
        if not args.run:
            print('READ-ONLY: use --run to mint an AgentID token and test gateway tools; no financial writes.')
            return 0
        print('Running real AgentID positive balance-read and negative card-read proof (read-only tools).')
        debug=start_ephemeral(namespace,pod)
        payload=json.dumps({'identity':env},separators=(',',':'))
        if len(payload)>65536:
            raise ProofError('Security proof payload exceeds limit')
        proc=kubectl(['-n',namespace,'exec','-i',pod,'-c',debug,'--','python3','-c',WORKER],
                     stdin=payload,timeout=115)
        if proc.returncode:
            raise ProofError(safe_k8s_error(proc))
        try:
            result=json.loads(proc.stdout.strip())
        except ValueError:
            raise ProofError('Malformed security proof response (raw content suppressed)') from None
        validate_proof(result)
        print('AGENTID_GATEWAY_DENIAL=verified: valid Account identity, permitted balance tool, forbidden card tool HTTP=403.')
        print('No customer write, transaction execution, key rotation, model call, or workload redeploy performed.')
        return 0
    except ProofError as exc:
        print('AGENTID_GATEWAY_DENIAL=not_verified: '+str(exc),file=sys.stderr)
        return 1

if __name__=='__main__':
    sys.exit(main())
