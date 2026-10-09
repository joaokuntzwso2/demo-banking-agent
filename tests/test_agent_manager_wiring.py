"""Source/contract smoke tests independent of running WSO2 itself."""
from pathlib import Path
from fastapi.testclient import TestClient
import yaml

ROOT=Path(__file__).resolve().parents[1]


def test_separate_git_build_folders_are_self_contained():
    for role in ('concierge','account','card','dispute','pix','wellness'):
        p=ROOT/'deployment'/'agent-manager'/'apps'/('aurora-'+role)
        for required in ('requirements.txt','main.py','agents/common.py','banking_common/config.py','knowledge/policies.json'):
            assert (p/required).exists(), f'{role}: missing {required}'
        main=(p/'main.py').read_text()
        assert 'port=int(os.getenv("PORT", "8000"))' in main
        assert f'AGENT_ROLE"] = "{role}"' in main


def test_mcp_upstream_is_standalone_with_15_named_tools():
    folder=ROOT/'deployment'/'agent-manager'/'upstream-mcp'
    assert (folder/'Dockerfile').exists()
    text=(folder/'mcp_server/app.py').read_text()
    for name in ('get_account_balance','list_beneficiaries','get_human_cases','request_human_escalation','request_card_block','execute_approved_card_block',
                 'request_dispute','execute_approved_dispute','request_pix','execute_approved_pix'):
        assert f'async def {name}(' in text


def test_hybrid_compose_no_locally_running_agents():
    config=yaml.safe_load((ROOT/'deployment'/'agent-manager'/'compose.portal.yaml').read_text())
    assert set(config['services']) == {'db','core','mcp','portal'}
    assert 'CONCIERGE_URL' in config['services']['portal']['environment']
    assert 'CONCIERGE_API_KEY' in config['services']['portal']['environment']


def test_supporting_manifest_does_not_claim_to_deploy_agents():
    docs=list(yaml.safe_load_all((ROOT/'deployment'/'agent-manager'/'k8s'/'supporting-services.yaml').read_text()))
    assert len(docs)==6
    assert {x['metadata']['name'] for x in docs} == {
       'aurora-core','aurora-mcp','aurora-portal'}
    assert {x['kind'] for x in docs} == {'Deployment','Service'}


def test_bff_only_calls_concierge_for_approvals(monkeypatch):
    import demo_portal.app as portal
    from unittest.mock import patch
    calls=[]
    class FakeResponse:
        status_code=200
        text=''
        def json(self):
            return {'approval':{'status':'approved'},'result':{'id':'PIX-1'},'message':'Payment done'}
    class FakeClient:
        def __init__(self,**kwargs):pass
        async def __aenter__(self):return self
        async def __aexit__(self,*args):pass
        async def request(self,method,url,**kwargs):
            calls.append((method,url,kwargs));return FakeResponse()
    monkeypatch.setattr(portal.httpx,'AsyncClient',FakeClient)
    monkeypatch.setattr(portal,'CONCIERGE','https://amp.example/aurora-concierge')
    monkeypatch.setenv('CONCIERGE_API_KEY','test-gateway-key')
    client=TestClient(portal.app)
    unauth=client.post('/api/approvals/APR-1/decide',json={'approved':True})
    assert unauth.status_code==401
    login=client.post('/api/login',json={'access_code':'aurora-local-demo-only'})
    assert login.status_code==200
    csrf=login.json()['csrf_token']
    no_csrf=client.post('/api/approvals/APR-1/decide',json={'approved':True})
    assert no_csrf.status_code==403
    res=client.post('/api/approvals/APR-1/decide',headers={'X-CSRF-Token':csrf},
                    json={'approved':True,'language':'en','original_request':'pay'})
    assert res.status_code==200
    assert calls[0][1]=='https://amp.example/aurora-concierge/approve'
    assert calls[0][2]['headers']['X-API-Key']=='test-gateway-key'
    assert calls[0][2]['json']['approval_id']=='APR-1'
    assert calls[0][2]['json']['workflow_id']
    assert 'card' not in calls[0][1] and 'pix' not in calls[0][1]
