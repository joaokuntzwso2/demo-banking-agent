"""Specialists never approve their own writes; tools are MCP-governed."""
import os,hmac,re
from decimal import Decimal
from fastapi import Header,HTTPException
from pydantic import BaseModel
from banking_common.security import require_secret
from .common import *
from .confirmation import parse_brl_amount,money
from .intents import action_intent,resolve_card,resolve_transaction,resolve_beneficiary,resolve_dispute,asks_dispute_status,wants_human
from .retrieval import grounded_answer

def phrase(lang,en,es):return es if lang=='es' else en
async def account(r):
    acct=await mcp_tool('get_account_balance',{'customer_id':r.customer_id})
    activity=await mcp_tool('list_account_transactions',{'customer_id':r.customer_id})
    disputes=await mcp_tool('get_dispute_status',{'customer_id':r.customer_id})
    balance=acct['account']['balance']
    return AgentResponse(agent='account',message=phrase(r.language,
        f"Your checking balance is {money(balance,'en')}. Credit-card charges do not immediately debit checking.",
        f"El saldo de tu cuenta es {money(balance,'es')}. Los cargos de tarjeta no se debitan inmediatamente de la cuenta."),
        data={'account':acct['account'],'transactions':activity['transactions'],
              'payments':activity['payments'],'disputes':disputes['disputes']})

async def card(r):
    cards=(await mcp_tool('get_card_status',{'customer_id':r.customer_id}))['cards']
    chosen=resolve_card(cards,r.message)
    if not chosen:return AgentResponse(agent='card',message=phrase(r.language,
        'Which card? Please provide its last four digits.',
        '¿Qué tarjeta? Indica sus últimos cuatro dígitos.'))
    message=phrase(r.language,f"Card ••••{chosen['last4']} is {chosen['status']}.",
                   f"La tarjeta ••••{chosen['last4']} está {chosen['status']}.")
    actions=[]
    if chosen['status']=='active' and action_intent(r.message,'block_card').action:
        ap=await mcp_tool('request_card_block',{'customer_id':r.customer_id,'card_id':chosen['id']})
        actions=[{'approval_id':ap['id'],'action':'block_card',
                  'label':phrase(r.language,f"Block ••••{chosen['last4']}",f"Bloquear ••••{chosen['last4']}"),
                  'details':{'card_last4':chosen['last4']}}]
        message+=phrase(r.language,' Explicit approval is required.',' Se requiere autorización expresa.')
    return AgentResponse(agent='card',message=message,data={'card':chosen},next_actions=actions)

async def dispute(r):
    disputes=(await mcp_tool('get_dispute_status',{'customer_id':r.customer_id}))['disputes']
    # A dispute ID identifies a dispute record, not a card transaction. Query
    # the existing customer-scoped MCP read tool before any mutation planning.
    if asks_dispute_status(r.message) and not wants_human(r.message):
        selected=resolve_dispute(disputes,r.message)
        supplied_id=bool(re.search(r'\b[A-Z]{2,12}[-_][A-Z0-9][A-Z0-9_-]{1,80}\b',r.message,re.I))
        if selected is None and not supplied_id and len(disputes)==1:
            selected=disputes[0]
        if selected:
            identifier=selected['id']
            status=str(selected.get('status') or 'unknown').replace('_',' ')
            status_es={'under review':'en revisión','open':'abierta','pending':'pendiente',
                       'resolved':'resuelta','closed':'cerrada','rejected':'rechazada'}.get(status,status)
            msg=phrase(r.language,
                f'Dispute {identifier} is currently {status}. This is the recorded status, not a promise of refund or resolution.',
                f'La reclamación {identifier} está actualmente en estado {status_es}. Es el estado registrado, no una promesa de reembolso ni resolución.')
        elif disputes:
            # Never resolve an unknown identifier by using a different customer's
            # record or silently choosing a different dispute.
            msg=phrase(r.language,
                'I could not match that dispute ID to your account. Please check the full reference.' if supplied_id else
                'You have multiple disputes. Please provide the exact dispute ID.',
                'No encontré ese ID de reclamación en tu cuenta. Verifica la referencia completa.' if supplied_id else
                'Tienes varias reclamaciones. Indica el ID exacto de la reclamación.')
        else:
            msg=phrase(r.language,
                'No disputes are recorded for your account.',
                'No hay reclamaciones registradas en tu cuenta.')
        return AgentResponse(agent='dispute',message=msg,
            data={'dispute':selected},next_actions=[])
    transactions=(await mcp_tool('list_card_transactions',{'customer_id':r.customer_id}))['transactions']
    cases=(await mcp_tool('get_human_cases',{'customer_id':r.customer_id}))['service_cases']
    tx=resolve_transaction(transactions,r.message)
    rag=await grounded_answer('dispute',r.language,r.message,{'transaction_id':tx['id'] if tx else None,
        'merchant':tx['merchant'] if tx else None,'amount_brl':tx['amount'] if tx else None})
    message=rag['answer'];actions=[];opened=None
    if wants_human(r.message):
        # The customer explicitly requests a person; creation of a service case
        # does not approve a financial operation or resolve the dispute.
        reason='Customer requests a human review of banking dispute' + (f" {tx['id']}" if tx else '')
        opened=await mcp_tool('request_human_escalation',{'customer_id':r.customer_id,
            'reason':reason,'priority':'high' if ('urgent' in r.message.lower() or 'urgente' in r.message.lower()) else 'normal'})
        message+='\n\n'+phrase(r.language,
            f"I opened human-support case {opened['id']}. A human agent will review it; no resolution has been promised.",
            f"Abrí el caso de atención humana {opened['id']}. Una persona lo revisará; no se garantiza una resolución.")
    elif action_intent(r.message,'open_dispute').action:
        if not tx:
            message+='\n\n'+phrase(r.language,
                'Identify the card transaction by merchant, ID or exact amount before filing.',
                'Identifica el cargo por comercio, ID o importe exacto antes de reclamar.')
        elif any(d['transaction_id']==tx['id'] for d in disputes):
            message+='\n'+phrase(r.language,'An existing dispute already covers this transaction.',
                'Ya existe una reclamación para esta transacción.')
        else:
            approval=await mcp_tool('request_dispute',{'customer_id':r.customer_id,'transaction_id':tx['id']})
            actions=[{'approval_id':approval['id'],'action':'open_dispute',
                      'label':phrase(r.language,f"Dispute {tx['merchant']} ({money(tx['amount'],'en')})",
                                          f"Reclamar {tx['merchant']} ({money(tx['amount'],'es')})"),
                      'details':{'merchant':tx['merchant'],'amount':tx['amount']}}]
    return AgentResponse(agent='dispute',message=message,
        data={'transaction':tx,'disputes':disputes,'service_cases':cases,'opened_case':opened,
              'citations':rag['citations'],'retrieval_method':rag['retrieval_method']},next_actions=actions)

async def pix(r):
    from .intents import resolve_beneficiary
    from .intents import action_intent
    from .confirmation import parse_brl_amount
    account=(await mcp_tool('get_account_balance',{'customer_id':r.customer_id}))['account']
    benlist=(await mcp_tool('list_beneficiaries',{'customer_id':r.customer_id}))['beneficiaries']
    intent=action_intent(r.message,'execute_pix')
    if not intent.action:
        return AgentResponse(agent='pix',message=phrase(r.language,
            'I can explain Pix or prepare a payment, but I will not initiate a transfer without an explicit instruction.',
            'Puedo explicar Pix o preparar un pago, pero no iniciaré una transferencia sin una orden explícita.'),
            data={'intent_reason':intent.reason})
    amount=parse_brl_amount(r.message)
    if amount is None:
        return AgentResponse(agent='pix',message=phrase(r.language,
            'Specify an exact BRL payment amount, for example R$1,250.',
            'Indica un importe exacto en BRL, por ejemplo R$1.250.'))
    if amount>Decimal('2500'):
        return AgentResponse(agent='pix',message=phrase(r.language,
            'This exceeds the simulator payment limit of R$2,500; nothing was requested.',
            'Supera el límite simulado de R$2.500; no se solicitó ningún pago.'))
    # Detect known unverified recipients before selecting a verified beneficiary.
    lower=r.message.casefold()
    unverified=[b for b in benlist if not b['verified'] and
                (b['name'].casefold() in lower or b['id'].casefold() in lower)]
    if unverified:
        return AgentResponse(agent='pix',message=phrase(r.language,
            f"I cannot transfer to {unverified[0]['name']}: this beneficiary is not verified. No approval was created. Contact the bank to verify it.",
            f"No puedo transferir a {unverified[0]['name']}: el beneficiario no está verificado. No se creó una aprobación. Contacta con el banco."),
            data={'risk_decision':'deny_unverified_beneficiary','beneficiary_id':unverified[0]['id']})
    if any(k in lower for k in ('new pix key','unknown pix key','nueva clave pix','nueva llave pix')):
        return AgentResponse(agent='pix',message=phrase(r.language,
            'A new Pix key is not a verified beneficiary. First complete beneficiary verification; no payment is authorized.',
            'Una nueva clave Pix no es un beneficiario verificado. Primero debes verificarlo; no se autorizó el pago.'),
            data={'risk_decision':'deny_unverified_beneficiary'})
    beneficiary=resolve_beneficiary(benlist,r.message)
    if not beneficiary:
        return AgentResponse(agent='pix',message=phrase(r.language,
            'Which verified beneficiary? Please name the beneficiary.',
            '¿Qué beneficiario verificado? Indica su nombre.'),
            data={'verified_beneficiaries':[{'name':b['name'],'id':b['id']} for b in benlist if b['verified']]})
    if Decimal(str(account['balance']))<amount:
        return AgentResponse(agent='pix',message=phrase(r.language,
            'Insufficient checking funds. No payment requested.',
            'Saldo insuficiente. No se solicitó ningún pago.'))
    amount_str=f'{amount:.2f}'
    ap=await mcp_tool('request_pix',{'customer_id':r.customer_id,
        'beneficiary_id':beneficiary['id'],'amount':amount_str})
    return AgentResponse(agent='pix',message=phrase(r.language,
        f"Simulated Pix {money(amount,'en')} to {beneficiary['name']} awaits your explicit approval.",
        f"El Pix simulado de {money(amount,'es')} a {beneficiary['name']} espera tu autorización."),
        data={'beneficiary':beneficiary,'amount':amount_str},
        next_actions=[{'approval_id':ap['id'],'action':'execute_pix',
                       'label':phrase(r.language,'Authorize '+money(amount,'en'),'Autorizar '+money(amount,'es')),
                       'details':{'amount':amount_str,'beneficiary':beneficiary['name']}}])

async def wellness(r):
    account=(await mcp_tool('get_account_balance',{'customer_id':r.customer_id}))['account']
    obligations=(await mcp_tool('get_upcoming_obligations',{'customer_id':r.customer_id}))['obligations']
    from datetime import date
    due=sum((Decimal(str(o['amount'])) for o in obligations if date.fromisoformat(o['due'])>=date.today()),Decimal('0'))
    balance=Decimal(str(account['balance']))
    rag=await grounded_answer('wellness',r.language,r.message,
        {'checking_balance_brl':f'{balance:.2f}','upcoming_obligations_brl':f'{due:.2f}',
         'projected_remaining_brl':f'{balance-due:.2f}'})
    hypothetical=parse_brl_amount(r.message)
    phrase_markers=('what if','what happens if','if i pay','would i have','si pago','si pagara','si pagase','qué pasaría si','que pasaria si','me quedaría si')
    use_what_if=hypothetical is not None and any(x in r.message.casefold() for x in phrase_markers)
    if use_what_if:
        after=balance-due-hypothetical
        outcome=phrase(r.language,
            f"What-if only: after a hypothetical {money(hypothetical,'en')} payment and {money(due,'en')} of recorded bills, your projected checking funds would be {money(after,'en')}. No payment was initiated.",
            f"Solo simulación: tras un pago hipotético de {money(hypothetical,'es')} y {money(due,'es')} de facturas, quedarían {money(after,'es')} en cuenta. No se inició ningún pago.")
        message=outcome+' '+f"[POL-FIN-001, v1.0]"
    else:message=rag['answer']
    return AgentResponse(agent='wellness',message=message,
        data={'balance':float(balance),'obligations':float(due),'projected':float(balance-due),
              'hypothetical_payment':float(hypothetical) if use_what_if else None,
              'hypothetical_remaining':float(balance-due-hypothetical) if use_what_if else None,
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
    if req.customer_id != 'C001':raise HTTPException(403,'Demo customer binding mismatch')
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
