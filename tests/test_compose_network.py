"""Guard against mistakenly injecting host localhost URLs into Docker services.

Host localhost port mappings are for the presenter only; containers need
service DNS and the hybrid on-prem Compose uses a separate configuration.
"""
from pathlib import Path
import yaml

ROOT = Path(__file__).resolve().parents[1]

def test_local_compose_internal_dns_is_stable():
    config=yaml.safe_load((ROOT/'compose.yaml').read_text())['services']
    assert config['portal']['environment']['CONCIERGE_URL']=='http://concierge:8200'
    for role in ('account','card','dispute','pix','wellness'):
        assert config['concierge']['environment']['AGENT_'+role.upper()+'_URL']==f'http://{role}:8200'
    assert config['mcp']['environment']['BANK_CORE_URL']=='http://core:8100'


def test_browser_has_no_hardcoded_session_or_gateway_key():
    js=(ROOT/'demo_portal/static/app.js').read_text()
    assert 'local-demo-session' not in js
    assert 'X-Internal-Key' not in js
    assert 'X-API-Key' not in js
    assert 'AMP_AGENTID_CLIENT_SECRET' not in js
