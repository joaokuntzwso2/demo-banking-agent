#!/usr/bin/env python3
"""WRITE-capable synthetic banking E2E integration test. Resets the demo.

Requires the local Compose stack and a presenter reset code. NEVER run against
real banking services. Uses the same cookie/CSRF and BFF paths as the browser.
"""
import os,sys,httpx
BASE=os.getenv('AURORA_PORTAL_URL','http://localhost:8300')
ACCESS=os.getenv('DEMO_ACCESS_CODE','aurora-local-demo-only')
RESET=os.getenv('DEMO_RESET_CODE','local-presenter-reset')
client=httpx.Client(base_url=BASE,timeout=100)

def call(method,path,**kwargs):
    r=client.request(method,path,**kwargs)
    if r.status_code>=400:raise AssertionError(f'{method} {path}: {r.status_code} {r.text[:240]}')
    return r.json()

def chat(message,language='en'):
    return call('POST','/api/chat',json={'message':message,'language':language},headers={'X-CSRF-Token':csrf})

def decide(action,approved=True,language='en'):
    return call('POST',f"/api/approvals/{action['approval_id']}/decide",
                json={'approved':approved,'language':language},headers={'X-CSRF-Token':csrf})
try:
    assert client.get('/api/state').status_code==401
    login=call('POST','/api/login',json={'access_code':ACCESS})
    csrf=login['csrf_token']
    assert client.post('/api/chat',json={'message':'balance'}).status_code==403
    def reset():return call('POST','/api/reset',json={'reset_code':RESET},headers={'X-CSRF-Token':csrf})
    reset()
    try:
        original=call('GET','/api/state')
        assert original['account']['balance']==8450
        assert client.get('/api/state').status_code==200
        negated=chat('Do not pay R$1,250 to my condominium Pix beneficiary.')
        assert not negated['next_actions'],'Negated Pix must not request approval'
        info=chat('How does Pix work?')
        assert not info['next_actions'],'Informational request must not request approval'
        result=chat("I don't recognize the R$1,280 Orion Digital charge. Block my card and open a dispute.")
        actions={a['action']:a for a in result['next_actions']}
        assert {'block_card','open_dispute'}.issubset(actions),actions
        blocked=decide(actions['block_card']);assert blocked['result']['status']=='blocked'
        disputed=decide(actions['open_dispute']);assert disputed['result']['status']=='under_review'
        before=call('GET','/api/state')
        assert before['account']['balance']==8450,'Card charge/dispute must not debit checking'
        assert len(before['disputes'])==1
        payment=chat('Pay R$1,250 to my verified condominium Pix beneficiary, then check my remaining balance.')
        action=next(a for a in payment['next_actions'] if a['action']=='execute_pix')
        receipt=decide(action); assert receipt['result']['status']=='completed'
        assert receipt['data']['read_back_verified'],receipt
        same=decide(action);assert receipt['result']['id']==same['result']['id']
        after=call('GET','/api/state')
        assert after['account']['balance']==7200
        assert len(after['payments'])==1
        assert 'R$ 5,100.00' in receipt['message']
        spanish=chat('¿Cuánto dinero me queda después de mis próximos gastos?','es')
        assert 'R$ 5.100,00' in spanish['message'],spanish['message']
        assert all('correlation_id' in __import__('json').loads(e['payload']) for e in after['events'])
        print('PASS: authenticated BFF, CSRF, conservative intents, dual approvals, Pix idempotency, live projections, Spanish, audited events')
    finally:reset()
finally:client.close()
