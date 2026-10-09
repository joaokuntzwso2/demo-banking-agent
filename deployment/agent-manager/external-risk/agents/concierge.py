"""Customer-facing orchestrator. No banking mutations without explicit approval.

Graph: understand -> plan -> parallel independent specialists -> compose.
Approvals are persisted with the workflow and executed ONLY by their specialist;
commit acknowledgement is recovered via a same-approval idempotent retry.
"""
import os,asyncio,json,httpx,uuid,re
from typing import TypedDict
from fastapi import Header,HTTPException
from pydantic import BaseModel,Field
from langgraph.graph import StateGraph,START,END
from banking_common.config import CORE_URL
from banking_common.security import require_secret,secret
from banking_common.telemetry import outbound_headers
from .intents import action_intent,wants_human
from .confirmation import confirmed,response as confirmation_response
from .common import *

ROLE_URLS={role:os.getenv('AGENT_'+role.upper()+'_URL',f'http://localhost:{port}')
           for role,port in [('account',8201),('card',8202),('dispute',8203),('pix',8204),('wellness',8205)]}
if os.getenv('AGENT_RISK_URL'):
    ROLE_URLS['risk']=os.getenv('AGENT_RISK_URL')
KEYWORDS={'risk':['urgent settlement','urgent pix','unverified','new pix key','no verificado','urgente'],
          'account':['account','balance','cuenta','saldo','transaction','transacci'],
          'card':['card','tarjeta','fraud','fraude','block','bloque','unrecognized','desconoc'],
          'dispute':['dispute','reclamo','reclamaci','charge','cargo','fraud','fraude','recognize','reconozco'],
          'pix':['pix','transfer','pagar','pay ','payment','pago'],
          'wellness':['budget','expenses','gastos','future','enough','suficiente','money','dinero','obligacion','remaining','what if','what happens','si pago','si pagara']}

def gateway_headers(role):
    key=os.getenv('AGENT_'+role.upper()+'_API_KEY','')
    if os.getenv('WSO2_MODE','false').lower()=='true' and not key:
        raise RuntimeError(f'Missing Agent Manager gateway credential for {role}')
    return {**({'X-API-Key':key} if key else {}),'X-Agent-Call-Key':secret('SPECIALIST_CALL_KEY'),**outbound_headers()}

def heuristic(message):
    t=message.casefold();hits=[k for k,v in KEYWORDS.items() if any(w in t for w in v)]
    return hits or ['account']

def deterministic_requirements(message):
    """LLM is advisory for routing; explicit business intents cannot be dropped."""
    t=message.casefold()
    needed=set()
    if wants_human(message):needed.add('dispute')
    if 'risk' in ROLE_URLS and any(x in t for x in ('urgent settlement','new pix key','unverified','urgente')):
        needed.add('risk')
    for action,role in (('block_card','card'),('open_dispute','dispute'),('execute_pix','pix')):
        if action_intent(message,action).action:needed.add(role)
    if any(x in t for x in ('what if','what happens if','si pago','si pagara','si pagase','qué pasaría si')):
        needed.add('wellness')
        needed.discard('pix')  # no payment preparations for hypothetical spending
    return needed

def is_privacy_attack(message):
    t=message.casefold()
    external=('other customer','another customer','someone else',"other person",'otro cliente','otra persona',
              'todas las cuentas','all customers','other accounts')
    return any(x in t for x in external) and any(x in t for x in
             ('show','reveal','give me','access','display','dame','muestra','mostrar','enséñame','ver'))

async def plan(message):
    must=deterministic_requirements(message)
    guidance=('Return ONLY a JSON list of banking specialist roles: account,card,dispute,pix,wellness,risk (if available). '
              'Choose relevant independent specialists; never authorize financial operations. '
              'A request to speak to a person requires dispute. Hypothetical transfers require wellness, not Pix.')
    suggested=[]
    try:
        raw=await llm(guidance,message)
        parsed=json.loads(raw) if raw else None
        if isinstance(parsed,list):suggested=[r for r in parsed if r in ROLE_URLS]
    except Exception as exc:
        # A centrally governed model returning an error (including a guardrail
        # denial) MUST NOT silently downgrade into uncontrolled heuristic
        # execution of banking intents. Fail closed and surface the incident.
        if os.getenv('USE_LLM_PROVIDER','false').lower()=='true':
            raise HTTPException(503,'Governed model unavailable or rejected the request; no banking actions initiated') from exc
    choices=suggested or heuristic(message)
    return list(dict.fromkeys([*choices,*sorted(must)]))

class GraphState(TypedDict,total=False):
    request:dict
    language:str
    roles:list[str]
    results:list[dict]
    steps:list[dict]

async def interpret_node(state:GraphState):
    r=state['request'];lang=choose_language(r['message'],r.get('language','en'))
    return {'language':lang,'steps':[{'stage':'understand','status':'completed'}]}
async def plan_node(state:GraphState):
    roles=await plan(state['request']['message'])
    msg=state['request']['message'].casefold()
    if any(t in msg for t in ('what if','what happens if','si pago','si pagara','si pagase','qué pasaría si')):
        roles=['wellness']
    # After a requested Pix payment we must read back fresh account/wellness
    # state following the approved commit rather than present stale estimates.
    if 'pix' in roles and any(w in msg for w in ('then','después','luego','after','remaining','restante')):
        roles=[r for r in roles if r not in ('account','wellness')]
    return {'roles':roles,'steps':state['steps']+[{'stage':'plan','status':'completed','roles':roles}]}
async def invoke_roles(request,roles,lang):
    async with httpx.AsyncClient(timeout=50) as client:
        async def one(role):
            try:
                response=await client.post(ROLE_URLS[role].rstrip('/')+'/chat',
                    headers=gateway_headers(role),json={**request,'language':lang})
                response.raise_for_status();return response.json()
            except Exception as e:
                return {'agent':role,'message':('Service temporarily unavailable.' if lang=='en' else
                     'Servicio temporalmente no disponible.'),'data':{'error_type':type(e).__name__},'next_actions':[]}
        return await asyncio.gather(*(one(role) for role in roles))
async def invoke_node(state:GraphState):
    results=await invoke_roles(state['request'],state['roles'],state['language'])
    steps=[{'stage':'specialist','role':r,'status':'completed' if 'error_type' not in o.get('data',{}) else 'unavailable'}
           for r,o in zip(state['roles'],results)]
    return {'results':results,'steps':state['steps']+steps}
async def compose_node(state:GraphState):
    return {'steps':state['steps']+[{'stage':'compose','status':'completed'}]}

builder=StateGraph(GraphState)
for node,fn in [('understand',interpret_node),('plan',plan_node),('invoke',invoke_node),('compose',compose_node)]:
    builder.add_node(node,fn)
builder.add_edge(START,'understand');builder.add_edge('understand','plan')
builder.add_conditional_edges('plan',lambda st:'invoke' if st.get('roles') else 'compose',{'invoke':'invoke','compose':'compose'})
builder.add_edge('invoke','compose');builder.add_edge('compose',END)
workflow=builder.compile()

async def core(method,path,payload=None,capability='BANK_APPROVAL_KEY'):
    async with httpx.AsyncClient(timeout=25) as client:
        result=await client.request(method,CORE_URL+path,json=payload,
            headers={'X-Internal-Key':secret(capability),**outbound_headers()})
        result.raise_for_status();return result.json()

async def concierge(r:ChatRequest):
    workflow_id=r.workflow_id or uuid.uuid4().hex
    old={}
    try:old=(await core('GET',f'/workflows/{workflow_id}?customer_id={r.customer_id}'))['state']
    except httpx.HTTPStatusError as e:
        if e.response.status_code!=404:raise
    original=r.message
    if is_privacy_attack(original):
        lang=choose_language(original,r.language)
        return AgentResponse(agent='concierge',message=(
            'No puedo mostrar información de otros clientes ni omitir los controles de seguridad.' if lang=='es' else
            "I cannot access another customer's records or bypass banking authorization."),
            data={'language':lang,'invoked_agents':[],'workflow_id':workflow_id,
                  'steps':[{'stage':'privacy_policy','status':'denied'}]},next_actions=[])
    clarification=old.get('clarification')
    # A clarification response resumes the previously authorized *intent*,
    # but it never approves an operation or bypasses specialist validation.
    # Explicit cancellation or a new command starts a fresh plan.
    lower=original.strip().casefold()
    if clarification and not lower.startswith(('no','cancel','cancela','stop','alto')):
        new_command=any(action_intent(original,a).action for a in ('block_card','open_dispute','execute_pix'))
        if not new_command and not lower.startswith(('how','what','why','cómo','qué','que','explain','explica')):
            r=r.model_copy(update={'message':clarification['request']+'; '+original})
    state=await workflow.ainvoke({'request':r.model_dump(),'steps':[]})
    lang=state['language'];results=state.get('results',[])
    approvals=[a for result in results for a in result.get('next_actions',[])]
    pending={a['approval_id']:a for a in old.get('pending',[])}
    for action in approvals:pending[action['approval_id']]=action
    waiting=None
    for role,result in zip(state.get('roles',[]),results):
        corresponding={'card':'block_card','dispute':'open_dispute','pix':'execute_pix'}.get(role)
        if corresponding and action_intent(r.message,corresponding).action and not result.get('next_actions'):
            waiting={'role':role,'request':r.message}
            break
    progress={'language':lang,'pending':list(pending.values()),'completed':old.get('completed',[]),
              'steps':state['steps'],'last_message':r.message,'roles':state.get('roles',[]),
              'clarification':waiting}
    await core('POST','/workflows/'+workflow_id,{'customer_id':r.customer_id,'state':progress})
    return AgentResponse(agent='concierge',message='\n\n'.join(result['message'] for result in results),
        data={'language':lang,'invoked_agents':state.get('roles',[]),'specialists':results,
              'workflow_id':workflow_id,'steps':state['steps'],'awaiting_clarification':bool(waiting)},
        next_actions=approvals)

app=application('concierge',concierge)
class ApprovalDecision(BaseModel):
    approval_id:str
    approved:bool
    customer_id:str='C001'
    workflow_id:str
    language:str='en'
    original_request:str=''

async def post_action(action,outcome,customer_id,language):
    lang='es' if language=='es' else 'en'
    roles={'execute_pix':['account','wellness'],'block_card':['card'],'open_dispute':['dispute']}[action]
    question=({'execute_pix':'Check the current balance and future obligations; do not initiate another payment.',
               'block_card':'Check the current card status, do not block anything.',
               'open_dispute':'Show current disputes, do not open another case.'}[action])
    results=await invoke_roles({'customer_id':customer_id,'message':question,'language':lang,'history':[]},roles,lang)
    return confirmation_response(action,outcome,results,lang),roles,results

@app.post('/approve')
async def approve(req:ApprovalDecision,x_internal_key:str|None=Header(None)):
    require_secret(x_internal_key,'CONCIERGE_APPROVAL_KEY')
    if req.customer_id != 'C001':raise HTTPException(403,'Demo customer binding mismatch')
    # A signed-in customer may only approve an action prepared in THEIR
    # persisted workflow. An arbitrary guessed approval ID isn't sufficient.
    try:
        wf=(await core('GET',f'/workflows/{req.workflow_id}?customer_id={req.customer_id}'))['state']
    except httpx.HTTPStatusError as e:raise HTTPException(404,'Workflow not found') from e
    pending={a['approval_id']:a for a in wf.get('pending',[])}
    already=next((r for r in wf.get('completed',[]) if r['approval_id']==req.approval_id),None)
    if already:return already['response'] # completed response lost in transit
    if req.approval_id not in pending:raise HTTPException(403,'Approval not associated with this workflow')
    try:
        decision=await core('POST',f'/approvals/{req.approval_id}/decide?approved={str(req.approved).lower()}&customer_id={req.customer_id}')
        if not req.approved:
            response={'approval':decision,'result':None,'message':'Acción rechazada.' if req.language=='es' else 'Action declined.'}
        else:
            state=await core('GET',f'/state/{req.customer_id}',capability='BANK_CORE_READ_KEY')
            approval=next((x for x in state['approvals'] if x['id']==req.approval_id),None)
            if not approval:raise HTTPException(404,'Banking approval missing')
            payload=json.loads(approval['payload']) if isinstance(approval['payload'],str) else approval['payload']
            action=approval['action']
            config={'block_card':('card',{'card_id':payload.get('card_id')}),
                    'open_dispute':('dispute',{'transaction_id':payload.get('transaction_id')}),
                    'execute_pix':('pix',{**payload,'idempotency_key':'PIX-'+req.approval_id})}
            if action not in config:raise HTTPException(400,'Unsupported operation')
            role,details=config[action]
            async with httpx.AsyncClient(timeout=60) as client:
                result=await client.post(ROLE_URLS[role].rstrip('/')+'/execute',
                    headers={'X-Internal-Key':secret('SPECIALIST_EXECUTION_KEY'),**gateway_headers(role)},
                    json={'customer_id':req.customer_id,'approval_id':req.approval_id,'payload':details})
                result.raise_for_status();outcome=result.json()
            if not confirmed(action,outcome):raise HTTPException(502,'Result requires manual reconciliation')
            try:
                (message,verified),roles,results=await post_action(action,outcome,req.customer_id,req.language)
                data={'read_back_verified':verified,'invoked_agents':roles,'specialists':results}
            except Exception:
                message=(f'Operación completada (referencia {outcome["id"]}). No pude verificar el saldo posterior.'
                         if req.language=='es' else
                         f'Operation completed (reference {outcome["id"]}). Updated banking state could not be verified.')
                data={'read_back_verified':False}
            response={'approval':decision,'result':outcome,'message':message,'data':data}
    except httpx.HTTPStatusError as exc:
        # Retry SAME approval. Core and specialists are designed to replay the
        # completed outcome without duplicating the operation or debit.
        raise HTTPException(502,f'Banking execution unavailable (HTTP {exc.response.status_code}); retry this approval') from exc
    except httpx.RequestError as exc:
        raise HTTPException(503,'Banking execution uncertain; retry the same approval to reconcile') from exc
    pending.pop(req.approval_id,None)
    wf['pending']=list(pending.values())
    wf.setdefault('completed',[]).append({'approval_id':req.approval_id,'response':response})
    wf.setdefault('steps',[]).append({'stage':'approval_execution','approval_id':req.approval_id,
          'status':'completed' if req.approved else 'declined'})
    await core('POST',f'/workflows/{req.workflow_id}',{'customer_id':req.customer_id,'state':wf})
    return response

@app.get('/workflow/{workflow_id}')
async def get_workflow(workflow_id:str,customer_id:str='C001',x_internal_key:str|None=Header(None)):
    require_secret(x_internal_key,'CONCIERGE_APPROVAL_KEY')
    try:
        return await core('GET',f'/workflows/{workflow_id}?customer_id={customer_id}')
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code==404:return {'state':{'pending':[],'completed':[],'steps':[]}}
        raise HTTPException(502,'Workflow state unavailable') from exc
