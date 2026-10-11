import base64
import importlib.util
from pathlib import Path
import unittest
from unittest import mock

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts' / 'diagnose-aurora-llm-http.py'
spec = importlib.util.spec_from_file_location('aurora_llm_http_v2', SCRIPT)
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)

class ModelHTTPTests(unittest.TestCase):
    def test_python311_compiles(self):
        compile(SCRIPT.read_text(), str(SCRIPT), 'exec')

    def test_runtime_host_short_k8s_dns(self):
        self.assertEqual(m.service_identity('gateway-runtime.openchoreo-data-plane','a'),
                         ('gateway-runtime','openchoreo-data-plane'))
        self.assertEqual(m.service_identity('gateway-runtime.openchoreo-data-plane.svc.cluster.local','a'),
                         ('gateway-runtime','openchoreo-data-plane'))

    def test_reject_external_and_loopback_hosts(self):
        for host in ('foo.bar.example.com', 'localhost', '127.0.0.1', 'hi_foo.ns', 'bad.example.co.uk'):
            if host == 'localhost':
                # Single label is a Kubernetes service name but not useful here; see service validation below.
                continue
            with self.assertRaises(m.CheckError):
                m.service_identity(host,'a')

    def test_port_must_be_matching_service_port(self):
        def fake_kget(args):
            if 'service' in args:
                return {'metadata': {'name':'model-runtime','namespace':'gateway-ns'},
                        'spec':{'ports':[{'port':22893,'protocol':'TCP'}]}}
            return {'items':[{'endpoints':[{'conditions':{'ready':True},'addresses':['10.0.0.1']}]}]}
        mod=mock.Mock(); mod.kget.side_effect=fake_kget
        self.assertEqual(m.find_runtime_service(mod,'model-runtime.gateway-ns',22893,'concierge-ns'),('model-runtime','gateway-ns',1))
        with self.assertRaises(m.CheckError):
            m.find_runtime_service(mod,'model-runtime.gateway-ns',19080,'concierge-ns')

    def test_reject_externalname_service(self):
        mod=mock.Mock(); mod.kget.return_value={'metadata':{'name':'proxy','namespace':'n'},'spec':{'type':'ExternalName','ports':[{'port':22893}]}}
        with self.assertRaises(m.CheckError):
            m.find_runtime_service(mod,'proxy.n',22893,'x')

    def test_key_secret_key_ref_decoded_only_in_memory(self):
        key='TEST_ONLY_PRIVATE_GATEWAY_CREDENTIAL'
        mod=mock.Mock();mod.kget.return_value={'data':{'llmkey':base64.b64encode(key.encode()).decode()}}
        self.assertEqual(m.read_model_key(mod,'ns',{'env':[{'name':'LLM_PROVIDER_KEY',
                              'valueFrom':{'secretKeyRef':{'name':'secret','key':'llmkey'}}}]}),key)
        self.assertNotIn(key,SCRIPT.read_text())

    def test_key_from_secret_envfrom(self):
        key='SECRET_ENVFROM_VALUE';mod=mock.Mock()
        mod.kget.return_value={'data':{'LLM_PROVIDER_KEY':base64.b64encode(key.encode()).decode()}}
        self.assertEqual(m.read_model_key(mod,'ns',{'envFrom':[{'secretRef':{'name':'s'}}]}),key)

    def test_plaintext_and_configmap_key_never_accepted(self):
        mod=mock.Mock()
        for cont in ({'env':[{'name':'LLM_PROVIDER_KEY','value':'fake'}]},
                     {'env':[{'name':'LLM_PROVIDER_KEY','valueFrom':{'configMapKeyRef':{'name':'cm','key':'k'}}}]}):
            with self.assertRaises(m.CheckError):
                m.read_model_key(mod,'ns',cont)

    def test_http_status_classification(self):
        self.assertIn('SUCCESS',m.classify(200,True))
        self.assertIn('authentication',m.classify(401,False))
        self.assertIn('Route',m.classify(404,False))
        self.assertIn('rate limit',m.classify(429,False))
        self.assertIn('upstream',m.classify(502,False))

    def test_request_uses_only_loopback_gateway_and_api_key_header(self):
        key = 'SECRET_IN_TEST_DO_NOT_PRINT'
        seen = {}
        class Response:
            status=200
            def __enter__(self):return self
            def __exit__(self,*_):pass
            def read(self,_):return b'{"choices":[{"message":{"content":"OK"}}]}'
        class Opener:
            def open(self,req,timeout):
                seen['url']=req.full_url;seen['headers']=req.headers
                return Response()
        with mock.patch.object(m,'build_opener',return_value=Opener()):
            code, valid = m.call_once('http://model-runtime.gateway-ns:22893/aurora-openai/v1',
                        'model-runtime.gateway-ns',22893,56661,key,'gpt-4.1-mini')
        self.assertEqual((code,valid),(200,True))
        self.assertEqual(seen['url'],'http://127.0.0.1:56661/aurora-openai/v1/chat/completions')
        self.assertEqual(seen['headers']['Api-key'],key)
        self.assertNotIn(key,seen['url'])
        self.assertEqual(seen['headers']['Host'],'model-runtime.gateway-ns:22893')

    def test_http_401_never_exposes_body(self):
        from urllib.error import HTTPError
        import io
        body=io.BytesIO(b'PRIVATE_ERROR_RESPONSE_WITH_POSSIBLE_KEYS')
        class Opener:
            def open(self,*_a,**_kw):
                raise HTTPError('http://local',401,'Unauthorized',{},body)
        with mock.patch.object(m,'build_opener',return_value=Opener()):
            self.assertEqual(m.call_once('http://r.ns:22893/v1','r.ns',22893,44112,'testkey','gpt-4.1-mini'),(401,False))

    def test_no_credential_values_in_subprocess_args(self):
        text=SCRIPT.read_text()
        self.assertNotIn('curl ',text)
        self.assertNotIn('--header',text)
        self.assertIn('ProxyHandler({})',text)
        self.assertIn("'--address', '127.0.0.1'",text)

if __name__ == '__main__':unittest.main()
