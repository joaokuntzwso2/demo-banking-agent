import os,re,asyncio,httpx,hmac
from fastapi import Header, HTTPException
from pydantic import BaseModel, Field
from banking_common.config import INTERNAL_KEY, CORE_URL
from .confirmation import confirmed, response as confirmation_response
from typing import TypedDict
from langgraph.graph import StateGraph,START,END
from fastapi import FastAPI
from .common import *
ROLE_URLS={role:os.getenv('AGENT_'+role.upper()+'_URL',f'http://localhost:{port}') for role,port in [('account',8201),('card',8202),('dispute',8203),('pix',8204),('wellness',8205)]}
def _agent_gateway_headers(role):
    key=os.getenv('AGENT_'+role.upper()+'_API_KEY','')
    if os.getenv('WSO2_MODE','false').lower()=='true' and not key:
        raise RuntimeError(f'Missing gateway credential for {role}')
    return {'X-API-Key':key} if key else {}

KEYWORDS={'account' :['account','balance','cuenta','saldo','transaction','transacci'], 'card':['card','tarjeta','fraud','fraude','block','bloque','unrecognized','desconoc'], 'dispute':['dispute','reclamo','charge','cargo','fraud','fraude','recognize','reconozco','desconoc'], 'pix':['pix','transfer','pagar','pay ','payment','pago'], 'wellness':['budget','expenses','gastos','future','enough','suficiente','money','dinero','obligacion','remaining']}

def heuristic(message):
    t=message.lower();hits=[k for k,terms in KEYWORDS.items() if any(x in t for x in terms)]
    return hits or ['account']
async def plan(message):
    prompt="Choose banking specialists for this request. Return ONLY a JSON array using role names account, card, dispute, pix, wellness. Select all necessary; never call external tools or authorize payment."
    try:
        raw=await llm(prompt,message)
        if raw:
            import json
            parsed=json.loads(raw)
            if isinstance(parsed,list):
                valid=[x for x in parsed if x in ROLE_URLS]
                if valid:return list(dict.fromkeys(valid))
    except Exception:pass
    return heuristic(message)
class OrchestrationState(TypedDict):
    request: dict
    roles: list[str]
    results: list[dict]

async def select_specialists(state:OrchestrationState):
    message = state['request']['message']
    roles = await plan(message)
    # Post-Pix balances and cash flow are deferred until AFTER approval and
    # actual execution, when the Concierge re-queries the banking agents.
    if 'pix' in roles and any(x in message.casefold() for x in ('then', 'después', 'luego', 'after', 'remaining balance', 'saldo restante')):
        roles = [role for role in roles if role not in ('account', 'wellness')]
    return {'roles': roles}

async def invoke_specialists(state:OrchestrationState):
    r=ChatRequest(**state['request'])
    lang=choose_language(r.message,r.language)
    async with httpx.AsyncClient(timeout=50) as client:
        async def invoke(role):
            try:
                res=await client.post(ROLE_URLS[role].rstrip('/')+'/chat',json={**r.model_dump(),'language':lang},headers=_agent_gateway_headers(role))
                res.raise_for_status();return res.json()
            except Exception as e:
                return {'agent':role,'message':('Service temporarily unavailable.' if lang=='en' else 'Servicio temporalmente no disponible.'),'data':{'error_type':type(e).__name__},'next_actions':[]}
        return {'results':await asyncio.gather(*(invoke(role) for role in state['roles']))}

graph=StateGraph(OrchestrationState)
graph.add_node('select',select_specialists)
graph.add_node('invoke',invoke_specialists)
graph.add_edge(START,'select');graph.add_edge('select','invoke');graph.add_edge('invoke',END)
workflow=graph.compile()

async def concierge(r):
    state=await workflow.ainvoke({'request':r.model_dump(),'roles':[],'results':[]})
    lang=choose_language(r.message,r.language)
    results=state['results']
    actions=[a for p in results for a in p.get('next_actions',[])]
    return AgentResponse(agent='concierge',message='\n\n'.join(p['message'] for p in results),data={'language':lang,'invoked_agents':state['roles'],'specialists':results},next_actions=actions)
app=application('concierge',concierge)

# The BFF invokes this protected callback only AFTER a specialist has executed a
# previously approved mutation.  The Concierge re-queries agents for up-to-date
# read models; it never invents a post-payment balance from an LLM response.
class PostActionRequest(BaseModel):
    customer_id: str = 'C001'
    language: str = 'en'
    original_request: str = ''
    action: str
    outcome: dict = Field(default_factory=dict)


@app.post('/post-action', response_model=AgentResponse)
async def post_action(req: PostActionRequest, x_internal_key: str | None = Header(None)):
    if not hmac.compare_digest(x_internal_key or '', INTERNAL_KEY):
        raise HTTPException(401, 'Service authentication required')
    if not confirmed(req.action, req.outcome):
        raise HTTPException(422, 'Successful specialist execution is required')

    lang = 'es' if req.language == 'es' else 'en'
    roles = {
        'execute_pix': ['account', 'wellness'],
        'block_card': ['card'],
        'open_dispute': ['dispute'],
    }[req.action]
    follow_up = ChatRequest(
        customer_id=req.customer_id, language=lang,
        message=('Confirm the latest balance and upcoming obligations after the authorized operation.'
                 if lang == 'en' else
                 'Confirma el saldo y los compromisos pendientes tras la operación autorizada.'),
    )
    invoked = await invoke_specialists({
        'request': follow_up.model_dump(), 'roles': roles, 'results': []
    })
    specialists = invoked['results']
    text, verified = confirmation_response(req.action, req.outcome, specialists, lang)
    event_id = req.outcome['id']
    return AgentResponse(agent='concierge', message=text,
                         data={'language':lang, 'invoked_agents':roles,
                               'specialists':specialists, 'read_back_verified':verified,
                               'action':req.action, 'transaction_id':event_id})



# The Concierge owns the approval->specialist execute->readback workflow.
# The BFF is just the single customer-facing transport and dashboard read model.
class ApprovalDecision(BaseModel):
    approval_id: str
    approved: bool
    customer_id: str = 'C001'
    language: str = 'en'
    original_request: str = ''

async def _bank_core(method, path, body=None):
    async with httpx.AsyncClient(timeout=30) as client:
        resp=await client.request(method, CORE_URL+path,
            headers={'X-Internal-Key':INTERNAL_KEY},json=body)
        resp.raise_for_status()
        return resp.json()

@app.post('/approve')
async def approve(req:ApprovalDecision, x_internal_key:str|None=Header(None)):
    if not hmac.compare_digest(x_internal_key or '', INTERNAL_KEY):
        raise HTTPException(401,'Authenticated banking channel required')
    # The core authorizes ownership and the immutable pending approval payload.
    try:
        decision=await _bank_core('POST',
            f'/approvals/{req.approval_id}/decide?approved={str(req.approved).lower()}&customer_id={req.customer_id}')
        if not req.approved:
            return {'approval':decision,'result':None,
                'message': 'Acción rechazada.' if req.language=='es' else 'Action declined.'}
        state=await _bank_core('GET',f'/state/{req.customer_id}')
        approval=next((x for x in state['approvals'] if x['id']==req.approval_id),None)
        if approval is None: raise HTTPException(404,'Approval missing')
        import json
        payload=json.loads(approval['payload']) if isinstance(approval['payload'],str) else approval['payload']
        action=approval['action']
        config={
            'block_card':('card',{'card_id':payload.get('card_id')}),
            'open_dispute':('dispute',{'transaction_id':payload.get('transaction_id')}),
            'execute_pix':('pix',{**payload,'idempotency_key':'PIX-'+req.approval_id}),
        }
        if action not in config: raise HTTPException(400,'Unsupported banking operation')
        role, details=config[action]
        async with httpx.AsyncClient(timeout=60) as client:
            result=await client.post(ROLE_URLS[role].rstrip('/')+'/execute',
                headers={'X-Internal-Key':INTERNAL_KEY, **_agent_gateway_headers(role)},
                json={'customer_id':req.customer_id,'approval_id':req.approval_id,'payload':details})
            result.raise_for_status()
            outcome=result.json()
    except httpx.HTTPStatusError as exc:
        # Do not mask a committed approval as an authorization success.
        raise HTTPException(502,f'Banking operation failed: upstream HTTP {exc.response.status_code}') from exc
    except httpx.RequestError as exc:
        raise HTTPException(503,'Banking operation temporarily unreachable') from exc
    # The operation is ALREADY committed; a readback failure cannot undo it.
    try:
        confirmation=await post_action(PostActionRequest(
            customer_id=req.customer_id,language=req.language,
            original_request=req.original_request,action=action,outcome=outcome),
            x_internal_key=INTERNAL_KEY)
        text=confirmation.message
        metadata=confirmation.data
    except Exception:
        text=(f'Operación completada (referencia {outcome.get("id", "N/A")}). '
              'No pude verificar el estado posterior.' if req.language=='es' else
              f'Operation completed (reference {outcome.get("id", "N/A")}). '
              'I could not verify the updated banking state.')
        metadata={'read_back_verified':False}
    return {'approval':decision,'result':outcome,'message':text,'data':metadata}
