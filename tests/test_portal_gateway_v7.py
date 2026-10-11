import hashlib
import hmac
import importlib.util
import json
import pathlib
import tempfile
import unittest
from unittest import mock

HERE=pathlib.Path(__file__).resolve().parent.parent

def module(path, name):
    spec=importlib.util.spec_from_file_location(name,HERE/'scripts'/path)
    mod=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

patcher=module('patch-aurora-portal-gateway.py','portal_patcher')
verifier=module('verify-aurora-portal.py','portal_verifier')

BFF='''import os
import httpx
CONCIERGE = os.getenv('CONCIERGE_URL', 'http://localhost:8200')
def gateway_headers(approval=False):
    return {'X-API-Key':os.getenv('CONCIERGE_API_KEY','')}
async def agent(method,path,payload=None,approval=False):
    async with httpx.AsyncClient(timeout=90) as client:
        res=await client.request(method,CONCIERGE.rstrip('/')+path,json=payload,
                                 headers=gateway_headers(approval))
        return res.json()
'''

class PortalGatewayTests(unittest.TestCase):
    def test_patcher_is_idempotent_and_keeps_key_server_side(self):
        new,changed=patcher.transform(BFF)
        self.assertTrue(changed)
        self.assertIn("'Host': parsed.netloc",new)
        self.assertIn('**gateway_headers(approval)',new)
        self.assertNotIn('OPENAI_API_KEY',new)
        twice,changed=patcher.transform(new)
        self.assertEqual(new,twice)
        self.assertFalse(changed)
    def test_patcher_rejects_unknown_bff_without_changes(self):
        with self.assertRaisesRegex(ValueError,'differs'):
            patcher.transform(BFF.replace('timeout=90','timeout=81'))
    def test_known_localhost_rewrite_only_inside_cluster(self):
        src,_=patcher.transform(BFF)
        env={'CONCIERGE_URL':'http://default-default.am-gateway.localhost:19080/aurora-concierge',
             'WSO2_MODE':'true','KUBERNETES_SERVICE_HOST':'10.0.0.1'}
        ns={'__name__':'aurora_test'}
        with mock.patch.dict('os.environ',env,clear=True):
            exec(src,ns)
            url,headers=ns['concierge_transport']()
        self.assertEqual(url,'http://gateway-default.openchoreo-data-plane.svc.cluster.local:19080/aurora-concierge')
        self.assertEqual(headers,{'Host':'default-default.am-gateway.localhost:19080'})
        with mock.patch.dict('os.environ',{k:v for k,v in env.items() if k!='KUBERNETES_SERVICE_HOST'},clear=True):
            exec(src,ns)
            url,headers=ns['concierge_transport']()
        self.assertEqual(url,env['CONCIERGE_URL'])
        self.assertEqual(headers,{})
    def test_rewrite_does_not_accept_wrong_port(self):
        src,_=patcher.transform(BFF)
        ns={}
        with mock.patch.dict('os.environ',{'CONCIERGE_URL':'http://default-default.am-gateway.localhost:10000/aurora-concierge','WSO2_MODE':'true','KUBERNETES_SERVICE_HOST':'10.0.0.1'},clear=True):
            exec(src,ns)
            with self.assertRaisesRegex(RuntimeError,'Unrecognized'):
                ns['concierge_transport']()
    def test_pick_one_ready_portal_pod(self):
        def pod(name,ready):
            return {'metadata':{'name':name},'spec':{'containers':[{'name':'portal'}]},'status':{'phase':'Running','conditions':[{'type':'Ready','status':'True' if ready else 'False'}],'containerStatuses':[{'ready':ready}]}}
        obj={'items':[pod('aurora-portal-abc',False),pod('aurora-portal-def',True)]}
        self.assertEqual(verifier.pick_portal_pod(obj),('aurora-portal-def','portal'))
        with self.assertRaisesRegex(RuntimeError,'one Ready'):
            verifier.pick_portal_pod({'items':[pod('aurora-portal-one',True),pod('aurora-portal-two',True)]})
    def test_gateway_proofs_and_200_required(self):
        nonce=b'1'*32
        local={'CONCIERGE_URL':'http://default-default.am-gateway.localhost:19080/aurora-concierge','CONCIERGE_API_KEY':'hidden-high-entropy-secret'}
        proofs={'url_proof':hmac.new(nonce,local['CONCIERGE_URL'].encode(),'sha256').hexdigest(),
                'key_proof':hmac.new(nonce,local['CONCIERGE_API_KEY'].encode(),'sha256').hexdigest(),
                'wso2_mode':True,'provider_key_in_portal':False,'gateway_http_status':200}
        verifier.audit_payload(proofs,local,nonce)
        with self.assertRaisesRegex(RuntimeError,'does not match'):
            verifier.audit_payload({**proofs,'key_proof':'wrong'},local,nonce)
        with self.assertRaisesRegex(RuntimeError,'cannot reach'):
            verifier.audit_payload({**proofs,'gateway_http_status':403},local,nonce)
        with self.assertRaisesRegex(RuntimeError,'OpenAI'):
            verifier.audit_payload({**proofs,'provider_key_in_portal':True},local,nonce)
    def test_reconciler_will_verify_portal_after_rollout(self):
        s=(HERE/'scripts/aurora-full-reconcile.py').read_text()
        part=s.split('def configure_portal():',1)[1].split('\ndef verify_scenarios():',1)[0]
        self.assertIn("'scripts/verify-aurora-portal.py'",part)
        self.assertIn('CONCIERGE_API_KEY',part)
        self.assertIn("'support'",part)

if __name__=='__main__':
    unittest.main()
