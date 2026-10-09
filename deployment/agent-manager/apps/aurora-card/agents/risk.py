"""Optional externally hosted, read-only beneficiary risk advisor.

This service can be registered in Agent Manager as an External agent.
It never approves a payment: Pix and Core enforce verification independently.
"""
from .common import application,AgentResponse,mcp_tool

async def evaluate(req):
    directory=(await mcp_tool('list_beneficiaries',{'customer_id':req.customer_id}))['beneficiaries']
    text=req.message.casefold()
    identified=[b for b in directory if b['name'].casefold() in text or b['id'].casefold() in text]
    lang='es' if req.language=='es' else 'en'
    if len(identified)==1:
        b=identified[0];ok=bool(b['verified']);status='verified' if ok else 'unverified'
        if lang=='es':
            message=(f"Beneficiario {b['name']}: {'verificado' if ok else 'NO verificado'}. "
                     +('La validación no garantiza ausencia de fraude.' if ok else
                       'No autorices la transferencia sin una verificación independiente.'))
        else:
            message=(f"Beneficiary {b['name']}: {status}. "
                     +('Verification does not guarantee absence of fraud.' if ok else
                       'Do not authorize a transfer before independent verification.'))
    else:
        status='unknown';message=('Destinatario no identificado. No se puede confirmar su verificación.'
                  if lang=='es' else 'Recipient not identified. Verification cannot be established.')
    return AgentResponse(agent='risk',message=message,data={
        'advisory_risk_status':status,'rule':'beneficiary_verification_only',
        'authorizes_payment':False})

app=application('risk',evaluate)
