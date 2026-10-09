"""Deterministic checks for the enhanced banking scenario fixtures and case states."""
import os,json
from pathlib import Path
os.environ.setdefault('DATABASE_URL','sqlite://')
os.environ.setdefault('BANK_CORE_READ_KEY','test-core-read-secret')
os.environ.setdefault('BANK_CORE_WRITE_KEY','test-core-write-secret')
os.environ.setdefault('BANK_APPROVAL_KEY','test-approval-secret')
os.environ.setdefault('BANK_CASE_OPERATOR_KEY','test-case-operator')
from fastapi.testclient import TestClient
from mock_core.app import app
from mock_core.store import reset
from agents.intents import action_intent,wants_human
ROOT=Path(__file__).resolve().parents[1]
client=TestClient(app)
R={'X-Internal-Key':os.environ['BANK_CORE_READ_KEY']}
W={'X-Internal-Key':os.environ['BANK_CORE_WRITE_KEY']}
O={'X-Internal-Key':os.environ['BANK_CASE_OPERATOR_KEY']}

def test_unverified_recipient_seed_is_visible_as_unverified():
    reset()
    s=client.get('/state/C001',headers=R).json()
    ben={b['id']:b for b in s['beneficiaries']}
    assert ben['BEN003']['verified']==0
    assert ben['BEN001']['verified']==1

def test_human_case_creation_deduplication_and_transition():
    reset()
    payload={'customer_id':'C001','reason':'Customer requests a human review of banking dispute TXN-ORION','priority':'high'}
    result=client.post('/service-cases',headers=W,json=payload)
    assert result.status_code==200,result.text
    item=result.json()
    again=client.post('/service-cases',headers=W,json=payload)
    assert again.json()['id']==item['id']
    bad=client.post('/service-cases/'+item['id']+'/assign',headers=R,json={
        'customer_id':'C001','status':'assigned'})
    assert bad.status_code==401
    bad_order=client.post('/service-cases/'+item['id']+'/assign',headers=O,json={
        'customer_id':'C001','status':'resolved'})
    assert bad_order.status_code==409
    for status in ('assigned','in_progress','resolved'):
        ok=client.post('/service-cases/'+item['id']+'/assign',headers=O,json={
            'customer_id':'C001','status':status})
        assert ok.status_code==200 and ok.json()['status']==status,ok.text
    final=client.get('/state/C001',headers=R).json()
    assert final['service_cases'][0]['status']=='resolved'
    assert len([e for e in final['events'] if e['type']=='human_escalation'])==1

def test_operator_cannot_transition_other_customer_case():
    reset()
    item=client.post('/service-cases',headers=W,json={
        'customer_id':'C001','reason':'Review dispute with a human'}).json()
    r=client.post('/service-cases/'+item['id']+'/assign',headers=O,json={
        'customer_id':'C002','status':'assigned'})
    assert r.status_code==404

def test_hypothetical_payment_is_not_a_direct_action():
    for text in ('What if I pay R$1,250 today?',
                 'What happens if I pay R$1,250 now?',
                 '¿Qué pasaría si pago R$1.250 ahora?'):
        assert action_intent(text,'execute_pix').action is None,text
    assert wants_human('I want to speak to a person about my dispute')
    assert wants_human('Quiero hablar con una persona sobre mi reclamación')

def test_eight_scenarios_and_policy_coverage():
    cases=json.loads((ROOT/'scenarios/catalog.json').read_text())
    docs=json.loads((ROOT/'knowledge/policies.json').read_text())
    assert [s['id'] for s in cases]==[f'S{i:02}' for i in range(1,9)]
    assert all(s.get('platform_evidence') and s.get('expected') for s in cases)
    assert len(docs)>=60
    topics={(d['topic'],d['language']) for d in docs}
    for topic in ('pix','security','service','governance','dispute','wellness'):
        assert (topic,'en') in topics and (topic,'es') in topics
    assert all(d['fictional'] is True and d.get('effective') and d.get('version') for d in docs)

def test_mcp_granularity_and_no_legacy_snapshot():
    source=(ROOT/'mcp_server/app.py').read_text()
    for tool in ('get_account_balance','list_account_transactions','get_card_status',
                 'list_card_transactions','get_dispute_status','list_beneficiaries',
                 'get_upcoming_obligations','get_human_cases','request_human_escalation'):
        assert f'async def {tool}(' in source
    assert 'async def get_banking_snapshot(' not in source
