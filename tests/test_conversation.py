"""Regression tests for truthful bilingual post-operation messages.

Pure functions are deliberately testable without a running MCP or LLM provider.
"""
from decimal import Decimal
import asyncio

from agents.confirmation import parse_brl_amount, response, confirmed, money


def snapshot(balance=7200, paid=True):
    payment = [{'id': 'PIX-123', 'amount': 1250.0, 'status': 'completed'}] if paid else []
    return [
        {'agent': 'account', 'data': {'account': {'balance': balance},
          'beneficiaries': [{'id': 'BEN001', 'name': 'Condomínio Jardim do Sol'}],
          'payments': payment}},
        {'agent': 'wellness', 'data': {'balance': balance,
          'obligations': 2100.0, 'projected': balance - 2100.0}},
    ]


def test_correct_payment_confirmation_in_english():
    event = {'id': 'PIX-123', 'amount': 1250.0, 'beneficiary_id': 'BEN001', 'status': 'completed'}
    msg, verified = response('execute_pix', event, snapshot(), 'en')
    assert verified
    for text in ('R$ 1,250.00', 'R$ 7,200.00', 'R$ 2,100.00', 'R$ 5,100.00', 'Condomínio Jardim do Sol', 'PIX-123'):
        assert text in msg
    assert '{' not in msg and 'idempotency_key' not in msg


def test_spanish_uses_spanish_and_currency_convention():
    event = {'id': 'PIX-123', 'amount': '1250.00', 'beneficiary_id': 'BEN001', 'status': 'completed'}
    msg, verified = response('execute_pix', event, snapshot(), 'es')
    assert verified
    assert 'Tu pago Pix' in msg and 'R$ 1.250,00' in msg
    assert 'R$ 7.200,00' in msg and 'R$ 5.100,00' in msg
    assert 'Your Pix payment' not in msg


def test_stale_balance_or_missing_receipt_never_appears_verified():
    event = {'id': 'PIX-123', 'amount': '1250.00', 'beneficiary_id': 'BEN001', 'status': 'completed'}
    inconsistent = snapshot()
    inconsistent[1]['data']['projected'] = 6350
    msg, verified = response('execute_pix', event, inconsistent, 'en')
    assert not verified and 'could not verify' in msg
    missing = snapshot(paid=False)
    msg, verified = response('execute_pix', event, missing, 'en')
    assert not verified
    assert 'could not verify' in msg and 'R$ 7,200.00' not in msg


def test_amount_parsing_does_not_default_to_1250():
    for text in ('Pay R$1,250', 'Paga R$1.250,00', 'Transfer BRL 1250.00'):
        assert parse_brl_amount(text) == Decimal('1250.00')
    assert parse_brl_amount('How does Pix work?') is None
    assert parse_brl_amount('Pay 300 to the condominium') is None
    assert parse_brl_amount('Pay R$0') is None
    assert parse_brl_amount('Pay R$99,99') == Decimal('99.99')


def test_completion_requires_recognized_status():
    assert confirmed('execute_pix', {'id':'PIX-123','status':'completed'})
    assert not confirmed('execute_pix', {'id':'PIX-123','status':'pending'})
    assert not confirmed('execute_pix', {'status':'completed'})
    assert not confirmed('fake', {'id':'X','status':'completed'})


def test_offline_wellness_explains_cash_flow_without_raw_internal_keys(monkeypatch):
    import pytest
    pytest.importorskip('openai')
    from agents.retrieval import grounded_answer
    monkeypatch.delenv('OPENAI_API_KEY', raising=False)
    facts = {'checking_balance_brl':'8450.00','upcoming_obligations_brl':'2100.00',
             'projected_remaining_brl':'6350.00'}
    en = asyncio.run(grounded_answer('wellness', 'en', 'Will I have enough money?', facts))['answer']
    es = asyncio.run(grounded_answer('wellness', 'es', '¿Tendré suficiente dinero?', facts))['answer']
    assert 'R$ 6,350.00' in en and '[POL-FIN-001, v1.0]' in en
    assert 'R$ 6.350,00' in es and '[POL-FIN-001, v1.0]' in es
    assert 'checking_balance_brl:' not in en and 'checking_balance_brl:' not in es
