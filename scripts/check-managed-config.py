#!/usr/bin/env python3
"""Read-only configuration preflight; never prints credentials or writes to AMP."""
import argparse,re,sys
from pathlib import Path
from urllib.parse import urlsplit
parser=argparse.ArgumentParser()
parser.add_argument('--env-file',required=True,help='Local .env file; never commit')
parser.add_argument('--mode',choices=('portal','concierge','specialist','upstream-mcp'),required=True)
args=parser.parse_args()
filename=Path(args.env_file)
if not filename.exists():raise SystemExit(f'Config file not found: {filename}')
env={}
for line in filename.read_text().splitlines():
    line=line.strip()
    if not line or line.startswith('#') or '=' not in line:continue
    k,v=line.split('=',1);env[k.strip()]=v.strip().strip('"').strip("'")
required={
 'portal':['CONCIERGE_URL','CONCIERGE_API_KEY','BANK_CORE_URL','BANK_CORE_READ_KEY','DEMO_SESSION_SIGNING_KEY','DEMO_ACCESS_CODE','DEMO_OPERATOR_CODE','BANK_CASE_OPERATOR_KEY'],
 'concierge':['AGENT_ACCOUNT_URL','AGENT_CARD_URL','AGENT_DISPUTE_URL','AGENT_PIX_URL','AGENT_WELLNESS_URL',
              'BANK_CORE_URL','BANK_CORE_READ_KEY','BANK_APPROVAL_KEY','CONCIERGE_CALL_KEY','CONCIERGE_APPROVAL_KEY'],
 'specialist':['MCP_SERVER_URL','MCP_AUTH_MODE','SPECIALIST_CALL_KEY'],
 'upstream-mcp':['BANK_CORE_URL','BANK_CORE_READ_KEY','BANK_CORE_WRITE_KEY','MCP_ALLOWED_HOSTS']
}[args.mode]
problems=[]
for key in required:
    v=env.get(key,'')
    if not v or any(bad in v.upper() for bad in ('REPLACE_','YOUR_','SECRET_','YOUR-')):
        problems.append(key+': missing or placeholder')
for key in [x for x in required if x.endswith('_URL')]:
    if key in env and env[key] and urlsplit(env[key]).scheme not in ('http','https'):
        problems.append(key+': expected absolute http(s) URL')
if args.mode=='specialist' and env.get('MCP_AUTH_MODE') not in ('apikey','agentid'):
    problems.append('MCP_AUTH_MODE: must be apikey or agentid')
if args.mode=='specialist' and env.get('MCP_AUTH_MODE')=='apikey' and not env.get('MCP_PROXY_API_KEY'):
    problems.append('MCP_PROXY_API_KEY: missing')
if args.mode=='portal' and env.get('DEMO_SECURE_COOKIE','false').lower()!='true':
    print('WARNING: DEMO_SECURE_COOKIE=false — use HTTPS and true for shared environments')
if args.mode=='concierge' and env.get('USE_LLM_PROVIDER','false').lower()=='true':
    print('INFO: LLM_PROVIDER_URL and LLM_PROVIDER_KEY expected to be injected by Agent Manager, not in this file')
for p in problems:print('ERROR:',p)
if problems:raise SystemExit(2)
print('PASS:',args.mode,'configuration keys present (does NOT verify connectivity, gateway policies or secrets).')
