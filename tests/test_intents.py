from agents.intents import action_intent,resolve_transaction,resolve_beneficiary,resolve_card
from agents.confirmation import parse_brl_amount

def test_negative_and_informational_never_prepare_mutation():
    cases=[('Do not pay R$1,250 to my condominium','execute_pix'),
           ('How does Pix work?','execute_pix'),('No pagues R$1.250 por Pix','execute_pix'),
           ('I am not ready to pay R$200','execute_pix'),('Do not block my card','block_card'),('No bloquees la tarjeta','block_card'),
           ('Tell me how to dispute a charge','open_dispute'),
           ('Do not dispute this transaction','open_dispute')]
    for text,action in cases:assert action_intent(text,action).action is None,(text,action)

def test_fraud_statement_with_dont_recognize_still_allows_explicit_block():
    assert action_intent("I don't recognize Orion Digital. Block my card and open a dispute.",'block_card').action
    assert action_intent("I don't recognize Orion Digital. Block my card and open a dispute.",'open_dispute').action

def test_card_and_dispute_require_unambiguous_target():
    cards=[{'id':'C1','last4':'4421'},{'id':'C2','last4':'2277'}]
    assert resolve_card(cards,'block card') is None
    assert resolve_card(cards,'block 4421')['id']=='C1'
    tx=[{'id':'T1','merchant':'Orion Digital','amount':1280,'kind':'card'},
        {'id':'T2','merchant':'Other','amount':1280,'kind':'card'}]
    assert resolve_transaction(tx,'dispute R$1,280') is None
    assert resolve_transaction(tx,'dispute Orion Digital')['id']=='T1'

def test_beneficiary_must_be_named_or_uniquely_identified():
    beneficiaries=[{'id':'B1','name':'Condomínio Jardim do Sol','verified':1},
                   {'id':'B2','name':'Other shop','verified':1}]
    assert resolve_beneficiary(beneficiaries,'pay my verified beneficiary') is None
    assert resolve_beneficiary(beneficiaries,'pay condo Pix') is None
    assert resolve_beneficiary(beneficiaries,'pay Condomínio Jardim do Sol')['id']=='B1'

def test_multiple_money_amounts_need_clarification():
    assert parse_brl_amount('Card charge R$1,280, pay R$1,250 by Pix') is None
