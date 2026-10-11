import base64
import importlib.util
import io
from contextlib import redirect_stdout, redirect_stderr
from pathlib import Path
from unittest import TestCase, mock

P = Path(__file__).resolve().parents[1] / 'scripts' / 'diagnose-aurora-llm-path.py'
spec = importlib.util.spec_from_file_location('llm_path_v1', P)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


class ModelPathTests(TestCase):
    def test_grammar_compiles(self):
        compile(P.read_text(), str(P), 'exec')

    def test_provider_url_classified_no_full_url(self):
        h, p, scheme, family = m.endpoint_info('http://default-default.gateway.localhost:19080/aurora-openai')
        self.assertEqual((p, scheme), (19080, 'http'))
        self.assertEqual(h, 'default-default.gateway.localhost')
        self.assertIn('localhost-domain', family)

    def test_url_rejects_embedded_credentials_query(self):
        for uri in ('https://key:pass@model.example/v1', 'https://model.example/v1?token=topsecret',
                    'file:///foo', 'https://model.example:99999/v1', 'http://model.example/hi\nattack'):
            with self.subTest(uri=uri[:17]), self.assertRaises(m.AuditError):
                m.endpoint_info(uri)

    def test_model_key_is_not_decoded(self):
        env = {'env': [{'name': 'LLM_PROVIDER_KEY', 'valueFrom': {'secretKeyRef': {'name':'llm-secret','key':'apikey'}}}]}
        with mock.patch.object(m,'kget',return_value={'data': {'apikey': '!!!!!!not-decoded!!!!!'}}):
            self.assertTrue(m.read_env('ns',env,'LLM_PROVIDER_KEY',is_key=True))

    def test_model_key_rejects_plaintext(self):
        env = {'env':[{'name':'LLM_PROVIDER_KEY','value':'sensitive'}]}
        with self.assertRaisesRegex(m.AuditError,'Plaintext'):
            m.read_env('ns',env,'LLM_PROVIDER_KEY',is_key=True)

    def test_env_from_url_decode_and_key_presence(self):
        url = 'http://default-default.gateway.localhost:19080/aurora-openai'
        val = base64.b64encode(url.encode()).decode()
        env = {'envFrom':[{'secretRef':{'name':'managed'}}]}
        with mock.patch.object(m,'kget',return_value={'data':{'LLM_PROVIDER_URL':val,'LLM_PROVIDER_KEY':'encoded-but-unexamined'}}):
            self.assertEqual(m.read_env('ns',env,'LLM_PROVIDER_URL'),url)
            self.assertTrue(m.read_env('ns',env,'LLM_PROVIDER_KEY',is_key=True))

    def test_ready_concierge_exactly_one(self):
        pod={'metadata':{'name':'aurora-concierge-a','namespace':'dp-test'},
             'status':{'phase':'Running','containerStatuses':[{'name':'main','ready':True}]},
             'spec':{'containers':[{'name':'main','env':[]}]}}
        self.assertEqual(m.select_pod({'items':[pod]})[1], 'aurora-concierge-a')
        with self.assertRaises(m.AuditError):
            m.select_pod({'items':[pod,pod]})

    def test_selecting_egress_policy(self):
        pod={'metadata':{'labels':{'openchoreo.dev/component':'aurora-concierge'}}}
        policies={'items':[{'spec':{'podSelector':{'matchLabels':{'openchoreo.dev/component':'aurora-concierge'}},
                                        'policyTypes':['Egress'],'egress':[]}},
                           {'spec':{'podSelector':{'matchLabels':{'app':'other'}},'policyTypes':['Egress']}}]}
        self.assertEqual(m.selecting_egress_count(policies,pod),1)

    def test_probe_code_never_contains_gateway_key(self):
        code=m.probe_code('model.example',443,19080)
        self.assertIn('MODEL_TCP=ok',code)
        self.assertIn('GATEWAY_TCP=failed',code)
        self.assertNotIn('LLM_PROVIDER_KEY',code)
        self.assertNotIn('curl ',code)

    def test_parse_probe_allows_only_four_statuses(self):
        good='MODEL_DNS=ok\nMODEL_TCP=failed\nGATEWAY_DNS=ok\nGATEWAY_TCP=failed\n'
        self.assertEqual(m.parse_probe(good)['MODEL_TCP'],'failed')
        with self.assertRaises(m.AuditError):m.parse_probe(good+'MODEL_TCP=ok\n')
        with self.assertRaises(m.AuditError):m.parse_probe('MODEL_DNS=ok')

    def test_interpret_egress_distinct(self):
        values={'MODEL_DNS':'ok','MODEL_TCP':'failed','GATEWAY_DNS':'ok','GATEWAY_TCP':'failed'}
        self.assertIn('NetworkPolicy',m.interpret(values,1))
        values['GATEWAY_TCP']='ok'
        self.assertIn('hostname/port',m.interpret(values,1))
        values['MODEL_TCP']='ok'
        self.assertIn('API-key',m.interpret(values,1))

    def test_main_default_is_read_only_and_redacts_model_url(self):
        pod={'metadata':{'name':'aurora-concierge-a','namespace':'dp','labels':{'app':'concierge'}},
             'status':{'phase':'Running','containerStatuses':[{'name':'main','ready':True}]},
             'spec':{'containers':[{'name':'main'}]}}
        src={'LLM_PROVIDER_URL':'http://hidden.internal:19080/private/path','LLM_PROVIDER_KEY':True}
        def dispatch(argv):
            if argv == ['get','pods','-A']:
                return {'items':[pod]}
            if 'networkpolicies' in argv:
                return {'items':[]}
            if 'service' in argv:
                return {'spec':{'ports':[{'port':19080}]}}
            raise AssertionError(argv)
        out=io.StringIO()
        with mock.patch.object(m,'kget',side_effect=dispatch),mock.patch.object(m,'read_env',side_effect=lambda _n,_c,k,is_key=False:src[k]),mock.patch.object(m,'probe',side_effect=AssertionError('must not probe')),redirect_stdout(out):
            self.assertEqual(m.main([]),0)
        self.assertNotIn('hidden.internal',out.getvalue())
        self.assertNotIn('/private/path',out.getvalue())
        self.assertNotIn('apikey',out.getvalue().lower())

    def test_probe_never_exposes_secrets_or_model_http_request(self):
        script=P.read_text()
        self.assertNotIn('requests.post(',script)
        self.assertNotIn('httpx.',script)
        self.assertNotIn('kubectl apply',script)
        self.assertNotIn('create -f',script)
        self.assertNotIn('delete',script)
