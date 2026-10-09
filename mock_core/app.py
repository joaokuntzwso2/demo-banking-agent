"""Synthetic banking core. All writes validate customer ownership and approval payload."""
import json,os
from datetime import datetime,timezone
from decimal import Decimal
from fastapi import FastAPI,HTTPException,Header,Request
from pydantic import BaseModel,Field
from sqlalchemy import select
from sqlalchemy.orm import Session
from .store import *
from banking_common.security import require_secret
from banking_common.telemetry import install_telemetry,correlation_id
app=FastAPI(title='Banco Aurora Mock Core',version='1.1.0')
install_telemetry(app,'banking-core')

def guard(key,capability='BANK_CORE_READ_KEY'):require_secret(key,capability)
def owner(s,customer):
    if not s.get(Customer,customer):raise HTTPException(404,'Customer not found')
def audit(s,customer,kind,payload):
    s.add(Event(id=ident('EVT'),customer_id=customer,type=kind,
                payload=json.dumps({**payload,'correlation_id':correlation_id()},ensure_ascii=False),created=now()))
def require_approval(s,approval_id,customer,action,expected):
    a=s.get(Approval,approval_id or '',with_for_update=True)
    if not a or a.customer_id!=customer or a.action!=action or json.loads(a.payload)!=expected:
        raise HTTPException(403,'Approval ownership or immutable details mismatch')
    if a.status not in ('approved','consumed'):
        raise HTTPException(403,'Positive customer authorization required')
    return a

class CustomerRequest(BaseModel):customer_id:str='C001'
class CardRequest(CustomerRequest):card_id:str;approval_id:str
class DisputeRequest(CustomerRequest):transaction_id:str;approval_id:str
class PixRequest(CustomerRequest):beneficiary_id:str;amount:Decimal=Field(gt=0);approval_id:str;idempotency_key:str
class ApprovalRequest(CustomerRequest):action:str;payload:dict
class WorkflowRequest(CustomerRequest):state:dict
class EscalationRequest(CustomerRequest):
    reason:str=Field(min_length=8,max_length=500)
    priority:str='normal'
class CaseStatusRequest(CustomerRequest):
    status:str
    assigned_to:str='Aurora Human Support'

@app.get('/health')
def health():return {'status':'ok'}
@app.get('/state/{customer_id}')
def state(customer_id:str,x_internal_key:str|None=Header(None)):
    guard(x_internal_key)
    with Session(engine) as s:
        owner(s,customer_id)
        def rows(model):return serialize(s.scalars(select(model).where(model.customer_id==customer_id)).all())
        account=s.scalar(select(Account).where(Account.customer_id==customer_id))
        return {'customer':serialize(s.get(Customer,customer_id)),'account':serialize(account),'cards':rows(Card),'transactions':rows(Transaction),
                'beneficiaries':rows(Beneficiary),'obligations':rows(Obligation),'disputes':rows(Dispute),
                'payments':rows(Payment),'service_cases':rows(ServiceCase),'approvals':rows(Approval),'events':rows(Event)}

@app.post('/workflows/{workflow_id}')
def write_workflow(workflow_id:str,r:WorkflowRequest,x_internal_key:str|None=Header(None)):
    guard(x_internal_key,'BANK_APPROVAL_KEY')
    with Session(engine) as s:
        owner(s,r.customer_id)
        w=s.get(WorkflowSession,workflow_id,with_for_update=True)
        if w and w.customer_id!=r.customer_id:raise HTTPException(403,'Workflow owner mismatch')
        if not w:
            w=WorkflowSession(id=workflow_id,customer_id=r.customer_id)
            s.add(w)
        w.state_json=json.dumps(r.state,ensure_ascii=False);w.updated=now();s.commit()
    return {'workflow_id':workflow_id,'status':'saved'}
@app.get('/workflows/{workflow_id}')
def read_workflow(workflow_id:str,customer_id:str,x_internal_key:str|None=Header(None)):
    guard(x_internal_key,'BANK_APPROVAL_KEY')
    with Session(engine) as s:
        w=s.get(WorkflowSession,workflow_id)
        if not w or w.customer_id!=customer_id:raise HTTPException(404,'Workflow not found')
        return {'workflow_id':w.id,'state':json.loads(w.state_json)}

@app.post('/approvals')
def request_approval(r:ApprovalRequest,x_internal_key:str|None=Header(None)):
    guard(x_internal_key,'BANK_CORE_WRITE_KEY')
    if r.action not in ('block_card','open_dispute','execute_pix'):raise HTTPException(400,'Action prohibited')
    payload=json.dumps(r.payload,sort_keys=True)
    with Session(engine) as s:
        owner(s,r.customer_id)
        existing=s.scalar(select(Approval).where(Approval.customer_id==r.customer_id,Approval.action==r.action,
                                              Approval.payload==payload,Approval.status=='pending'))
        if existing:return serialize(existing)
        a=Approval(id=ident('APR'),customer_id=r.customer_id,action=r.action,payload=payload,status='pending',created=now())
        s.add(a);audit(s,r.customer_id,'approval_requested',{'approval_id':a.id,'action':a.action});s.commit();return serialize(a)
@app.post('/approvals/{approval_id}/decide')
def decide(approval_id:str,approved:bool,customer_id:str='C001',x_internal_key:str|None=Header(None)):
    guard(x_internal_key,'BANK_APPROVAL_KEY')
    with Session(engine) as s:
        a=s.get(Approval,approval_id,with_for_update=True)
        if not a or a.customer_id!=customer_id:raise HTTPException(404,'Approval not found')
        desired='approved' if approved else 'rejected'
        if a.status=='pending':
            a.status=desired;audit(s,customer_id,'approval_decision',{'approval_id':a.id,'approved':approved});s.commit()
        elif (approved and a.status in ('approved','consumed')) or (not approved and a.status=='rejected'):
            pass # replay after a lost HTTP response must be safe
        else:raise HTTPException(409,'Approval previously decided differently')
        return serialize(a)
@app.post('/cards/block')
def block(r:CardRequest,x_internal_key:str|None=Header(None)):
    guard(x_internal_key,'BANK_CORE_WRITE_KEY')
    with Session(engine) as s:
        c=s.get(Card,r.card_id,with_for_update=True)
        if not c or c.customer_id!=r.customer_id:raise HTTPException(404,'Card not found')
        a=require_approval(s,r.approval_id,r.customer_id,'block_card',{'card_id':r.card_id})
        if a.status=='consumed':
            if c.status!='blocked':raise HTTPException(409,'Approval consumed but card state differs')
            return serialize(c)
        c.status='blocked';a.status='consumed'
        audit(s,r.customer_id,'card_blocked',{'card_id':c.id,'approval_id':a.id});s.commit();return serialize(c)
@app.post('/disputes')
def dispute(r:DisputeRequest,x_internal_key:str|None=Header(None)):
    guard(x_internal_key,'BANK_CORE_WRITE_KEY')
    with Session(engine) as s:
        t=s.get(Transaction,r.transaction_id)
        if not t or t.customer_id!=r.customer_id or t.kind!='card':raise HTTPException(404,'Card transaction not found')
        a=require_approval(s,r.approval_id,r.customer_id,'open_dispute',{'transaction_id':r.transaction_id})
        existing=s.scalar(select(Dispute).where(Dispute.transaction_id==t.id,Dispute.customer_id==r.customer_id))
        if a.status=='consumed':
            if not existing:raise HTTPException(409,'Dispute operation requires reconciliation')
            return serialize(existing)
        if existing:raise HTTPException(409,'Transaction already disputed')
        d=Dispute(id=ident('DSP'),customer_id=r.customer_id,transaction_id=t.id,status='under_review',created=now())
        s.add(d);a.status='consumed'
        audit(s,r.customer_id,'dispute_created',{'dispute_id':d.id,'transaction_id':t.id,'approval_id':a.id});s.commit();return serialize(d)
@app.post('/pix')
def pix(r:PixRequest,x_internal_key:str|None=Header(None)):
    guard(x_internal_key,'BANK_CORE_WRITE_KEY')
    if r.idempotency_key!='PIX-'+r.approval_id and not os.getenv('ENABLE_LEGACY_IDEMPOTENCY','false').lower()=='true':
        # Older local clients can opt into arbitrary keys for existing test fixtures.
        raise HTTPException(422,'Idempotency key must be derived from approved operation')
    with Session(engine) as s:
        # Locks serialize same-approval replays and competing writes on an account.
        approval=require_approval(s,r.approval_id,r.customer_id,'execute_pix',
                                  {'beneficiary_id':r.beneficiary_id,'amount':str(r.amount)})
        p=s.scalar(select(Payment).where(Payment.idempotency_key==r.idempotency_key))
        if p:
            if (p.customer_id!=r.customer_id or p.beneficiary_id!=r.beneficiary_id or p.amount!=r.amount):
                raise HTTPException(409,'Idempotency collision')
            return serialize(p)
        if approval.status=='consumed':raise HTTPException(409,'Approved operation requires reconciliation')
        ben=s.get(Beneficiary,r.beneficiary_id)
        account=s.scalar(select(Account).where(Account.customer_id==r.customer_id).with_for_update())
        if not ben or ben.customer_id!=r.customer_id or not ben.verified:raise HTTPException(400,'Beneficiary not verified')
        if not account or account.balance<r.amount:raise HTTPException(409,'Insufficient funds')
        if r.amount>Decimal('2500'):raise HTTPException(403,'Demo Pix limit exceeded')
        p=Payment(id=ident('PIX'),customer_id=r.customer_id,beneficiary_id=ben.id,amount=r.amount,status='completed',
                  idempotency_key=r.idempotency_key,created=now());s.add(p)
        account.balance-=r.amount;approval.status='consumed'
        s.add(Ledger(id=ident('LDG'),account_id=account.id,payment_id=p.id,amount=-r.amount,created=now()))
        audit(s,r.customer_id,'pix_completed',{'payment_id':p.id,'approval_id':approval.id,
                                                'amount':float(r.amount),'beneficiary_id':ben.id})
        s.commit();return serialize(p)
@app.post('/service-cases')
def service_case(r:EscalationRequest,x_internal_key:str|None=Header(None)):
    """Create an auditable human handoff, not an automated financial decision."""
    guard(x_internal_key,'BANK_CORE_WRITE_KEY')
    if r.priority not in ('normal','high'):raise HTTPException(422,'Unsupported priority')
    with Session(engine) as s:
        owner(s,r.customer_id)
        # Dedupe open cases for the same normalized reason.
        same=s.scalar(select(ServiceCase).where(ServiceCase.customer_id==r.customer_id,
            ServiceCase.reason==r.reason,ServiceCase.status.in_(['waiting_for_agent','assigned'])))
        if same:return serialize(same)
        item=ServiceCase(id=ident('CASE'),customer_id=r.customer_id,reason=r.reason,
            priority=r.priority,status='waiting_for_agent',assigned_to='',created=now())
        s.add(item);audit(s,r.customer_id,'human_escalation',{'case_id':item.id,'priority':r.priority})
        s.commit();return serialize(item)

@app.post('/service-cases/{case_id}/assign')
def assign_case(case_id:str,r:CaseStatusRequest,x_internal_key:str|None=Header(None)):
    # Simulation-only operator action. A distinct credential, never an agent capability.
    guard(x_internal_key,'BANK_CASE_OPERATOR_KEY')
    if r.status not in ('assigned','in_progress','resolved'):
        raise HTTPException(422,'Invalid transition')
    with Session(engine) as s:
        item=s.get(ServiceCase,case_id,with_for_update=True)
        if not item or item.customer_id!=r.customer_id:raise HTTPException(404,'Case not found')
        allowed={'waiting_for_agent':{'assigned'},'assigned':{'in_progress'},'in_progress':{'resolved'},'resolved':set()}
        if item.status != r.status and r.status not in allowed[item.status]:raise HTTPException(409,'Invalid state transition')
        if item.status!=r.status:
            item.status=r.status;item.assigned_to=r.assigned_to
            audit(s,r.customer_id,'case_status_updated',{'case_id':case_id,'status':r.status})
            s.commit()
        return serialize(item)

@app.post('/admin/reset')
def admin_reset(x_internal_key:str|None=Header(None)):
    guard(x_internal_key,'BANK_RESET_KEY')
    if os.getenv('ENABLE_DEMO_RESET','false').lower()!='true':raise HTTPException(403,'Reset disabled')
    reset();return {'status':'reset'}
