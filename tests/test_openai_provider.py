"""WSO2 v1.0.0 OpenAI provider and agent binding security regressions."""
import importlib.util
import pathlib
import tempfile
import unittest
from unittest import mock

F=pathlib.Path(__file__).resolve().parent.parent/'scripts'/'aurora-full-reconcile.py'
spec=importlib.util.spec_from_file_location('aurora_openai_reconciler',F)
m=importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

class OpenAITests(unittest.TestCase):
    def test_llm_binding_type_and_runtime_env_vars(self):
        p=m.llm_binding_payload()
        self.assertEqual(p['type'],'llm')
        self.assertEqual(p['envMappings'][m.ENV]['providerName'],m.LLM_PROVIDER)
        self.assertEqual({v['name'] for v in p['environmentVariables']},
                         {'LLM_PROVIDER_URL','LLM_PROVIDER_KEY'})

    def test_existing_provider_never_prompts_for_key(self):
        with mock.patch.object(m,'llm_provider_inventory',return_value=[
             {'id':m.LLM_PROVIDER,'template':'openai'}]),\
             mock.patch.object(m.getpass,'getpass') as prompt,\
             mock.patch.object(m,'cmd') as cli:
            m.ensure_llm_provider()
            prompt.assert_not_called()
            cli.assert_not_called()

    def test_wrong_template_refuses_reuse(self):
        with mock.patch.object(m,'llm_provider_inventory',return_value=[
             {'id':m.LLM_PROVIDER,'template':'anthropic'}]),\
             mock.patch.object(m.getpass,'getpass') as prompt:
            with self.assertRaisesRegex(RuntimeError,'not verifiably an OpenAI'):
                m.ensure_llm_provider()
            prompt.assert_not_called()

    def test_provider_create_key_only_through_stdin_never_argv(self):
        key='sk-PROVIDED-BY-USER-FOR-TESTS-ONLY-SECRET'
        inv=[[], [{'id':m.LLM_PROVIDER,'template':'openai'}]]
        with mock.patch.object(m,'llm_provider_inventory',side_effect=inv),\
             mock.patch.object(m,'cmd',return_value=mock.Mock(stdout='--api-key-stdin --gateways --template')),\
             mock.patch.object(m.sys.stdin,'isatty',return_value=True),\
             mock.patch.object(m.getpass,'getpass',return_value=key),\
             mock.patch.object(m.subprocess,'run',return_value=mock.Mock(returncode=0,stdout='',stderr='')) as call:
            m.ensure_llm_provider()
            args=call.call_args.args[0]
            self.assertNotIn(key,args)
            self.assertNotIn(key,' '.join(args))
            self.assertIn('--api-key-stdin',args)
            self.assertIn('--gateways',args)
            self.assertEqual(call.call_args.kwargs['input'],key+'\n')

    def test_noninteractive_missing_provider_fails_before_create(self):
        with mock.patch.object(m,'llm_provider_inventory',return_value=[]),\
             mock.patch.object(m,'cmd',return_value=mock.Mock(stdout='--api-key-stdin --gateways --template')),\
             mock.patch.object(m.sys.stdin,'isatty',return_value=False),\
             mock.patch.object(m.subprocess,'run') as process:
            with self.assertRaisesRegex(RuntimeError,'interactive terminal'):
                m.ensure_llm_provider()
            process.assert_not_called()

    def test_existing_agent_is_bound_with_type_llm(self):
        import json
        raw=json.loads((F.parent.parent/'tests/fixtures/amp_100_llm_live.json').read_text())
        item=raw['aurora-account']
        with mock.patch.object(m, 'llm_provider_inventory', return_value=raw['provider_list']['data']['providers']), \
             mock.patch.object(m,'api',side_effect=[item['list'], item['detail']]) as call:
            m.bind_llm('aurora-account')
            self.assertEqual(call.call_count,2)
            self.assertTrue(all(len(x.args)<2 or x.args[1]=='GET' for x in call.call_args_list))

    def test_tool_binding_does_not_post_mcp_to_llm_endpoint(self):
        self.assertTrue(all('/model-configs' not in x for x in m.tool_config_paths('aurora-account')))
        with mock.patch.object(m,'api',return_value=None) as api:
            with self.assertRaisesRegex(RuntimeError,'safely identify'):
                m.bind_tool('aurora-account')
            self.assertFalse(any(c.args[1]=='POST' for c in api.call_args_list if len(c.args)>1))

    def test_all_six_use_managed_llm_without_upstream_openai_key(self):
        for role in m.AGENTS:
            creds={'SPECIALIST_CALL_KEY':'s','SPECIALIST_EXECUTION_KEY':'s',
                   'BANK_CORE_READ_KEY':'s','BANK_APPROVAL_KEY':'s',
                   'CONCIERGE_CALL_KEY':'s','CONCIERGE_APPROVAL_KEY':'s'}
            creds.update({f'AGENT_{r.upper()}_API_KEY':'s' for r in m.ROLES})
            cfg,sec=m.agent_config(role,creds)
            self.assertEqual(cfg['USE_LLM_PROVIDER'],'true')
            self.assertEqual(cfg['REQUIRE_GOVERNED_LLM'],'true')
            self.assertNotIn('OPENAI_API_KEY',sec)

if __name__=='__main__':unittest.main()
