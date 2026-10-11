"""Environment resolution and no-guessing tests using live AMP v1.0.0 details."""
import copy
import importlib.util
import json
from pathlib import Path
import unittest
from unittest import mock

ROOT=Path(__file__).resolve().parents[1]
CAPTURE=json.loads((ROOT/'tests/fixtures/amp_100_llm_live.json').read_text())
spec=importlib.util.spec_from_file_location('aurora_llm_v12',ROOT/'scripts'/'aurora-full-reconcile.py')
m=importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

def pair(agent='aurora-account'):
    c=copy.deepcopy(CAPTURE[agent]);return c['list'],c['detail']

def registry():
    return mock.patch.object(m,'llm_provider_inventory',return_value=copy.deepcopy(CAPTURE['provider_list']['data']['providers']))

def writes(api):
    return [c for c in api.call_args_list if len(c.args)>1 and c.args[1]!='GET']

class EnvironmentV12Tests(unittest.TestCase):
    def test_default_map_key_supported(self):
        s,d=pair()
        with registry(),mock.patch.object(m,'api',side_effect=[s,d]) as api:
            m.bind_llm('aurora-account'); self.assertEqual(writes(api),[])

    def test_uuid_key_with_inner_environment_name(self):
        s,d=pair()
        d['envMappings']={'different-key':{**d['envMappings']['default'],'environmentName':'Default'}}
        with registry(),mock.patch.object(m,'api',side_effect=[s,d]) as api:
            m.bind_llm('aurora-account');self.assertEqual(writes(api),[])

    def test_uuid_key_with_matching_support_env_uuid(self):
        s,d=pair();d['envMappings']={'3ca08496-7c42-4ef5-969f-eeb658bbef91':{
            'configuration':d['envMappings']['default']['configuration']}}
        with registry(),mock.patch.object(m,'read_kv',return_value={'AMP_ENVIRONMENT_UUID':'3ca08496-7c42-4ef5-969f-eeb658bbef91'}),\
             mock.patch.object(m,'api',side_effect=[s,d]) as api:
            m.bind_llm('aurora-account');self.assertEqual(writes(api),[])

    def test_unbound_openai_template_refuses_unsupported_put(self):
        s,d=pair('aurora-card');d['envMappings']={};d['name']='openai'
        with registry(),mock.patch.object(m,'api',side_effect=[s,d]) as api:
            with self.assertRaisesRegex(RuntimeError,'no environment mapping'):
                m.bind_llm('aurora-card')
            self.assertEqual(writes(api),[])

    def test_unbound_arbitrary_custom_config_refused_without_write(self):
        s,d=pair('aurora-card');d['envMappings']={};d['name']='aurora-openai'
        with registry(),mock.patch.object(m,'api',side_effect=[s,d]) as api:
            with self.assertRaisesRegex(RuntimeError,'no environment mapping'):
                m.bind_llm('aurora-card')
            self.assertEqual(writes(api),[])

    def test_wrong_provider_is_never_overwritten(self):
        s,d=pair('aurora-card');d['envMappings']['default']['configuration']['providerName']='another-openai'
        with registry(),mock.patch.object(m,'api',side_effect=[s,d]) as api:
            with self.assertRaisesRegex(RuntimeError,'references provider'):
                m.bind_llm('aurora-card')
            self.assertEqual(writes(api),[])

    def test_duplicate_environment_entries_fail_closed(self):
        s,d=pair()
        d['envMappings']['uuid-other']=copy.deepcopy(d['envMappings']['default'])
        with registry(),mock.patch.object(m,'api',side_effect=[s,d]) as api:
            with self.assertRaisesRegex(RuntimeError,'multiple envMappings'):
                m.bind_llm('aurora-account')
            self.assertEqual(writes(api),[])

    def test_unknown_env_mapping_not_inferred_by_single_entry(self):
        s,d=pair()
        d['envMappings']={'unknown-env':{**d['envMappings']['default'],'environmentName':'staging'}}
        with registry(),mock.patch.object(m,'api',side_effect=[s,d]) as api:
            with self.assertRaisesRegex(RuntimeError,"no 'default' environment mapping"):
                m.bind_llm('aurora-account')
            self.assertEqual(writes(api),[])

    def test_unmapped_mapping_never_put_automatically(self):
        s,d=pair('aurora-card');d['envMappings']={}
        with registry(),mock.patch.object(m,'api',side_effect=[s,d]) as api:
            with self.assertRaisesRegex(RuntimeError,'no environment mapping'):
                m.bind_llm('aurora-card')
            self.assertEqual(api.call_count,2);self.assertEqual(writes(api),[])

    def test_unmapped_openai_with_custom_variable_refuses_mutation(self):
        s,d=pair('aurora-card');d['envMappings']={}
        d['environmentVariables']=[{'key':'url','name':'OPENAI_URL'},{'key':'other','name':'CUSTOM'}]
        with registry(),mock.patch.object(m,'api',side_effect=[s,d]) as api:
            with self.assertRaisesRegex(RuntimeError,'no environment mapping'):
                m.bind_llm('aurora-card')
            self.assertEqual(writes(api),[])

    def test_unmapped_detail_cannot_claim_persistence(self):
        s,d=pair('aurora-card');d['envMappings']={}
        with registry(),mock.patch.object(m,'api',side_effect=[s,d]) as api:
            with self.assertRaisesRegex(RuntimeError,'no environment mapping'):
                m.bind_llm('aurora-card')
            self.assertEqual(api.call_count,2)

    def test_readonly_metadata_never_logs_credentials(self):
        s,d=pair()
        with registry(),mock.patch.object(m,'api',side_effect=[s,d]),mock.patch.object(m,'log') as log:
            m.bind_llm('aurora-account')
            for call in log.call_args_list:
                self.assertNotIn('LLM_PROVIDER_KEY',str(call))
                self.assertNotIn('secret',str(call).lower())

if __name__=='__main__':unittest.main()
