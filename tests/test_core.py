import os
os.environ['DATABASE_URL']='sqlite://'
os.environ['BANK_INTERNAL_KEY']='test-secret'
from fastapi.testclient import TestClient
from mock_core.app import app
from mock_core.store import reset, engine
from sqlalchemy import text
from sqlalchemy.orm import Session
client=TestClient(app);H={'X-Internal-Key':'test-secret'}
def post(path,obj):return client.post(path,headers=H,json=obj)
def approval(action,payload):
    r=post('/approvals',{'customer_id':'C001','action':action,'payload':payload});assert r.status_code==200
    a=r.json();d=client.post(f"/approvals/{a['id']}/decide?approved=true&customer_id=C001",headers=H);assert d.status_code==200
    return a['id']
def test_seed_and_credit_card_balance_invariant():
    reset();s=client.get('/state/C001',headers=H).json()
    assert s['account']['balance']==8450
    assert any(t['id']=='TXN-ORION' and t['amount']==1280 for t in s['transactions'])
    aid=approval('open_dispute',{'transaction_id':'TXN-ORION'})
    r=post('/disputes',{'customer_id':'C001','transaction_id':'TXN-ORION','approval_id':aid})
    assert r.status_code==200 and r.json()['status']=='under_review'
    assert client.get('/state/C001',headers=H).json()['account']['balance']==8450

def test_pix_approval_idempotency_and_ledger():
    reset();base={'customer_id':'C001','beneficiary_id':'BEN001','amount':'1250.00','idempotency_key':'once'}
    assert post('/pix',{**base,'approval_id':'fake'}).status_code==403
    aid=approval('execute_pix',{'beneficiary_id':'BEN001','amount':'1250.00'})
    r=post('/pix',{**base,'approval_id':aid});assert r.status_code==200
    duplicate=post('/pix',{**base,'approval_id':aid});assert duplicate.status_code==200
    assert duplicate.json()['id']==r.json()['id']
    s=client.get('/state/C001',headers=H).json()
    assert s['account']['balance']==7200
    assert len(s['payments'])==1
    assert post('/pix',{**base,'amount':'1400.00','approval_id':aid}).status_code==409

def test_card_block_requires_matching_approval():
    reset();aid=approval('block_card',{'card_id':'CARD001'})
    r=post('/cards/block',{'customer_id':'C001','card_id':'CARD001','approval_id':aid})
    assert r.status_code==200 and r.json()['status']=='blocked'
    assert post('/cards/block',{'customer_id':'C001','card_id':'CARD001','approval_id':aid}).status_code==403

def test_unauthorized_access():
    assert client.get('/state/C001',headers={'X-Internal-Key':'wrong'}).status_code==401
    assert client.get('/state/C999',headers=H).status_code==404


def test_first_run_seed_obeys_foreign_keys():
    """SQLite tests must enforce real FK ordering, as PostgreSQL always does."""
    with Session(engine) as session:
        assert session.scalar(text('PRAGMA foreign_keys')) == 1
    reset()
    with Session(engine) as session:
        rows = session.execute(text('SELECT accounts.customer_id FROM accounts JOIN customers ON accounts.customer_id = customers.id')).all()
    assert rows == [('C001',)]


def test_database_initialization_preserves_existing_state():
    """Restarting Banking Core must not erase actions or reseed the account."""
    from mock_core.store import initialize_database
    reset()
    aid = approval('execute_pix', {'beneficiary_id': 'BEN001', 'amount': '1250.00'})
    payload = {'customer_id': 'C001', 'beneficiary_id': 'BEN001', 'amount': '1250.00',
               'idempotency_key': 'persist-across-restart', 'approval_id': aid}
    assert post('/pix', payload).status_code == 200
    initialize_database()
    state = client.get('/state/C001', headers=H).json()
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
    state = client.get('/state/C001', headers=H).json()
    assert state['cards'][0]['status'] == 'active'
    assert state['account']['balance'] == 8450
    assert state['events'] == []
