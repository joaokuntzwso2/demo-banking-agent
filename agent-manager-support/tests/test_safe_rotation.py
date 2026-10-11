"""Regression: only rotate a verified Banco Aurora Quick Tunnel registration."""
from pathlib import Path
import importlib.util
import copy
import os
import unittest
from unittest.mock import patch

P = Path(__file__).resolve().parents[1] / 'publish_proxy.py'
spec=importlib.util.spec_from_file_location('aurora_rotation_testing',P)
mod=importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
OLD='https://cult-socks-zone-isle.trycloudflare.com/mcp'
NEW='https://another-local-demo.trycloudflare.com/mcp'
UUID='3ca08496-7c42-4ef5-969f-eeb658bbef91'

def make_tools():
    return [{'name':name,'inputSchema':{'type':'object','properties':{}}} for name in mod.ACTIONS]

class SafeRotation(unittest.TestCase):
    def setUp(self):
        with patch.dict(os.environ,{'AURORA_MCP_ORIGIN_KEY':'a'*48}):
            self.desired=mod.build_proxy_payload('aurora-banking',NEW,UUID,make_tools())
        self.original=copy.deepcopy(self.desired)
        self.original['endpoints'][0]['upstream']['main']['url']=OLD
        self.original['endpoints'][0]['upstream']['main']['auth']={
            'type':'api-key','header':'X-Aurora-Origin-Key'}
        self.path='/orgs/default/mcp-proxies/aurora-banking'
        self.scopes=[{'action':action,'tools':[name]}
                     for name,action in mod.ACTIONS.items()]

    def fake_server(self):
        state={'proxy':copy.deepcopy(self.original),'writes':[]}
        def call(path,method='GET',payload=None,allow_missing=False):
            if path==self.path+'/scopes':return {'scopes':self.scopes}
            if path!=self.path:raise AssertionError(path)
            if method=='GET':return copy.deepcopy(state['proxy'])
            if method=='PUT':
                state['writes'].append(copy.deepcopy(payload))
                # Mimic AMP's GET which redacts upstream auth.value
                state['proxy']=copy.deepcopy(payload)
                del state['proxy']['endpoints'][0]['upstream']['main']['auth']['value']
                return {'ok':True}
            raise AssertionError(method)
        return state,call

    def test_rotates_known_repo_proxy_and_confirms_get(self):
        state,call=self.fake_server()
        with patch.object(mod,'apicall',side_effect=call), patch.dict(os.environ,{'AURORA_ALLOW_EPHEMERAL_PROXY_UPDATE':'true'}):
            self.assertTrue(mod.ensure_known_proxy(self.path,NEW,'aurora-banking',self.desired))
        self.assertEqual(len(state['writes']),1)
        self.assertEqual(state['proxy']['endpoints'][0]['upstream']['main']['url'],NEW)

    def test_rotation_requires_explicit_opt_in(self):
        state,call=self.fake_server()
        with patch.object(mod,'apicall',side_effect=call), patch.dict(os.environ,{'AURORA_ALLOW_EPHEMERAL_PROXY_UPDATE':'false'}):
            with self.assertRaisesRegex(RuntimeError,'different upstream URL'):
                mod.ensure_known_proxy(self.path,NEW,'aurora-banking',self.desired)
        self.assertEqual(state['writes'],[])

    def test_rejects_foreign_proxy_even_when_opted_in(self):
        state,call=self.fake_server()
        state['proxy']['name']='Someone else MCP'
        with patch.object(mod,'apicall',side_effect=call), patch.dict(os.environ,{'AURORA_ALLOW_EPHEMERAL_PROXY_UPDATE':'true'}):
            with self.assertRaisesRegex(RuntimeError,'do not match'):
                mod.ensure_known_proxy(self.path,NEW,'aurora-banking',self.desired)
        self.assertEqual(state['writes'],[])

    def test_rejects_non_quick_tunnel(self):
        state,call=self.fake_server()
        state['proxy']['endpoints'][0]['upstream']['main']['url']='https://production.example.net/mcp'
        with patch.object(mod,'apicall',side_effect=call), patch.dict(os.environ,{'AURORA_ALLOW_EPHEMERAL_PROXY_UPDATE':'true'}):
            with self.assertRaisesRegex(RuntimeError,'non-Quick-Tunnel'):
                mod.ensure_known_proxy(self.path,NEW,'aurora-banking',self.desired)
        self.assertEqual(state['writes'],[])

    def test_rejects_different_scopes(self):
        state,call=self.fake_server()
        self.scopes=self.scopes[:-1]
        with patch.object(mod,'apicall',side_effect=call), patch.dict(os.environ,{'AURORA_ALLOW_EPHEMERAL_PROXY_UPDATE':'true'}):
            with self.assertRaisesRegex(RuntimeError,'OAuth tool scopes differ'):
                mod.ensure_known_proxy(self.path,NEW,'aurora-banking',self.desired)
        self.assertEqual(state['writes'],[])

    def test_matching_endpoint_is_not_recreated(self):
        state,call=self.fake_server()
        state['proxy']['endpoints'][0]['upstream']['main']['url']=NEW
        with patch.object(mod,'apicall',side_effect=call):
            self.assertTrue(mod.ensure_known_proxy(self.path,NEW,'aurora-banking',self.desired))
        self.assertEqual(state['writes'],[])

class OrchestratorRegression(unittest.TestCase):
    def test_stage3_uses_idempotent_tunnel_up(self):
        source=(P.parent.parent/'scripts'/'start-everything.sh').read_text()
        self.assertNotIn('status | grep -q',source)
        self.assertIn('AURORA_ALLOW_EPHEMERAL_PROXY_UPDATE=true',source)
        self.assertIn('bash scripts/publish-mcp-secure-tunnel.sh up',source)
    def test_tunnel_ensures_owned_processes_reconciled(self):
        source=(P.parent.parent/'scripts'/'publish-mcp-secure-tunnel.sh').read_text()
        self.assertIn('ensure_tunnel(){',source)
        self.assertIn('if verify_local_origin && verify_public_origin "$current_url"; then',source)
        self.assertIn('stop_all\n  start_tunnel_fresh',source)
        self.assertIn('port_available 18101',source)
        self.assertIn('up|start) ensure_tunnel; publish;;',source)
    def test_concierge_refreshes_credentials_after_specialists(self):
        source=(P.parent.parent/'scripts'/'aurora-full-reconcile.py').read_text()
        i=source.index('    creds=service_secrets()\n    created=ensure_agent(\'aurora-concierge\'')
        self.assertGreater(i,source.index('    for role in ROLES:\n        name=\'aurora-\'+role'))

if __name__=='__main__':unittest.main()
