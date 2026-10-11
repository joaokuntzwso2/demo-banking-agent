import importlib.util
from pathlib import Path
import sys
import unittest
from unittest import mock

P = Path(__file__).resolve().parents[1] / 'scripts' / 'diagnose-aurora-core-path.py'
spec = importlib.util.spec_from_file_location('aurora_corepath', P)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

class Tests(unittest.TestCase):
    def test_syntax_python311(self):
        compile(P.read_text(), str(P), 'exec', _feature_version=11)
    def test_only_known_probe_fields(self):
        out=m.parse_probe_output('DNS=ok\nFQDN_HTTP=failed\nSERVICE_IP_HTTP=ok\nENDPOINT_IP_HTTP=ok')
        self.assertIn('DNS resolution', m.interpret(out))
    def test_incomplete_fails(self):
        with self.assertRaisesRegex(RuntimeError, 'incomplete'):
            m.parse_probe_output('DNS=ok')
    def test_duplicate_fails(self):
        with self.assertRaisesRegex(RuntimeError, 'Duplicate'):
            m.parse_probe_output('DNS=ok\nDNS=failed\nFQDN_HTTP=ok\nSERVICE_IP_HTTP=ok\nENDPOINT_IP_HTTP=ok')
    def test_select_ready_only(self):
        o={'items':[{'metadata':{'name':'aurora-concierge-abc'},'status':{'phase':'Running','containerStatuses':[{'name':'main','ready':True}]},'spec':{'containers':[{'name':'main'}]}}]}
        self.assertEqual(m.choose_concierge(o)[0],'aurora-concierge-abc')
    def test_service_slices(self):
        ip,ep=m.core_addresses({'spec':{'clusterIP':'10.43.0.2','ports':[{'port':8100,'targetPort':8100}]}},{'items':[{'endpoints':[{'addresses':['10.42.0.2'],'conditions':{'ready':True}}]}]})
        self.assertEqual((ip,ep),('10.43.0.2',['10.42.0.2']))
    def test_proxy_names_do_not_expose_values(self):
        self.assertEqual(m.proxy_env_info({'env':[{'name':'HTTPS_PROXY','value':'http://SENSITIVE'}]}),(['HTTPS_PROXY'],False))
    def test_no_probe_by_default(self):
        pod={'metadata':{'name':'aurora-concierge-abc'},'status':{'phase':'Running','containerStatuses':[{'name':'main','ready':True}]},'spec':{'containers':[{'name':'main'}]}}
        svc={'spec':{'clusterIP':'10.43.0.2','ports':[{'port':8100,'targetPort':8100}]}}
        eps={'items':[{'endpoints':[{'addresses':['10.42.0.2'],'conditions':{'ready':True}}]}]}
        with mock.patch.object(m,'get_json',side_effect=[{'items':[pod]},svc,eps,{'items':[]},{'items':[]}]), mock.patch.object(m,'probe') as active:
            self.assertEqual(m.main([]),0)
            active.assert_not_called()
    def test_probe_uses_no_secrets_or_post(self):
        shell=m.make_probe_shell('10.43.0.2','10.42.0.2')
        self.assertIn('/health',shell)
        self.assertNotIn('X-Internal-Key',shell)
        self.assertNotIn('/workflows',shell)
    def test_source_no_delete_or_patch_kubernetes_resources(self):
        src=P.read_text()
        self.assertNotIn("'delete'",src)
        self.assertNotIn("'apply'",src)
        self.assertNotIn("'patch'",src)
if __name__=='__main__':unittest.main()
