#!/usr/bin/env python3
"""Read-only static and runtime-environment checks for Banco Aurora v4.

This does NOT provision Agent Manager, fetch secrets, or prove tool scopes.
Never prints secret values. Use --mode hosted-* only on a host with real vars.
"""
import argparse,os,sys,json,ast
from pathlib import Path
from urllib.parse import urlsplit

ROOT=Path(__file__).resolve().parents[1]
p=argparse.ArgumentParser()
p.add_argument('--mode',choices=['source','hosted-concierge','hosted-specialist','hosted-portal','hosted-upstream-mcp'],default='source')
p.add_argument('--env-file',help='Private KEY=VALUE file; ignored by Git. Do not provide env files containing shell expressions.')
a=p.parse_args()
e=dict(os.environ)
if a.env_file:
    f=Path(a.env_file)
    if not f.exists():p.error('Environment file not found')
    for line in f.read_text().splitlines():
        line=line.strip()
        if not line or line.startswith('#') or '=' not in line:continue
        k,v=line.split('=',1)
        e[k.strip()]=v.strip().strip('"').strip("'")
errors=[];warnings=[]
def bad(k):
    v=e.get(k,'').strip()
    return not v or any(m in v.upper() for m in ('REPLACE_','YOUR_','CHANGE_ME','TODO'))
def require(*keys):
    for k in keys:
        if bad(k):errors.append(f'{k}: not configured or placeholder')
def url(k,secure=False):
    v=e.get(k,'')
    if bad(k):return
    parts=urlsplit(v)
    if parts.scheme not in ('http','https') or not parts.hostname:
        errors.append(f'{k}: invalid absolute HTTP(S) URL')
    elif secure and parts.scheme!='https':
        warnings.append(f'{k}: HTTP, not TLS (only acceptable on trusted internal-only networks)')
if a.mode=='source':
    cases=json.loads((ROOT/'scenarios/catalog.json').read_text())
    if [d.get('id') for d in cases]!=[f'S{i:02}' for i in range(1,9)]:errors.append('Scenario catalog must contain S01–S08')
    source=(ROOT/'mcp_server/app.py').read_text()
    if source.count('@mcp.tool()')!=15:errors.append('Expected 15 banking MCP tools')
    for k in ('BANK_CASE_OPERATOR_KEY','DEMO_OPERATOR_CODE'):
        if k not in (ROOT/'deployment/agent-manager/k8s/supporting-services.yaml').read_text():errors.append(f'Missing Kubernetes secret {k}')
    for relative in ('agents/common.py','agents/concierge.py','agents/specialists.py','agents/retrieval.py','banking_common/telemetry.py'):
        ast.parse((ROOT/relative).read_text(),filename=relative)
    print('Source preflight: eight scenarios, 15 MCP tools, manifest and Python syntax inspected.')
elif a.mode=='hosted-portal':
    require('CONCIERGE_URL','CONCIERGE_API_KEY','BANK_CORE_URL','BANK_CORE_READ_KEY',
            'DEMO_SESSION_SIGNING_KEY','DEMO_ACCESS_CODE','DEMO_RESET_CODE',
            'BANK_CASE_OPERATOR_KEY','DEMO_OPERATOR_CODE','CONCIERGE_CALL_KEY','CONCIERGE_APPROVAL_KEY')
    for k in ('CONCIERGE_URL','BANK_CORE_URL'):url(k,k=='CONCIERGE_URL')
    if e.get('DEMO_SECURE_COOKIE','').lower()!='true':errors.append('DEMO_SECURE_COOKIE must be true on managed portal')
    if len(e.get('DEMO_SESSION_SIGNING_KEY',''))<32:errors.append('DEMO_SESSION_SIGNING_KEY must be at least 32 characters')
elif a.mode=='hosted-concierge':
    require('BANK_CORE_URL','BANK_CORE_READ_KEY','BANK_APPROVAL_KEY','CONCIERGE_CALL_KEY',
            'CONCIERGE_APPROVAL_KEY','SPECIALIST_CALL_KEY','SPECIALIST_EXECUTION_KEY')
    for role in ('ACCOUNT','CARD','DISPUTE','PIX','WELLNESS'):
        require(f'AGENT_{role}_URL',f'AGENT_{role}_API_KEY')
        url(f'AGENT_{role}_URL',True)
    if e.get('USE_LLM_PROVIDER')=='true':
        # Runtime Agent Manager-injected variables are expected.
        require('LLM_PROVIDER_URL','LLM_PROVIDER_KEY')
        url('LLM_PROVIDER_URL',True)
    else:warnings.append('No managed LLM provider: this run cannot demonstrate gateway LLM policies')
elif a.mode=='hosted-specialist':
    require('MCP_SERVER_URL','MCP_AUTH_MODE','SPECIALIST_CALL_KEY')
    url('MCP_SERVER_URL',True)
    if e.get('MCP_AUTH_MODE')!='agentid':warnings.append('Not using AgentID tool-level OAuth: real gateway scope demonstration unavailable')
    else:require('AMP_AGENTID_CLIENT_ID','AMP_AGENTID_CLIENT_SECRET','AMP_AGENTID_TOKEN_ENDPOINT')
    if e.get('LLM_GROUNDED_ANSWERS')=='true':
        require('LLM_PROVIDER_URL','LLM_PROVIDER_KEY')
        url('LLM_PROVIDER_URL',True)
    if e.get('OPENAI_API_KEY') and e.get('WSO2_MODE','true').lower()=='true':
        errors.append('Direct OPENAI_API_KEY configured on hosted specialist; remove bypass')
elif a.mode=='hosted-upstream-mcp':
    require('BANK_CORE_URL','BANK_CORE_READ_KEY','BANK_CORE_WRITE_KEY','MCP_ALLOWED_HOSTS')
    url('BANK_CORE_URL')
    if '*'==e.get('MCP_ALLOWED_HOSTS','').strip():errors.append('MCP_ALLOWED_HOSTS must not allow every host')
if a.mode!='source' and e.get('WSO2_MODE','').lower()!='true':warnings.append('WSO2_MODE is not true; managed-mode checks may not be enforced')
for w in warnings:print('WARN:',w)
for err in errors:print('FAIL:',err)
if errors:raise SystemExit(2)
print('PASS:',a.mode,'configuration shape checked. No managed platform controls tested.')
