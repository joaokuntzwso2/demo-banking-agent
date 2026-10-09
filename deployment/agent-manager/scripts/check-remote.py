#!/usr/bin/env python3
"""Check deployed Concierge through Agent Manager Gateway (read only)."""
import json,os,sys,urllib.request,urllib.error
base=os.getenv('CONCIERGE_URL','').rstrip('/')
key=os.getenv('CONCIERGE_API_KEY','')
call_key=os.getenv('CONCIERGE_CALL_KEY','')
if not base or not key or not call_key:
    sys.exit('Set CONCIERGE_URL, CONCIERGE_API_KEY and CONCIERGE_CALL_KEY (backend only)')
req=urllib.request.Request(base+'/chat',data=json.dumps({
    'customer_id':'C001','language':'en','message':'What is my checking account balance?'}).encode(),
    headers={'Content-Type':'application/json','X-API-Key':key,'X-Agent-Call-Key':call_key},method='POST')
try:
    with urllib.request.urlopen(req,timeout=60) as r: data=json.load(r)
except urllib.error.HTTPError as exc:
    sys.exit('Gateway HTTP '+str(exc.code)+': '+exc.read(400).decode(errors='replace'))
print(json.dumps({'agent':data.get('agent'),'message':data.get('message'),
                  'invoked_agents':data.get('data',{}).get('invoked_agents')},indent=2))
assert data.get('agent')=='concierge', 'Wrong deployed agent/response schema'
assert 'account' in data.get('data',{}).get('invoked_agents',[]),'Account specialist not invoked'
assert '8,450' in data.get('message','') or '8.450' in data.get('message',''), 'Expected initial synthetic balance missing; reset demo or inspect state'
print('PASS: Gateway -> Concierge -> Account -> governed MCP -> Core (read only)')
