"""Implementation-level readiness checks. No running Agent Manager required."""
from pathlib import Path
import asyncio, sys, types
import pytest
from agents.retrieval import active_versions,retrieve_policy,grounded_answer,optional_policy_explanation
from banking_common.telemetry import business_span,outbound_headers
from agents.confirmation import response

ROOT=Path(__file__).parents[1]

def test_policy_versions_never_use_expired_or_future_versions(monkeypatch):
    monkeypatch.setenv('POLICY_AS_OF_DATE','2026-10-09')
    active=retrieve_policy('dispute','en','Eligibility to dispute a posted transaction',top_k=20)
    eligibility=[d for d in active if d['id']=='POL-DIS-002']
    assert len(eligibility)==1
    assert eligibility[0]['version']=='1.0'
    assert all(d['effective']<='2026-10-09' for d in active)
    assert all(not d.get('expires') or d['expires']>'2026-10-09' for d in active)

def test_future_policy_activates_only_after_effective_date(monkeypatch):
    monkeypatch.setenv('POLICY_AS_OF_DATE','2028-04-10')
    result=retrieve_policy('dispute','es','Eligibility',top_k=30)
    assert next(d for d in result if d['id']=='POL-DIS-002')['version']=='2.0'

def test_managed_embeddings_cannot_use_direct_openai_key(monkeypatch):
    from agents.retrieval import semantic_retrieve
    monkeypatch.setenv('WSO2_MODE','true')
    monkeypatch.setenv('OPENAI_API_KEY','should-never-be-used')
    monkeypatch.setenv('OPENAI_EMBEDDING_MODEL','text-embedding-3-small')
    monkeypatch.delenv('EMBEDDING_PROVIDER_URL',raising=False)
    monkeypatch.delenv('EMBEDDING_PROVIDER_KEY',raising=False)
    assert asyncio.run(semantic_retrieve('wellness','en','cashflow')) is None

def test_model_explanation_is_non_transactional_and_validated(monkeypatch):
    common=types.SimpleNamespace(llm=None)
    monkeypatch.setitem(sys.modules,'agents.common',common)
    monkeypatch.setenv('LLM_GROUNDED_ANSWERS','true')
    async def safe(system,user):
        assert 'retrieved_policy_evidence' in user
        return 'A card dispute opens a review rather than automatically issuing a refund.'
    monkeypatch.setattr(common,'llm',safe)
    facts={'merchant':'Orion Digital','amount_brl':'1280.00'}
    result=asyncio.run(grounded_answer('dispute','en','dispute Orion',facts))
    assert result['generation_mode']=='governed_llm_supplement'
    assert 'Orion Digital' in result['answer']
    assert 'R$ 1,280.00' in result['answer']
    assert result['citations']
    async def unsafe(system,user):
        return 'You are guaranteed R$1,280 by tomorrow.'
    monkeypatch.setattr(common,'llm',unsafe)
    result=asyncio.run(grounded_answer('dispute','en','dispute Orion',facts))
    assert result['generation_mode']=='deterministic_grounded'
    assert 'guaranteed R$' not in result['answer']

def test_managed_model_gateway_rejection_propagates(monkeypatch):
    common=types.SimpleNamespace(llm=None)
    monkeypatch.setitem(sys.modules,'agents.common',common)
    monkeypatch.setenv('LLM_GROUNDED_ANSWERS','true')
    async def denied(*args):raise RuntimeError('gateway denied')
    monkeypatch.setattr(common,'llm',denied)
    with pytest.raises(RuntimeError,match='gateway denied'):
        asyncio.run(grounded_answer('dispute','en','card dispute',{}))

def test_payment_confirmation_without_beneficiary_directory():
    outcome={'id':'PIX-PROOF','amount':1250,'beneficiary_id':'BEN001','status':'completed'}
    results=[{'agent':'account','data':{'account':{'balance':7200},'payments':[
        {'id':'PIX-PROOF','amount':1250,'status':'completed','beneficiary_name':'Condomínio Jardim do Sol'}]}},
        {'agent':'wellness','data':{'balance':7200,'obligations':2100,'projected':5100}}]
    text,verified=response('execute_pix',outcome,results,'en')
    assert verified and 'Condomínio Jardim do Sol' in text and 'R$ 5,100.00' in text
    assert 'beneficiaries' not in results[0]['data']

def test_span_exceptions_are_not_swallowed():
    with pytest.raises(ValueError,match='important failure'):
        with business_span('aurora.test.safe',**{'aurora.test':True}):
            raise ValueError('important failure')
    assert 'X-Correlation-ID' in outbound_headers()

def test_kubernetes_supports_case_operators():
    text=(ROOT/'deployment/agent-manager/k8s/supporting-services.yaml').read_text()
    assert text.count('name: BANK_CASE_OPERATOR_KEY')>=2
    assert 'name: DEMO_OPERATOR_CODE' in text

def test_data_minimization_in_account_and_upstream_mcp():
    specialist=(ROOT/'agents/specialists.py').read_text()
    account=specialist.split('async def account(r):',1)[1].split('async def card(r):',1)[0]
    assert "mcp_tool('list_beneficiaries'" not in account
    upstream=(ROOT/'mcp_server/app.py').read_text()
    assert "'beneficiary_name'" in upstream
