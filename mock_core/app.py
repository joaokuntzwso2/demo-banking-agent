import json, hmac, os
from decimal import Decimal
from fastapi import FastAPI, HTTPException, Header
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session
from .store import *
from banking_common.config import INTERNAL_KEY
app=FastAPI(title='Banco Aurora Mock Core',version='1.0.0')

def guard(key):
    if not hmac.compare_digest(key or '',INTERNAL_KEY):raise HTTPException(401,'Invalid internal key')
def owner(s,customer):
    if not s.get(Customer,customer):raise HTTPException(404,'Customer not found')
def event(s,customer,kind,payload):s.add(Event(id=ident('EVT'),customer_id=customer,type=kind,payload=json.dumps(payload,ensure_ascii=False),created=now()))
def require_approval(s,approval_id,customer,action,expected):
    a=s.get(Approval,approval_id or '');
    if not a or a.customer_id!=customer or a.action!=action or a.status!='approved' or json.loads(a.payload)!=expected:raise HTTPException(403,'Approved action with matching immutable payload required')
    a.status='consumed';return a
class CustomerRequest(BaseModel): customer_id:str='C001'
class CardRequest(CustomerRequest):card_id:str='CARD001';approval_id:str
class DisputeRequest(CustomerRequest):transaction_id:str;approval_id:str
class PixRequest(CustomerRequest):beneficiary_id:str;amount:Decimal=Field(gt=0);approval_id:str;idempotency_key:str
class ApprovalRequest(CustomerRequest):action:str;payload:dict

@app.get('/health')
def health():return {'status':'ok'}
@app.get('/state/{customer_id}')
def state(customer_id:str,x_internal_key:str|None=Header(None)):
    guard(x_internal_key)
    with Session(engine) as s:
        owner(s,customer_id)
        def rows(model):return serialize(s.scalars(select(model).where(model.customer_id==customer_id)).all())
        account=s.scalar(select(Account).where(Account.customer_id==customer_id))
        return {'customer':serialize(s.get(Customer,customer_id)),'account':serialize(account),'cards':rows(Card),'transactions':rows(Transaction),'beneficiaries':rows(Beneficiary),'obligations':rows(Obligation),'disputes':rows(Dispute),'payments':rows(Payment),'approvals':rows(Approval),'events':rows(Event)}
@app.post('/approvals')
def request_approval(r:ApprovalRequest,x_internal_key:str|None=Header(None)):
    guard(x_internal_key)
    if r.action not in ('block_card','open_dispute','execute_pix'):raise HTTPException(400,'Action prohibited')
    with Session(engine) as s:
        owner(s,r.customer_id)
        a=Approval(id=ident('APR'),customer_id=r.customer_id,action=r.action,payload=json.dumps(r.payload,sort_keys=True),status='pending',created=now());s.add(a)
        event(s,r.customer_id,'approval_requested',{'approval_id':a.id,'action':a.action});s.commit();return serialize(a)
@app.post('/approvals/{approval_id}/decide')
def decide(approval_id:str,approved:bool,customer_id:str='C001',x_internal_key:str|None=Header(None)):
    guard(x_internal_key)
    with Session(engine) as s:
        a=s.get(Approval,approval_id)
        if not a or a.customer_id!=customer_id:raise HTTPException(404,'Approval not found')
        if a.status!='pending':raise HTTPException(409,'Already decided')
        a.status='approved' if approved else 'rejected';event(s,customer_id,'approval_decision',{'approval_id':a.id,'approved':approved});s.commit();return serialize(a)
@app.post('/cards/block')
def block(r:CardRequest,x_internal_key:str|None=Header(None)):
    guard(x_internal_key)
    with Session(engine) as s:
        c=s.get(Card,r.card_id)
        if not c or c.customer_id!=r.customer_id:raise HTTPException(404,'Card not found')
        require_approval(s,r.approval_id,r.customer_id,'block_card',{'card_id':r.card_id})
        c.status='blocked';event(s,r.customer_id,'card_blocked',{'card_id':c.id});s.commit();return serialize(c)
@app.post('/disputes')
def dispute(r:DisputeRequest,x_internal_key:str|None=Header(None)):
    guard(x_internal_key)
    with Session(engine) as s:
        t=s.get(Transaction,r.transaction_id)
        if not t or t.customer_id!=r.customer_id or t.kind!='card':raise HTTPException(404,'Card transaction not found')
        existing=s.scalar(select(Dispute).where(Dispute.transaction_id==t.id,Dispute.customer_id==r.customer_id))
        if existing:raise HTTPException(409,'Transaction already disputed')
        require_approval(s,r.approval_id,r.customer_id,'open_dispute',{'transaction_id':r.transaction_id})
        d=Dispute(id=ident('DSP'),customer_id=r.customer_id,transaction_id=t.id,status='under_review',created=now());s.add(d)
        event(s,r.customer_id,'dispute_created',{'dispute_id':d.id,'transaction_id':t.id});s.commit();return serialize(d)
@app.post('/pix')
def pix(r:PixRequest,x_internal_key:str|None=Header(None)):
    guard(x_internal_key)
    with Session(engine) as s:
        p=s.scalar(select(Payment).where(Payment.idempotency_key==r.idempotency_key))
        if p:
            if p.customer_id!=r.customer_id or p.beneficiary_id!=r.beneficiary_id or p.amount!=r.amount:raise HTTPException(409,'Idempotency collision')
            return serialize(p)
        ben=s.get(Beneficiary,r.beneficiary_id)
        a=s.scalar(select(Account).where(Account.customer_id==r.customer_id))
        if not ben or ben.customer_id!=r.customer_id or not ben.verified:raise HTTPException(400,'Beneficiary not verified')
        if not a or a.balance<r.amount:raise HTTPException(409,'Insufficient funds')
        if r.amount>Decimal('2500'):raise HTTPException(403,'Demo Pix limit exceeded')
        payload={'beneficiary_id':r.beneficiary_id,'amount':str(r.amount)}
        require_approval(s,r.approval_id,r.customer_id,'execute_pix',payload)
        p=Payment(id=ident('PIX'),customer_id=r.customer_id,beneficiary_id=ben.id,amount=r.amount,status='completed',idempotency_key=r.idempotency_key,created=now());s.add(p)
        a.balance-=r.amount;s.add(Ledger(id=ident('LDG'),account_id=a.id,payment_id=p.id,amount=-r.amount,created=now()))
        event(s,r.customer_id,'pix_completed',{'payment_id':p.id,'amount':float(r.amount),'beneficiary_id':ben.id})
        s.commit();return serialize(p)
@app.post('/admin/reset')
def admin_reset(x_internal_key:str|None=Header(None)):
    guard(x_internal_key)
    if os.getenv('ENABLE_DEMO_RESET','false').lower()!='true':raise HTTPException(403,'Reset disabled')
    reset();return {'status':'reset'}
