"""Specialists never approve their own writes; tools are MCP-governed."""
import os,hmac
from decimal import Decimal
from fastapi import Header,HTTPException
from pydantic import BaseModel
from banking_common.security import require_secret
from .common import *
from .confirmation import parse_brl_amount,money
from .intents import action_intent,resolve_card,resolve_transaction,resolve_beneficiary
from .retrieval import grounded_answer

def phrase(lang,en,es):return es if lang=='es' else en
async def account(r):
    s=await mcp_tool('get_banking_snapshot',{'customer_id':r.customer_id})
    a=s['account']
    return AgentResponse(agent='account',message=phrase(r.language,
        f"Your checking balance is {money(a['balance'],'en')}. Credit-card charges do not immediately debit checking.",
        f"El saldo de tu cuenta es {money(a['balance'],'es')}. Los cargos de tarjeta no se debitan inmediatamente de la cuenta."),
        data={'account':a,'transactions':s['transactions'],'beneficiaries':s['beneficiaries'],'payments':s['payments']})
async def card(r):
    s=await mcp_tool('get_banking_snapshot',{'customer_id':r.customer_id})
    chosen=resolve_card(s['cards'],r.message)
    if not chosen:return AgentResponse(agent='card',message=phrase(r.language,
        'Which card should I inspect or block? Please provide its last four digits.',
        '¿Qué tarjeta deseas consultar o bloquear? Indica sus últimos cuatro dígitos.'))
    msg=phrase(r.language,
        f"Card ending {chosen['last4']} is {chosen['status']}.",
        f"La tarjeta terminada en {chosen['last4']} está {chosen['status']}.")
    actions=[]
    if chosen['status']=='active' and action_intent(r.message,'block_card').action:
        approval=await mcp_tool('request_card_block',{'customer_id':r.customer_id,'card_id':chosen['id']})
        actions=[{'approval_id':approval['id'],'action':'block_card','label':phrase(r.language,
            f"Block card •••• {chosen['last4']}",f"Bloquear tarjeta •••• {chosen['last4']}") ,'details':{'card_last4':chosen['last4']}}]
        msg+=phrase(r.language,' I prepared a block request; your approval is required.',
                    ' Preparé una solicitud de bloqueo que requiere tu autorización.')
    return AgentResponse(agent='card',message=msg,data={'card':chosen},next_actions=actions)
async def dispute(r):
    s=await mcp_tool('get_banking_snapshot',{'customer_id':r.customer_id})
    tx=resolve_transaction(s['transactions'],r.message)
    rag=await grounded_answer('dispute',r.language,r.message,{'transaction_id':tx['id'] if tx else None,
        'merchant':tx['merchant'] if tx else None,'amount_brl':tx['amount'] if tx else None})
    message=rag['answer'];actions=[]
    if action_intent(r.message,'open_dispute').action:
        if not tx:
            message+='\n\n'+phrase(r.language,
                'Please identify the card transaction by merchant, transaction ID or exact amount before I open a dispute.',
                'Identifica el cargo por comercio, ID de transacción o importe exacto antes de abrir una reclamación.')
        elif any(d['transaction_id']==tx['id'] for d in s['disputes']):
            message+='\n'+phrase(r.language,'An existing dispute already covers this transaction.',
                                   'Ya existe una reclamación para esta transacción.')
        else:
            approval=await mcp_tool('request_dispute',{'customer_id':r.customer_id,'transaction_id':tx['id']})
            actions=[{'approval_id':approval['id'],'action':'open_dispute',
                      'label':phrase(r.language,f"Dispute {tx['merchant']} ({money(tx['amount'],'en')})",
                                          f"Reclamar {tx['merchant']} ({money(tx['amount'],'es')})"),
                      'details':{'merchant':tx['merchant'],'amount':tx['amount']}}]
    return AgentResponse(agent='dispute',message=message,
        data={'transaction':tx,'disputes':s['disputes'],'citations':rag['citations'],'retrieval_method':rag['retrieval_method']},
        next_actions=actions)
async def pix(r):
    s=await mcp_tool('get_banking_snapshot',{'customer_id':r.customer_id})
    intent=action_intent(r.message,'execute_pix')
    if not intent.action:
        return AgentResponse(agent='pix',message=phrase(r.language,
            'I can explain Pix or prepare a simulated payment. State an explicit payment instruction, amount and verified beneficiary.',
            'Puedo explicar Pix o preparar un pago simulado. Indica una orden de pago, importe y beneficiario verificado.'),
            data={'intent_reason':intent.reason})
    amount=parse_brl_amount(r.message)
    if amount is None:
        return AgentResponse(agent='pix',message=phrase(r.language,
            'Please specify the exact Pix amount with R$ or BRL, for example R$1,250.',
            'Indica el importe Pix exacto con R$ o BRL, por ejemplo R$1.250.'))
    if amount>Decimal('2500'):
        return AgentResponse(agent='pix',message=phrase(r.language,
            'This exceeds the simulated Pix limit of R$2,500. No transfer has been requested.',
            'Supera el límite Pix simulado de R$2.500. No se solicitó ningún pago.'))
    beneficiary=resolve_beneficiary(s['beneficiaries'],r.message)
    if not beneficiary:
        return AgentResponse(agent='pix',message=phrase(r.language,
            'Which verified beneficiary should receive the Pix? Please name the beneficiary.',
            '¿Qué beneficiario verificado debe recibir el Pix? Indica el nombre.'),
            data={'verified_beneficiaries':[{'name':b['name'],'id':b['id']} for b in s['beneficiaries'] if b['verified']]})
    if Decimal(str(s['account']['balance']))<amount:
        return AgentResponse(agent='pix',message=phrase(r.language,
            'Insufficient checking funds; no payment was requested.',
            'Saldo insuficiente; no se solicitó el pago.'))
    amount_str=f'{amount:.2f}'
    approval=await mcp_tool('request_pix',{'customer_id':r.customer_id,
        'beneficiary_id':beneficiary['id'],'amount':amount_str})
    return AgentResponse(agent='pix',message=phrase(r.language,
        f"Pix of {money(amount,'en')} to {beneficiary['name']} is awaiting your explicit approval. This is a simulation.",
        f"Pix de {money(amount,'es')} para {beneficiary['name']} espera tu autorización expresa. Es una simulación."),
        data={'beneficiary':beneficiary,'amount':amount_str},
        next_actions=[{'approval_id':approval['id'],'action':'execute_pix',
            'label':phrase(r.language,'Authorize Pix '+money(amount,'en'),'Autorizar Pix '+money(amount,'es')),
            'details':{'amount':amount_str,'beneficiary':beneficiary['name']}}])
async def wellness(r):
    s=await mcp_tool('get_banking_snapshot',{'customer_id':r.customer_id})
    from datetime import date
    today=date.today()
    due=sum((Decimal(str(o['amount'])) for o in s['obligations'] if date.fromisoformat(o['due'])>=today),Decimal('0'))
    balance=Decimal(str(s['account']['balance']))
    rag=await grounded_answer('wellness',r.language,r.message,
        {'checking_balance_brl':f'{balance:.2f}','upcoming_obligations_brl':f'{due:.2f}',
         'projected_remaining_brl':f'{balance-due:.2f}'})
    return AgentResponse(agent='wellness',message=rag['answer'],
        data={'balance':float(balance),'obligations':float(due),'projected':float(balance-due),
              'citations':rag['citations'],'retrieval_method':rag['retrieval_method']})
ROLES={'account':account,'card':card,'dispute':dispute,'pix':pix,'wellness':wellness}
ROLE=os.getenv('AGENT_ROLE','account')
app=application(ROLE,ROLES.get(ROLE,account))
class ExecuteRequest(BaseModel):
    customer_id:str='C001'
    approval_id:str
    payload:dict
@app.post('/execute')
async def execute(req:ExecuteRequest,x_internal_key:str|None=Header(None)):
    require_secret(x_internal_key,'SPECIALIST_EXECUTION_KEY')
    mapping={'card':('execute_approved_card_block',['card_id']),
             'dispute':('execute_approved_dispute',['transaction_id']),
             'pix':('execute_approved_pix',['beneficiary_id','amount','idempotency_key'])}
    if ROLE not in mapping:raise HTTPException(403,'Read-only specialist')
    tool,fields=mapping[ROLE]
    if not all(k in req.payload for k in fields):raise HTTPException(422,'Missing operation fields')
    args={'customer_id':req.customer_id,'approval_id':req.approval_id,**{k:req.payload[k] for k in fields}}
    try:return await mcp_tool(tool,args)
    except Exception as exc:
        raise HTTPException(502,f'Governed banking tool execution failed: {type(exc).__name__}') from exc
