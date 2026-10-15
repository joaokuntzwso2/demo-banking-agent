import importlib.util
import pathlib
import tempfile
import unittest
from unittest import mock

FILE=pathlib.Path(__file__).resolve().parent.parent/'scripts'/'aurora-full-reconcile.py'
spec=importlib.util.spec_from_file_location('aurora_reconciler',str(FILE))
m=importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

class Tests(unittest.TestCase):
    def test_exact_six_agents(self):
        self.assertEqual(m.AGENTS,('account','card','dispute','pix','wellness','concierge'))
    def test_fail_closed_proxy_non_https(self):
        with mock.patch.object(m,'api',side_effect=[{'id':'aurora-banking','endpoints':[{'upstream':{'main':{'url':'http://inside/mcp'}},'environments':[{'deploymentStatus':'Deployed'}]}]}]):
            with self.assertRaisesRegex(RuntimeError,'HTTPS'):
                m.verify_proxy()
    def test_reject_incomplete_scopes(self):
        p={'id':'aurora-banking','endpoints':[{'upstream':{'main':{'url':'https://demo.example/mcp','auth':{'header':'X-Aurora-Origin-Key'}}},'environments':[{'deploymentStatus':'Deployed'}], 'capabilities':{'tools':[{'name':f't{i}'} for i in range(15)]}}]}
        env_uuid = '01234567-89ab-4cde-8f01-23456789abcd'
        p['endpoints'][0]['environments'][0]['environmentUuid'] = env_uuid
        # The production repo may contain an active, real Quick Tunnel URL.
        # Isolate SUPPORT so a unit-test fixture is never compared with it.
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(m, 'SUPPORT', pathlib.Path(tmp)):
                with mock.patch.object(m,'api',side_effect=[p,{'scopes':[]}]), mock.patch.object(m,'current_amp_environment_uuid',return_value=env_uuid):
                    with self.assertRaisesRegex(RuntimeError,'15 MCP tools and scopes'):
                        m.verify_proxy()

    def test_reject_proxy_pointing_to_different_live_tunnel(self):
        p={'id':'aurora-banking','endpoints':[{'upstream':{'main':{'url':'https://demo.example/mcp','auth':{'header':'X-Aurora-Origin-Key'}}},'environments':[{'deploymentStatus':'Deployed'}], 'capabilities':{'tools':[{'name':f't{i}'} for i in range(15)]}}]}
        with tempfile.TemporaryDirectory() as tmp:
            state=pathlib.Path(tmp)/'.mcp-public-tunnel'
            state.mkdir()
            (state/'url').write_text('https://another-tunnel.example')
            with mock.patch.object(m,'SUPPORT',pathlib.Path(tmp)):
                with mock.patch.object(m,'api',return_value=p) as amctl_api:
                    with self.assertRaisesRegex(RuntimeError,'does not match the local live HTTPS tunnel'):
                        m.verify_proxy()
                    self.assertEqual(amctl_api.call_count,1)
    def test_does_not_reveal_creation_secrets(self):
        with mock.patch.object(m,'agent_json',return_value=None), mock.patch.object(m,'verify_agent_openapi'), mock.patch.object(m,'check_create_cli_flags'), mock.patch.object(m,'cmd',return_value=None) as c:
            m.ensure_agent('aurora-account','account','deadbeef'*5,{'SPECIALIST_CALL_KEY':'s' * 32})
            self.assertTrue(c.call_args.kwargs['hide'])
            self.assertIn('--openapi-spec',c.call_args.args[0])
            self.assertEqual(c.call_args.args[0][c.call_args.args[0].index('--openapi-spec')+1],'openapi.json')
            self.assertIn('--llm-provider',c.call_args.args[0])
            self.assertEqual(c.call_args.args[0][c.call_args.args[0].index('--llm-provider')+1],m.LLM_PROVIDER)
            self.assertIn('--base-path',c.call_args.args[0])
            self.assertEqual(c.call_args.args[0][c.call_args.args[0].index('--base-path')+1],'/')
    def test_tool_binding_ambiguous_refuses_writes(self):
        with mock.patch.object(m,'api',side_effect=[{'modelConfigs':[]}, {'modelConfigs':[]}, None, None]) as a:
            with self.assertRaisesRegex(RuntimeError,'safely identify'):
                m.bind_tool('aurora-account')
            self.assertEqual(a.call_count,4)
    def test_config_permissions(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=pathlib.Path(tmp)/'support.env'
            m.update_kv(p,{'KUBE_CONTEXT':'k3d-amp-local'})
            m.update_kv(p,{'KUBE_CONTEXT':'k3d-amp-local','AMP_PUBLISH':'true'})
            data=p.read_text()
            self.assertEqual(data.count('KUBE_CONTEXT='),1)
            self.assertIn('AMP_PUBLISH=true',data)
            self.assertEqual(p.stat().st_mode & 0o777,0o600)
    def test_agent_credentials_fail_closed(self):
        with self.assertRaisesRegex(RuntimeError,'secrets are missing'):
            m.agent_config('pix',{})
    def test_model_binding_payload_matches_wso2_go_contract(self):
        payload=m.mcp_binding_payload()
        self.assertEqual(payload['type'],'mcp')
        self.assertEqual(payload['envMappings'][m.ENV]['providerName'],'aurora-banking')
        self.assertIn({'key':'url','name':'MCP_SERVER_URL'},payload['environmentVariables'])
    def test_cli_success_envelope_is_not_successful_build(self):
        self.assertNotIn(m.status_label({'status':'ok','data':{'status':'Running'}}),('success','succeeded','completed'))
        self.assertEqual(m.status_label({'status':'ok','buildStatus':'Completed'}),'completed')
    def test_missing_api_key_route_fails_before_post(self):
        with mock.patch.object(m,'service_secrets',return_value={}), mock.patch.object(m,'api',return_value=None) as a:
            with self.assertRaisesRegex(RuntimeError,'No unambiguous'):
                m.provision_gateway_key('aurora-account')
            self.assertTrue(all(c.kwargs.get('allow_404',False) for c in a.call_args_list))
    def test_concierge_requires_specialist_keys(self):
        with self.assertRaisesRegex(RuntimeError,'secrets are missing'):
            m.agent_config('concierge',{'BANK_CORE_READ_KEY':'a','BANK_APPROVAL_KEY':'b',
               'CONCIERGE_CALL_KEY':'c','CONCIERGE_APPROVAL_KEY':'d',
               'SPECIALIST_CALL_KEY':'e','SPECIALIST_EXECUTION_KEY':'f'})
    def test_redacts_flag_value_but_shows_validation_reason(self):
        secret='THIS_MUST_NEVER_APPEAR_10a90ffba02cd'
        args=['amctl','agent','create','aurora-account','--env-secret',f'SPECIALIST_CALL_KEY={secret}']
        output=f'Error: invalid flag --openapi-spec; command --env-secret SPECIALIST_CALL_KEY={secret}'
        result=m.redacted_cli_error(output,args)
        self.assertNotIn(secret,result)
        self.assertIn('invalid flag',result)
        self.assertIn('REDACTED',result)

    def test_cli_exit_two_shows_safe_error(self):
        secret='SENSITIVE_B6E312_FROMSOMEWHERE'
        args=['amctl','agent','create','aurora-account','--env-secret','TOKEN='+secret]
        completed=mock.Mock(returncode=2,stderr='Error: missing required flag --openapi-spec '+secret, stdout='')
        with mock.patch.object(m.subprocess,'run',return_value=completed):
            with self.assertRaisesRegex(RuntimeError,'--openapi-spec') as cm:
                m.cmd(args,hide=True)
        self.assertNotIn(secret,str(cm.exception))
        self.assertIn('CLI exit 2',str(cm.exception))

    def test_verifies_actual_openapi_document_and_main_before_create(self):
        import json
        with tempfile.TemporaryDirectory() as tmp:
            root=pathlib.Path(tmp)
            app=root/'deployment'/'agent-manager'/'apps'/'aurora-account'
            app.mkdir(parents=True)
            (app/'main.py').write_text('print(123)')
            (app/'openapi.json').write_text(json.dumps({'openapi':'3.0.3','info':{'title':'Demo','version':'1'},'paths':{'/health':{'get':{}}}}))
            with mock.patch.object(m,'ROOT',root):
                self.assertEqual(m.verify_agent_openapi('aurora-account'),app/'openapi.json')
                (app/'openapi.json').write_text('{')
                with self.assertRaisesRegex(RuntimeError,'not valid JSON'):
                    m.verify_agent_openapi('aurora-account')

    def test_cli_help_validation_prevents_create_with_secret(self):
        help_text='--llm-provider --llm-url-env --llm-api-key-env --openapi-spec --base-path --repo-path --env-secret --subtype --build-type --run-command'
        with mock.patch.object(m,'cmd',return_value=mock.Mock(stdout=help_text,stderr='')):
            m.check_create_cli_flags()
        with mock.patch.object(m,'cmd',return_value=mock.Mock(stdout='--repo-path --env-secret',stderr='')):
            with self.assertRaisesRegex(RuntimeError,'--llm-provider'):
                m.check_create_cli_flags()

    def test_base_path_matches_generated_openapi_root_routes(self):
        # Agent Manager's spec.inputInterface.basePath is mandatory for
        # custom-api agents, and these FastAPI apps mount at /, not /api.
        import json
        spec_path = FILE.parent.parent / 'deployment/agent-manager/apps/aurora-account/openapi.json'
        if not spec_path.is_file():
            self.skipTest('Generated OpenAPI files absent from fixture checkout')
        doc = json.loads(spec_path.read_text())
        self.assertEqual(m.AGENT_BASE_PATH, '/')
        self.assertIn('/chat', doc['paths'])
        self.assertIn('/health', doc['paths'])

    def test_main_bash_syntax(self):
        import subprocess
        p=FILE.parent/'start-everything.sh'
        q=subprocess.run(['bash','-n',str(p)],capture_output=True)
        self.assertEqual(q.returncode,0)

if __name__=='__main__':unittest.main()
