"""Legacy regression intentions migrated to captured AMP 1.0.0 LLM responses."""
import copy
import importlib.util
import json
from pathlib import Path
import unittest
from unittest import mock

ROOT=Path(__file__).resolve().parents[1]
CAPTURE=json.loads((ROOT/'tests/fixtures/amp_100_llm_live.json').read_text())
spec=importlib.util.spec_from_file_location('aurora_llm_v11',ROOT/'scripts'/'aurora-full-reconcile.py')
m=importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

def pair(agent='aurora-card'):
    d=copy.deepcopy(CAPTURE[agent]);return d['list'], d['detail']

def registry():
    return mock.patch.object(m,'llm_provider_inventory',return_value=copy.deepcopy(CAPTURE['provider_list']['data']['providers']))

def writes(api):
    return [c for c in api.call_args_list if len(c.args)>1 and c.args[1]!='GET']

class ModelBindingV11Tests(unittest.TestCase):
    def test_reuses_template_named_openai_configuration(self):
        s,d=pair()
        s['configs'][0]['name']='openai'; d['name']='openai'
        with registry(),mock.patch.object(m,'api',side_effect=[s,d]) as api:
            m.bind_llm('aurora-card')
            self.assertEqual(api.call_count,2); self.assertEqual(writes(api),[])

    def test_reuses_arbitrary_config_name_when_provider_handle_matches(self):
        s,d=pair();s['configs'][0]['name']='openai-2';d['name']='openai-2'
        with registry(),mock.patch.object(m,'api',side_effect=[s,d]) as api:
            m.bind_llm('aurora-card')
            self.assertEqual(writes(api),[])

    def test_preserves_manual_provider_and_fails_closed(self):
        s,d=pair();d['envMappings']['default']['configuration']['providerName']='another-provider'
        with registry(),mock.patch.object(m,'api',side_effect=[s,d]) as api:
            with self.assertRaisesRegex(RuntimeError,'references provider'):
                m.bind_llm('aurora-card')
            self.assertEqual(writes(api),[])

    def test_absent_provider_detail_fails_not_assume_from_name(self):
        s,d=pair();d['envMappings']={}
        with registry(),mock.patch.object(m,'api',side_effect=[s,d]) as api:
            with self.assertRaisesRegex(RuntimeError,'no environment mapping'):
                m.bind_llm('aurora-card')
            self.assertEqual(writes(api),[])

    def test_environment_names_corrected_with_minimal_put_verified(self):
        s,d=pair()
        d['environmentVariables']=[{'key':'url','name':'OPENAI_URL'},{'key':'apikey','name':'OPENAI_API_KEY'}]
        after=pair()[1]
        with registry(),mock.patch.object(m,'api',side_effect=[s,d,{},after]) as api:
            m.bind_llm('aurora-card')
            changes=writes(api)
            self.assertEqual(len(changes),1)
            self.assertEqual(changes[0].args[1],'PUT')
            self.assertEqual(changes[0].args[2],{'environmentVariables':m.llm_binding_payload()['environmentVariables']})

    def test_missing_environment_var_persistence_fails(self):
        s,d=pair()
        d['environmentVariables'][0]['name']='OLD_URL'
        with registry(),mock.patch.object(m,'api',side_effect=[s,d,{},copy.deepcopy(d)]) as api:
            with self.assertRaisesRegex(RuntimeError,'did not persist'):
                m.bind_llm('aurora-card')
            self.assertEqual(len(writes(api)),1)

    def test_unknown_extra_var_refuses_overwrite(self):
        s,d=pair();d['environmentVariables'].append({'key':'other','name':'CUSTOM'})
        with registry(),mock.patch.object(m,'api',side_effect=[s,d]) as api:
            with self.assertRaisesRegex(RuntimeError,'custom/absent injection'):
                m.bind_llm('aurora-card')
            self.assertEqual(writes(api),[])

    def test_empty_binding_created_once_and_verified(self):
        s,d=pair()
        empty={'configs':[],'pagination':{'count':0,'offset':0,'limit':20}}
        with registry(),mock.patch.object(m,'api',side_effect=[empty,{},s,d]) as api:
            m.bind_llm('aurora-card')
            self.assertEqual(len(writes(api)),1)
            self.assertEqual(writes(api)[0].args[1],'POST')

    def test_multiple_model_configs_are_not_overwritten(self):
        s,d=pair();s['configs'].append({**s['configs'][0],'uuid':'another'});s['pagination']['count']=2
        with registry(),mock.patch.object(m,'api',return_value=s) as api:
            with self.assertRaisesRegex(RuntimeError,'exactly one LLM configuration'):
                m.bind_llm('aurora-card')
            self.assertEqual(writes(api),[])

    def test_missing_detail_does_not_create_duplicate(self):
        s,_=pair()
        with registry(),mock.patch.object(m,'api',side_effect=[s,None]) as api:
            with self.assertRaisesRegex(RuntimeError,'detail unavailable'):
                m.bind_llm('aurora-card')
            self.assertEqual(writes(api),[])

if __name__=='__main__':unittest.main()
