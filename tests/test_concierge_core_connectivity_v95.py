"""Regression checks for observed Concierge -> Banking Core ConnectError."""
import importlib.util
import base64
from pathlib import Path
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
SCRIPT=ROOT/'scripts'/'repair-aurora-concierge-core.py'
RECONCILER=ROOT/'scripts'/'aurora-full-reconcile.py'
spec=importlib.util.spec_from_file_location('aurora_core_repair_v95',SCRIPT)
m=importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


def good_core():
    return ({'spec': {'selector': {'app':'aurora-core'},
                      'ports':[{'port':8100,'targetPort':8100}]}},
            {'items':[{'endpoints':[{'addresses':['10.42.0.95'],
                                      'conditions':{'ready':True}}]}]})


def pods(env):
    return {'items':[{'metadata':{'name':'aurora-concierge-abc'},
                      'status':{'phase':'Running','containerStatuses':[{'name':'main','ready':True}]},
                      'spec':{'containers':[{'name':'main','env':env}]}}]}


class CoreRepairTests(unittest.TestCase):
    def test_core_service_and_endpoint_ready(self):
        svc,eps=good_core()
        self.assertEqual(m.core_service_ready(svc,eps),1)

    def test_core_service_without_ready_endpoint_never_passes(self):
        svc,eps=good_core()
        eps['items'][0]['endpoints'][0]['conditions']['ready']=False
        with self.assertRaisesRegex(RuntimeError,'no ready'):
            m.core_service_ready(svc,eps)

    def test_missing_core_url_detected(self):
        self.assertEqual(m.core_env_state({'env':[{'name':'PORT','value':'8000'}]}),'missing')

    def test_correct_core_url_no_redeployment_required(self):
        self.assertEqual(m.core_env_state({'env':[{'name':'BANK_CORE_URL','value':m.CORE_URL}]}),'correct')

    def test_wrong_core_url_detected_without_printing_it(self):
        self.assertEqual(m.core_env_state({'env':[{'name':'BANK_CORE_URL','value':'http://localhost:8100'}]}),'mismatched-literal')

    def test_secret_reference_does_not_guess_or_overwrite(self):
        with patch.object(m,'query',return_value={'data':{'y':base64.b64encode(m.CORE_URL.encode()).decode()}}):
            self.assertEqual(m.core_env_state({'env':[{'name':'BANK_CORE_URL','valueFrom':{'secretKeyRef':{'name':'x','key':'y'}}}]}),'correct')

    def test_envfrom_does_not_guess(self):
        with patch.object(m,'query',return_value={'data':{'BANK_CORE_URL':base64.b64encode(m.CORE_URL.encode()).decode()}}):
            self.assertEqual(m.core_env_state({'envFrom':[{'secretRef':{'name':'s'}}]}),'correct')

    def test_bad_secret_value_can_never_be_printed(self):
        with patch.object(m,'query',return_value={'data':{'y':base64.b64encode(b'http://localhost:8100').decode()}}):
            self.assertEqual(m.core_env_state({'env':[{'name':'BANK_CORE_URL','valueFrom':{'secretKeyRef':{'name':'x','key':'y'}}}]}),'mismatched-reference')

    def test_envfrom_duplicate_refuse_auto_selection(self):
        with patch.object(m,'query',return_value={'data':{'BANK_CORE_URL':base64.b64encode(m.CORE_URL.encode()).decode()}}):
            self.assertEqual(m.core_env_state({'envFrom':[{'secretRef':{'name':'s'}},{'secretRef':{'name':'t'}}]}),'ambiguous-envFrom')

    def test_reject_duplicate_core_url(self):
        with self.assertRaisesRegex(RuntimeError,'Duplicate'):
            m.core_env_state({'env':[{'name':'BANK_CORE_URL','value':'x'}, {'name':'BANK_CORE_URL','value':'y'}]})

    def test_reject_ambiguous_concierge_pods(self):
        pool=pods([])
        pool['items'].append(pool['items'][0])
        with self.assertRaisesRegex(RuntimeError,'one ready'):
            m.select_concierge(pool)

    def test_dry_run_does_not_modify_anything(self):
        svc,eps=good_core()
        def get(ns,kind,name=None,selector=None):
            if kind=='svc':return svc
            if kind=='endpointslices':return eps
            return pods([{'name':'BANK_CORE_URL','value':m.CORE_URL}])
        with patch.object(m,'query',side_effect=get) as query, \
             patch.object(m,'apply') as apply, patch.object(m.sys,'argv',['core-repair']):
            self.assertEqual(m.main(),0)
            self.assertEqual(query.call_count,3)
            apply.assert_not_called()

    def test_existing_correct_core_never_deploys(self):
        with patch.object(m,'audit',return_value='correct'),patch.object(m.importlib.util,'spec_from_file_location') as load:
            m.apply()
            load.assert_not_called()

    def test_persistent_reconciler_declares_core_during_deploy(self):
        source=RECONCILER.read_text()
        self.assertIn("CORE_SERVICE_URL = 'http://aurora-core.aurora-demo.svc.cluster.local:8100'",source)
        self.assertIn("urlvars={'BANK_CORE_URL':CORE_SERVICE_URL,",source)
        self.assertIn("'BANK_CORE_URL':CORE_SERVICE_URL",source)
        self.assertIn("'USE_LLM_PROVIDER':'true'",source)

    def test_script_python311_compatible(self):
        compile(SCRIPT.read_text(),str(SCRIPT),'exec',dont_inherit=True)
        compile(RECONCILER.read_text(),str(RECONCILER),'exec',dont_inherit=True)


if __name__=='__main__':
    unittest.main()
