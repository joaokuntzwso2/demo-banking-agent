import os
from .confirmation import parse_brl_amount, money
from decimal import Decimal
import hmac
from fastapi import Header,HTTPException
from pydantic import BaseModel
from banking_common.config import INTERNAL_KEY
from .common import *
from .retrieval import grounded_answer

def phrase(lang,en,es):return es if lang=='es' else en
async def account(r):
    s=await mcp_tool('get_banking_snapshot',{'customer_id':r.customer_id});a=s['account'];return AgentResponse(agent='account',message=phrase(r.language,f"Your checking balance is BRL {a['balance']:,.2f}. The displayed card charge does not debit checking.",f"El saldo de tu cuenta es BRL {a['balance']:,.2f}. El cargo de tarjeta no debita la cuenta corriente."),data={'account':a,'transactions':s['transactions'],'beneficiaries':s['beneficiaries'],'payments':s['payments']})
async def card(r):
    s=await mcp_tool('get_banking_snapshot',{'customer_id':r.customer_id});c=s['cards'][0]
    msg=phrase(r.language,f"Card ending {c['last4']} is {c['status']}. I can request a block, but confirmation is required.",f"La tarjeta terminada en {c['last4']} está {c['status']}. Puedo solicitar el bloqueo; es necesaria tu confirmación.")
    actions=[]
    if c['status']=='active' and any(x in r.message.lower() for x in ['block','freeze','bloque','fraud','fraude','unknown','desconoc','recognize']):
        approval=await mcp_tool('request_card_block',{'customer_id':r.customer_id,'card_id':c['id']});actions=[{'approval_id':approval['id'],'action':'block_card','label':phrase(r.language,'Confirm card block','Confirmar bloqueo de tarjeta')}]
    return AgentResponse(agent='card',message=msg,data={'card':c},next_actions=actions)
async def dispute(r):
    s=await mcp_tool('get_banking_snapshot',{'customer_id':r.customer_id});tx=next((t for t in s['transactions'] if t['id']=='TXN-ORION'),None)
    rag=await grounded_answer('dispute',r.language,r.message,{'transaction_id':tx['id'] if tx else None,'merchant':tx['merchant'] if tx else None,'amount_brl':tx['amount'] if tx else None})
    policy=rag['answer']
    actions=[]
    if tx and not any(d['transaction_id']==tx['id'] for d in s['disputes']):
        approval=await mcp_tool('request_dispute',{'customer_id':r.customer_id,'transaction_id':tx['id']});actions=[{'approval_id':approval['id'],'action':'open_dispute','label':phrase(r.language,'Confirm dispute case','Confirmar reclamación')}]
    return AgentResponse(agent='dispute',message=policy,data={'transaction':tx,'disputes':s['disputes'],'citation':'POL-DIS-001','retrieval_method':rag['document']['retrieval_method']},next_actions=actions)
async def pix(r):
    s=await mcp_tool('get_banking_snapshot',{'customer_id':r.customer_id})
    candidates=[b for b in s['beneficiaries'] if b['verified']]
    text=r.message.casefold()
    intent=any(k in text for k in ('pay','transfer','send','pagar','paga','transferir','enviar'))
    if not intent:
        return AgentResponse(agent='pix',message=phrase(r.language,
            'I can help you make a simulated Pix payment. Tell me the verified beneficiary and amount in BRL.',
            'Puedo ayudarte a hacer un Pix simulado. Indica el beneficiario verificado y el importe en BRL.'))
    amount=parse_brl_amount(r.message)
    if amount is None:
        return AgentResponse(agent='pix',message=phrase(r.language,
            'Please specify the Pix amount using R$ or BRL (for example, R$1,250).',
            'Indica el importe del Pix con R$ o BRL (por ejemplo, R$1.250).'))
    if amount>Decimal('2500'):
        return AgentResponse(agent='pix',message=phrase(r.language,
            'That exceeds the simulated Pix limit of R$2,500. No payment was requested.',
            'Ese importe supera el límite Pix simulado de R$2.500. No se solicitó ningún pago.'))
    beneficiary=next((b for b in candidates if
        'condom' in text or 'verified beneficiary' in text or 'beneficiario verificado' in text or
        b['name'].casefold() in text),None)
    if not beneficiary:
        return AgentResponse(agent='pix',message=phrase(r.language,
            'Please confirm the beneficiary by name before creating a Pix authorization.',
            'Confirma el nombre del beneficiario antes de solicitar una autorización Pix.'))
    if s['account']['balance']<float(amount):
        return AgentResponse(agent='pix',message=phrase(r.language,
            'The current checking balance is insufficient for that Pix amount.',
            'El saldo de la cuenta no alcanza para ese importe Pix.'))
    amount_str=f'{amount:.2f}'
    approval=await mcp_tool('request_pix',{
        'customer_id':r.customer_id,'beneficiary_id':beneficiary['id'],'amount':amount_str})
    label=phrase(r.language,'Authorize Pix of '+money(amount,'en'),
                 'Autorizar Pix de '+money(amount,'es'))
    msg=phrase(r.language,
        f'Verified beneficiary: {beneficiary["name"]}. The simulated Pix of {money(amount,"en")} is pending your approval. No funds move until you authorize it.',
        f'Beneficiario verificado: {beneficiary["name"]}. El Pix simulado de {money(amount,"es")} espera tu autorización. No se mueve dinero antes de confirmarlo.')
    return AgentResponse(agent='pix',message=msg,data={'beneficiary':beneficiary,'amount':amount_str},
                         next_actions=[{'approval_id':approval['id'],'action':'execute_pix','label':label}])
async def wellness(r):
    s=await mcp_tool('get_banking_snapshot',{'customer_id':r.customer_id});due=sum(o['amount'] for o in s['obligations']);balance=s['account']['balance']
    rag=await grounded_answer('wellness',r.language,r.message,{'checking_balance_brl':f'{balance:.2f}','upcoming_obligations_brl':f'{due:.2f}','projected_remaining_brl':f'{balance-due:.2f}'})
    return AgentResponse(agent='wellness',message=rag['answer'],data={'balance':balance,'obligations':due,'projected':balance-due,'citation':'POL-FIN-001','retrieval_method':rag['document']['retrieval_method']})
ROLES={'account':account,'card':card,'dispute':dispute,'pix':pix,'wellness':wellness}
ROLE=os.getenv('AGENT_ROLE','account')
app=application(ROLE,ROLES.get(ROLE,account))
class ExecuteRequest(BaseModel):
    customer_id:str='C001'
    approval_id:str
    payload:dict

@app.post('/execute')
async def execute(req:ExecuteRequest,x_internal_key:str|None=Header(None)):
    if not hmac.compare_digest(x_internal_key or '',INTERNAL_KEY):
        raise HTTPException(401,'Service authentication required')
    mapping={
      'card':('execute_approved_card_block',['card_id']),
      'dispute':('execute_approved_dispute',['transaction_id']),
      'pix':('execute_approved_pix',['beneficiary_id','amount','idempotency_key'])}
    if ROLE not in mapping:raise HTTPException(403,'Specialist cannot execute mutations')
    tool,fields=mapping[ROLE]
    if not all(x in req.payload for x in fields):raise HTTPException(422,'Missing required fields')
    args={'customer_id':req.customer_id,'approval_id':req.approval_id,**{k:req.payload[k] for k in fields}}
    try:return await mcp_tool(tool,args)
    except Exception as exc:raise HTTPException(502,f'Governed tool call failed: {type(exc).__name__}') from exc

