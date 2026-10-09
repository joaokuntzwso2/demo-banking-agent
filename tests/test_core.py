import os
os.environ['DATABASE_URL']='sqlite://'
os.environ['BANK_CORE_READ_KEY']='test-core-read-secret'
os.environ['BANK_CORE_WRITE_KEY']='test-core-write-secret'
os.environ['BANK_APPROVAL_KEY']='test-approval-secret'
from fastapi.testclient import TestClient
from mock_core.app import app
from mock_core.store import reset, engine
from sqlalchemy import text
from sqlalchemy.orm import Session
client = TestClient(app)
READ_H = {'X-Internal-Key': os.environ['BANK_CORE_READ_KEY']}
WRITE_H = {'X-Internal-Key': os.environ['BANK_CORE_WRITE_KEY']}
APPROVAL_H = {'X-Internal-Key': os.environ['BANK_APPROVAL_KEY']}
def post(path, obj):
    # Workflow persistence belongs to the orchestration/approval capability.
    headers = APPROVAL_H if path.startswith('/workflows/') else WRITE_H
    return client.post(path, headers=headers, json=obj)
def approval(action,payload):
    r=post('/approvals',{'customer_id':'C001','action':action,'payload':payload});assert r.status_code==200
    a=r.json();d=client.post(f"/approvals/{a['id']}/decide?approved=true&customer_id=C001",headers=APPROVAL_H);assert d.status_code==200
    return a['id']
def test_seed_and_credit_card_balance_invariant():
    reset();s=client.get('/state/C001',headers=READ_H).json()
    assert s['account']['balance']==8450
    assert any(t['id']=='TXN-ORION' and t['amount']==1280 for t in s['transactions'])
    aid=approval('open_dispute',{'transaction_id':'TXN-ORION'})
    r=post('/disputes',{'customer_id':'C001','transaction_id':'TXN-ORION','approval_id':aid})
    assert r.status_code==200 and r.json()['status']=='under_review'
    assert client.get('/state/C001',headers=READ_H).json()['account']['balance']==8450

def test_pix_approval_idempotency_and_ledger():
    reset();base={'customer_id':'C001','beneficiary_id':'BEN001','amount':'1250.00','idempotency_key':'PIX-fake'}
    assert post('/pix',{**base,'approval_id':'fake'}).status_code==403
    aid=approval('execute_pix',{'beneficiary_id':'BEN001','amount':'1250.00'})
    base['idempotency_key']='PIX-'+aid
    r=post('/pix',{**base,'approval_id':aid});assert r.status_code==200
    duplicate=post('/pix',{**base,'approval_id':aid});assert duplicate.status_code==200
    assert duplicate.json()['id']==r.json()['id']
    s=client.get('/state/C001',headers=READ_H).json()
    assert s['account']['balance']==7200
    assert len(s['payments'])==1
    assert post('/pix',{**base,'amount':'1400.00','approval_id':aid}).status_code==403

def test_card_block_requires_matching_approval():
    reset();aid=approval('block_card',{'card_id':'CARD001'})
    r=post('/cards/block',{'customer_id':'C001','card_id':'CARD001','approval_id':aid})
    assert r.status_code==200 and r.json()['status']=='blocked'
    assert post('/cards/block',{'customer_id':'C001','card_id':'CARD001','approval_id':aid}).json()['status']=='blocked'

def test_unauthorized_access():
    assert client.get('/state/C001',headers={'X-Internal-Key':'wrong'}).status_code==401
    assert client.get('/state/C999',headers=READ_H).status_code==404


def test_first_run_seed_obeys_foreign_keys():
    """SQLite tests must enforce real FK ordering, as PostgreSQL always does."""
    with Session(engine) as session:
        assert session.scalar(text('PRAGMA foreign_keys')) == 1
    reset()
    with Session(engine) as session:
        rows = session.execute(text('SELECT accounts.customer_id FROM accounts JOIN customers ON accounts.customer_id = customers.id')).all()
    assert set(rows) == {('C001',), ('C002',)}


def test_database_initialization_preserves_existing_state():
    """Restarting Banking Core must not erase actions or reseed the account."""
    from mock_core.store import initialize_database
    reset()
    aid = approval('execute_pix', {'beneficiary_id': 'BEN001', 'amount': '1250.00'})
    payload = {'customer_id': 'C001', 'beneficiary_id': 'BEN001', 'amount': '1250.00',
               'idempotency_key': 'PIX-'+aid, 'approval_id': aid}
    assert post('/pix', payload).status_code == 200
    initialize_database()
    state = client.get('/state/C001', headers=READ_H).json()
    assert state['account']['balance'] == 7200
    assert len(state['payments']) == 1
    # Clean up to avoid state leaking into unrelated tests.
    reset()


def test_explicit_reset_is_transactional_and_fk_safe():
    """An explicit reset restores the seed without destructive schema DDL."""
    reset()
    aid = approval('block_card', {'card_id': 'CARD001'})
    assert post('/cards/block', {'customer_id': 'C001', 'card_id': 'CARD001', 'approval_id': aid}).status_code == 200
    reset()
    state = client.get('/state/C001', headers=READ_H).json()
    assert state['cards'][0]['status'] == 'active'
    assert state['account']['balance'] == 8450
    assert state['events'] == []


def test_approval_retry_returns_original_outcome_without_second_debit():
    reset()
    a=post('/approvals',{'customer_id':'C001','action':'execute_pix',
                         'payload':{'beneficiary_id':'BEN001','amount':'1250.00'}}).json()
    path=f"/approvals/{a['id']}/decide?approved=true&customer_id=C001"
    assert client.post(path,headers=APPROVAL_H).status_code==200
    assert client.post(path,headers=APPROVAL_H).status_code==200  # lost approval-response replay
    data={'customer_id':'C001','beneficiary_id':'BEN001','amount':'1250.00',
          'approval_id':a['id'],'idempotency_key':'PIX-'+a['id']}
    first=post('/pix',data);assert first.status_code==200
    second=post('/pix',data);assert second.status_code==200
    assert first.json()['id']==second.json()['id']
    state=client.get('/state/C001',headers=READ_H).json()
    assert state['account']['balance']==7200 and len(state['payments'])==1
    assert len([e for e in state['events'] if e['type']=='pix_completed'])==1

def test_rejected_approval_is_never_executable():
    reset()
    a=post('/approvals',{'customer_id':'C001','action':'block_card','payload':{'card_id':'CARD001'}}).json()
    path=f"/approvals/{a['id']}/decide?approved=false&customer_id=C001"
    assert client.post(path,headers=APPROVAL_H).status_code==200
    assert client.post(path,headers=APPROVAL_H).status_code==200
    assert post('/cards/block',{'customer_id':'C001','card_id':'CARD001','approval_id':a['id']}).status_code==403
    assert client.post(path.replace('approved=false','approved=true'),headers=APPROVAL_H).status_code==409

def test_customer_separation_and_workflow_persistence():
    reset()
    wf={'customer_id':'C001','state':{'pending':[{'approval_id':'APR-1'}],'steps':[{'stage':'plan'}]}}
    assert post('/workflows/test-workflow',wf).status_code==200
    read=client.get('/workflows/test-workflow?customer_id=C001',headers=APPROVAL_H)
    assert read.status_code==200 and read.json()['state']['pending'][0]['approval_id']=='APR-1'
    assert client.get('/workflows/test-workflow?customer_id=C999',headers=APPROVAL_H).status_code==404
    reset()


def test_credentials_are_scoped_by_operation():
    """Read, write and approval capabilities must not be interchangeable."""
    reset()
    req = {'customer_id': 'C001', 'action': 'block_card',
           'payload': {'card_id': 'CARD001'}}
    assert client.get('/state/C001', headers=WRITE_H).status_code == 401
    assert client.get('/state/C001', headers=APPROVAL_H).status_code == 401
    assert client.post('/approvals', headers=READ_H, json=req).status_code == 401
    assert client.post('/approvals', headers=APPROVAL_H, json=req).status_code == 401
    r = client.post('/approvals', headers=WRITE_H, json=req)
    assert r.status_code == 200
    path = f"/approvals/{r.json()['id']}/decide?approved=true&customer_id=C001"
    assert client.post(path, headers=READ_H).status_code == 401
    assert client.post(path, headers=WRITE_H).status_code == 401
    assert client.post(path, headers=APPROVAL_H).status_code == 200
