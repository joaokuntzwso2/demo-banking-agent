"""Conservative intent and entity resolution for demo banking actions.

The LLM may select specialists, but it must NEVER grant permission for a write.
Only a clear positive command plus a uniquely identified banking resource
can request a pending approval. Financial mutations still require authorization.
"""
import re
from dataclasses import dataclass
from .confirmation import parse_brl_amount

INFO = re.compile(r'\b(how (do|does|can|to)|what (is|are)|tell me (about|how)|explain|can you explain|qué es|que es|cómo|como funciona|explica|explícame|información|informacion|ayuda con)\b',re.I)
NEGATED_COMMAND = {
 'block_card': re.compile(r"\b(?:do not|don.t|dont|never|not|no quiero|no deseo|no|sin)\s+(?:\w+\s+){0,3}(?:block|freeze|bloquear|bloquees|bloquea|congelar)\b",re.I),
 'open_dispute': re.compile(r"\b(?:do not|don.t|dont|never|not|no quiero|no deseo|no|sin)\s+(?:\w+\s+){0,3}(?:dispute|claim|reclama|reclamar|reclamación|disputa)\b",re.I),
 'execute_pix': re.compile(r"\b(?:do not|don.t|dont|never|not|no quiero|no deseo|no|sin)\s+(?:\w+\s+){0,3}(?:pay|send|transfer|pagar|pagues|paga|enviar|transferir)\b",re.I),
}
COMMANDS = {
 'block_card':re.compile(r'\b(block|freeze|lock|bloquea(?:r)?|congela(?:r)?|suspende(?:r)?)\b',re.I),
 'open_dispute':re.compile(r'\b(dispute|file (a )?claim|open (a )?dispute|challenge (the|this) charge|abre (una )?reclamaci[oó]n|reclama(?:r)?|impugna(?:r)?|disputa(?:r)?)\b',re.I),
 'execute_pix':re.compile(r'\b(pay|send|transfer|paga(?:r)?|env[ií]a(?:r)?|transfiere|transferir)\b',re.I),
}

@dataclass(frozen=True)
class Intent:
    action: str|None
    reason: str

def action_intent(message, action):
    text=message.strip()
    if NEGATED_COMMAND[action].search(text):return Intent(None,'negated')
    if INFO.search(text) and not re.search(r'\b(and|then|also|adem[aá]s|y luego)\b',text,re.I):return Intent(None,'informational')
    if COMMANDS[action].search(text):return Intent(action,'explicit_positive_command')
    return Intent(None,'no_explicit_command')

def resolve_card(cards,message):
    digits=re.findall(r'(?<!\d)\d{4}(?!\d)',message)
    matching=[c for c in cards if c.get('last4') in digits] if digits else list(cards)
    return matching[0] if len(matching)==1 else None

def resolve_transaction(transactions,message):
    text=message.casefold()
    # Merchant name, transaction id, or an explicit matching amount is required.
    candidates=[]
    for tx in transactions:
        if tx.get('kind')!='card':continue
        merchant=(tx.get('merchant') or '').casefold()
        if (merchant and merchant in text) or tx['id'].casefold() in text:
            candidates.append(tx)
    if candidates:
        return candidates[0] if len(candidates)==1 else None
    amount=parse_brl_amount(message)
    if amount is not None:
        candidates=[x for x in transactions if x.get('kind')=='card' and str(x['amount']) and abs(float(x['amount'])-float(amount))<0.001]
        return candidates[0] if len(candidates)==1 else None
    return None

def resolve_beneficiary(beneficiaries,message):
    text=message.casefold()
    verified=[b for b in beneficiaries if b.get('verified')]
    named=[b for b in verified if b['name'].casefold() in text or b['id'].casefold() in text]
    if len(named)==1:return named[0]
    if len(named)>1:return None
    # 'my verified condominium beneficiary' identifies category only if unique.
    if 'condom' in text:
        named=[b for b in verified if 'condom' in b['name'].casefold()]
        if len(named)==1:return named[0]
    # Do NOT silently select an arbitrary first verified beneficiary.
    return None
