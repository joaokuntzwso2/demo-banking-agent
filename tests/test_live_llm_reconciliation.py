"""Regression based on real `amctl api GET` captures for AMP v1.0.0."""
import copy
import importlib.util
import json
import pathlib
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
FIXTURE = json.loads((ROOT/'tests/fixtures/amp_100_llm_live.json').read_text())
spec = importlib.util.spec_from_file_location('aurora_full_live', ROOT/'scripts/aurora-full-reconcile.py')
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


def captured(name):
    return copy.deepcopy(FIXTURE[name])


class LiveContractTests(unittest.TestCase):
    def provider(self):
        return mock.patch.object(m, 'llm_provider_inventory',
                                 return_value=copy.deepcopy(FIXTURE['provider_list']['data']['providers']))

    def test_account_and_card_are_healthy_and_read_only(self):
        for name in ('aurora-account', 'aurora-card'):
            x=captured(name)
            with self.subTest(name=name), self.provider(), mock.patch.object(m, 'api', side_effect=[x['list'], x['detail']]) as api:
                m.bind_llm(name)
                self.assertEqual(api.call_count, 2)
                self.assertTrue(all(len(c.args) < 2 or c.args[1] == 'GET' for c in api.call_args_list))

    def test_actual_provider_uuid_and_proxy_uuid_are_distinct(self):
        for name in ('aurora-account', 'aurora-card'):
            obj=captured(name)['detail']
            cfg=m.llm_env_mapping(obj,name)
            self.assertEqual(cfg['providerUuid'], FIXTURE['provider_list']['data']['providers'][0]['uuid'])
            self.assertNotEqual(cfg['proxyUuid'], cfg['providerUuid'])

    def test_legacy_dto_detail_still_supported(self):
        obj=captured('aurora-card')['detail']
        old=obj.pop('envMappings')['default']['configuration']
        obj['envModelConfig']={'default':{'environmentName':'default','llmProxy':{
            'providerName':old['providerName'],'providerUuid':old['providerUuid'],
            'proxyUuid':old['proxyUuid']}}}
        with self.provider(), mock.patch.object(m,'api',side_effect=[captured('aurora-card')['list'],obj]) as api:
            m.bind_llm('aurora-card')
            self.assertEqual(api.call_count, 2)

    def test_missing_env_mapping_does_not_put(self):
        x=captured('aurora-account')
        x['detail']['envMappings']={}
        with self.provider(), mock.patch.object(m,'api',side_effect=[x['list'],x['detail']]) as api:
            with self.assertRaisesRegex(RuntimeError,'has no environment mapping'):
                m.bind_llm('aurora-account')
            self.assertEqual(api.call_count,2)

    def test_wrong_provider_handle_stops_before_mutation(self):
        x=captured('aurora-card')
        x['detail']['envMappings']['default']['configuration']['providerName']='other-provider'
        with self.provider(), mock.patch.object(m,'api',side_effect=[x['list'],x['detail']]) as api:
            with self.assertRaisesRegex(RuntimeError,'references provider'):
                m.bind_llm('aurora-card')
            self.assertEqual(api.call_count,2)

    def test_wrong_provider_uuid_stops_before_mutation(self):
        x=captured('aurora-card')
        x['detail']['envMappings']['default']['configuration']['providerUuid']='wrong-provider-uuid'
        with self.provider(), mock.patch.object(m,'api',side_effect=[x['list'],x['detail']]) as api:
            with self.assertRaisesRegex(RuntimeError,'provider UUID mismatch'):
                m.bind_llm('aurora-card')
            self.assertEqual(api.call_count,2)

    def test_stale_proxy_mapping_fails_before_mutation(self):
        for key,value in [('status','inactive'),('proxyUuid','')]:
            x=captured('aurora-account')
            x['detail']['envMappings']['default']['configuration'][key]=value
            with self.subTest(key=key), self.provider(), mock.patch.object(m,'api',side_effect=[x['list'],x['detail']]) as api:
                with self.assertRaises(RuntimeError):
                    m.bind_llm('aurora-account')
                self.assertEqual(api.call_count,2)

    def test_env_name_inconsistent_fails_closed(self):
        x=captured('aurora-account')
        x['detail']['envMappings']['default']['environmentName']='staging'
        with self.provider(), mock.patch.object(m,'api',side_effect=[x['list'],x['detail']]) as api:
            with self.assertRaisesRegex(RuntimeError,'conflicting envMappings'):
                m.bind_llm('aurora-account')
            self.assertEqual(api.call_count,2)

    def test_duplicate_mappings_fails_closed(self):
        x=captured('aurora-account')
        x['detail']['envMappings']['another'] = x['detail']['envMappings']['default']
        with self.provider(), mock.patch.object(m,'api',side_effect=[x['list'],x['detail']]) as api:
            with self.assertRaisesRegex(RuntimeError,'multiple envMappings'):
                m.bind_llm('aurora-account')
            self.assertEqual(api.call_count,2)

    def test_missing_env_var_key_fails_before_put(self):
        x=captured('aurora-card')
        x['detail']['environmentVariables'].pop()
        with self.provider(), mock.patch.object(m,'api',side_effect=[x['list'],x['detail']]) as api:
            with self.assertRaisesRegex(RuntimeError,'custom/absent injection'):
                m.bind_llm('aurora-card')
            self.assertEqual(api.call_count,2)

    def test_duplicate_env_var_key_fails_before_put(self):
        x=captured('aurora-account')
        x['detail']['environmentVariables'].append({'key':'url','name':'DIFFERENT_URL'})
        with self.provider(), mock.patch.object(m,'api',side_effect=[x['list'],x['detail']]) as api:
            with self.assertRaisesRegex(RuntimeError,'duplicate/blank'):
                m.bind_llm('aurora-account')
            self.assertEqual(api.call_count,2)

    def test_safe_variable_rename_only_puts_env_variables_and_reads_back(self):
        x=captured('aurora-card')
        x['detail']['environmentVariables'][0]['name']='OLD_URL'
        after=captured('aurora-card')['detail']
        with self.provider(), mock.patch.object(m,'api',side_effect=[x['list'],x['detail'],{},after]) as api:
            m.bind_llm('aurora-card')
            writes=[c for c in api.call_args_list if len(c.args)>1 and c.args[1]!='GET']
            self.assertEqual(len(writes),1)
            self.assertEqual(writes[0].args[1],'PUT')
            self.assertEqual(set(writes[0].args[2]),{'environmentVariables'})
            self.assertEqual(api.call_count,4)

    def test_variable_rename_readback_wrong_provider_fails(self):
        x=captured('aurora-account')
        x['detail']['environmentVariables'][0]['name']='OLD_URL'
        changed=captured('aurora-account')['detail']
        changed['envMappings']['default']['configuration']['providerUuid']='other-provider'
        with self.provider(), mock.patch.object(m,'api',side_effect=[x['list'],x['detail'],{},changed]):
            with self.assertRaisesRegex(RuntimeError,'provider UUID mismatch'):
                m.bind_llm('aurora-account')

    def test_incomplete_collection_refuses_duplication(self):
        x=captured('aurora-account')
        x['list']['pagination']['count']=2
        with self.provider(), mock.patch.object(m,'api',return_value=x['list']) as api:
            with self.assertRaisesRegex(RuntimeError,'incomplete model-config page'):
                m.bind_llm('aurora-account')
            self.assertEqual(api.call_count,1)

    def test_multiple_llm_configs_refuse_auto_selection(self):
        x=captured('aurora-account')
        x['list']['configs'].append({**x['list']['configs'][0], 'uuid':'second-config'})
        x['list']['pagination']['count']=2
        with self.provider(), mock.patch.object(m,'api',return_value=x['list']) as api:
            with self.assertRaisesRegex(RuntimeError,'exactly one LLM configuration'):
                m.bind_llm('aurora-account')
            self.assertEqual(api.call_count,1)

    def test_new_binding_only_post_when_none_and_verify_readback(self):
        x=captured('aurora-account')
        missing={'configs':[], 'pagination':{'count':0,'offset':0,'limit':20}}
        with self.provider(), mock.patch.object(m,'api',side_effect=[missing,{},x['list'],x['detail']]) as api:
            m.bind_llm('aurora-account')
            posts=[c for c in api.call_args_list if len(c.args)>1 and c.args[1]=='POST']
            self.assertEqual(len(posts),1)
            self.assertEqual(posts[0].args[2]['type'],'llm')

    def test_details_with_wrong_agent_identity_fail_closed(self):
        x=captured('aurora-card')
        x['detail']['agentId']='aurora-account'
        with self.provider(), mock.patch.object(m,'api',side_effect=[x['list'],x['detail']]) as api:
            with self.assertRaisesRegex(RuntimeError,'belongs to'):
                m.bind_llm('aurora-card')
            self.assertEqual(api.call_count,2)

if __name__=='__main__':
    unittest.main()
