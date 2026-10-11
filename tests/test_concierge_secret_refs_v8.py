"""Existing Concierge must be verified from effective Secret refs, not agent GET metadata.

All fixtures are synthetic and contain no real Banco Aurora credentials.
"""
import base64
import copy
import importlib.util
import json
import hmac
import hashlib
import pathlib
import types
import unittest
from unittest import mock

ROOT=pathlib.Path(__file__).resolve().parents[1]
SPEC=importlib.util.spec_from_file_location('reconciler_concierge_v8',ROOT/'scripts/aurora-full-reconcile.py')
m=importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(m)

CREDS={k:'TEST-high-entropy-'+k+'-token' for k in
       ('BANK_CORE_READ_KEY','BANK_APPROVAL_KEY','CONCIERGE_CALL_KEY',
        'CONCIERGE_APPROVAL_KEY','SPECIALIST_CALL_KEY','SPECIALIST_EXECUTION_KEY',
        *(f'AGENT_{r.upper()}_API_KEY' for r in m.ROLES))}
NS='dp-default-banco-aurora-default-test'

def secret(name,values):
    return {'metadata':{'name':name,'namespace':NS},
            'data':{k:base64.b64encode(v.encode()).decode() for k,v in values.items()}}

def pod(container,*,ready=True):
    return {'metadata':{'name':'aurora-concierge-abcdef'},
            'spec':{'containers':[{'name':'main',**container}]},
            'status':{'phase':'Running','containerStatuses':[{'name':'main','ready':ready}]}}

class SecretSourceTests(unittest.TestCase):
    def test_env_from_secret_succeeds_all_eleven(self):
        refs={'envFrom':[{'secretRef':{'name':'managed-env'}}]}
        found=m.concierge_secret_sources(refs,lambda name:secret(name,CREDS))
        self.assertEqual(set(found),set(CREDS))
        self.assertEqual(found['AGENT_ACCOUNT_API_KEY'][0],CREDS['AGENT_ACCOUNT_API_KEY'].encode())
    def test_explicit_secret_key_ref_succeeds(self):
        refs={'env':[{'name':k,'valueFrom':{'secretKeyRef':{'name':'mng','key':k}}} for k in CREDS]}
        found=m.concierge_secret_sources(refs,lambda name:secret(name,CREDS))
        self.assertEqual(set(found),set(CREDS))
    def test_envfrom_prefix_matches_logical_key(self):
        src={'ACCOUNT_API_KEY':CREDS['AGENT_ACCOUNT_API_KEY']}
        found=m.concierge_secret_sources({'envFrom':[{'prefix':'AGENT_','secretRef':{'name':'mng'}}]},lambda name:secret(name,src))
        self.assertIn('AGENT_ACCOUNT_API_KEY',found)
    def test_plaintext_sensitive_env_is_rejected(self):
        with self.assertRaisesRegex(RuntimeError,'plaintext'):
            m.concierge_secret_sources({'env':[{'name':'AGENT_ACCOUNT_API_KEY','value':'NOT-ALLOWED'}]},lambda name:secret(name,CREDS))
    def test_duplicate_sources_rejected(self):
        with self.assertRaisesRegex(RuntimeError,'duplicate secret source'):
            m.concierge_secret_sources({'envFrom':[{'secretRef':{'name':'a'}},{'secretRef':{'name':'b'}}]},lambda name:secret(name,CREDS))
    def test_unrelated_fields_not_mistaken_for_secret(self):
        found=m.concierge_secret_sources({'env':[{'name':'USE_LLM_PROVIDER','value':'true'}]},lambda name:secret(name,CREDS))
        self.assertEqual(found,{})
    def test_missing_secret_key_rejected(self):
        with self.assertRaisesRegex(RuntimeError,'missing key'):
            m.concierge_secret_sources({'env':[{'name':'AGENT_ACCOUNT_API_KEY','valueFrom':{'secretKeyRef':{'name':'s','key':'oops'}}}]},lambda name:secret(name,CREDS))
    def test_secret_data_malformed_rejected(self):
        with self.assertRaisesRegex(RuntimeError,'malformed encoded'):
            m.concierge_secret_sources({'env':[{'name':'AGENT_ACCOUNT_API_KEY','valueFrom':{'secretKeyRef':{'name':'s','key':'k'}}}]},lambda name:{'data':{'k':'@notbase64'}})

class RuntimeAuditTests(unittest.TestCase):
    def do_audit(self, *, vars=None, ready=True, two_pods=False, with_runtime=False, bad_runtime_key=None):
        vars=CREDS if vars is None else vars
        resources={
            'svc':{'items':[{'metadata':{'name':'aurora-concierge','namespace':NS}}]},
            'pods':{'items':[pod({'envFrom':[{'secretRef':{'name':'managed-env'}}]},ready=ready)]},
            'secret':secret('managed-env',vars),
        }
        if two_pods:
            second=copy.deepcopy(resources['pods']['items'][0]); second['metadata']['name']='aurora-concierge-other'
            resources['pods']['items'].append(second)
        calls=[]
        def cli(args,**kwargs):
            calls.append(args)
            if 'exec' in args:
                nonce=bytes.fromhex(args[len(args)-1-args[::-1].index('-c')+2])
                keys=args[len(args)-1-args[::-1].index('-c')+3:]
                proof={k:hmac.new(nonce,CREDS[k].encode(),hashlib.sha256).hexdigest() for k in keys}
                if bad_runtime_key:proof[bad_runtime_key]='BAD-PROOF'
                return types.SimpleNamespace(stdout=json.dumps(proof))
            if 'svc' in args:return types.SimpleNamespace(stdout=json.dumps(resources['svc']))
            if 'pods' in args:return types.SimpleNamespace(stdout=json.dumps(resources['pods']))
            if 'secret' in args:return types.SimpleNamespace(stdout=json.dumps(resources['secret']))
            raise AssertionError(args)
        with mock.patch.object(m,'cmd',side_effect=cli),mock.patch.object(m,'log') as logger:
            m.verify_concierge_managed_secrets(CREDS,require_runtime=with_runtime)
        return calls,logger
    def test_full_live_audit_matches_without_writes(self):
        calls,logger=self.do_audit()
        self.assertEqual(len(calls),3)
        self.assertTrue(all(c[0]=='kubectl' and 'get' in c for c in calls))
        self.assertTrue(logger.called)
    def test_unready_existing_pod_can_prove_secrets(self):
        calls,_=self.do_audit(ready=False)
        self.assertEqual(len(calls),3)
    def test_ready_process_hmac_runtime_proof_valid(self):
        calls,_=self.do_audit(with_runtime=True)
        self.assertEqual(len(calls),4)
        self.assertIn('exec',calls[-1])
        self.assertNotIn(CREDS['AGENT_ACCOUNT_API_KEY'],' '.join(calls[-1]))
    def test_unready_process_does_not_claim_live_verification(self):
        with self.assertRaisesRegex(RuntimeError,'not Ready'):
            self.do_audit(with_runtime=True,ready=False)
    def test_runtime_stale_key_is_detected_even_if_secret_resource_current(self):
        with self.assertRaisesRegex(RuntimeError,'AGENT_PIX_API_KEY'):
            self.do_audit(with_runtime=True,bad_runtime_key='AGENT_PIX_API_KEY')
    def test_wrong_gateway_key_fails_without_printing_key(self):
        altered=dict(CREDS);altered['AGENT_ACCOUNT_API_KEY']='DIFFERENT-secret'
        with self.assertRaisesRegex(RuntimeError,'AGENT_ACCOUNT_API_KEY') as cm:
            self.do_audit(vars=altered)
        self.assertNotIn(altered['AGENT_ACCOUNT_API_KEY'],str(cm.exception))
    def test_missing_gateway_key_fails_and_no_mutation(self):
        altered=dict(CREDS);altered.pop('AGENT_CARD_API_KEY')
        with self.assertRaisesRegex(RuntimeError,'AGENT_CARD_API_KEY'):
            self.do_audit(vars=altered)
    def test_two_unready_pods_ambiguous(self):
        with self.assertRaisesRegex(RuntimeError,'multiple possible'):
            self.do_audit(two_pods=True,ready=False)
    def test_original_flawed_agent_get_check_gone(self):
        code=(ROOT/'scripts/aurora-full-reconcile.py').read_text()
        method=code.split('def managed_agents():',1)[1].split('\ndef check():',1)[0]
        self.assertNotIn("serialized=json.dumps(obj)",method)
        self.assertIn('verify_concierge_managed_secrets(creds, require_runtime=True)',method)
        self.assertNotIn("'--env-secret'",method)
    def test_existing_and_created_agents_both_verified_after_deploy(self):
        code=(ROOT/'scripts/aurora-full-reconcile.py').read_text()
        method=code.split('def managed_agents():',1)[1].split('\ndef check():',1)[0]
        self.assertNotIn('serialized=json.dumps(obj)',method)
        self.assertIn('verify_concierge_managed_secrets(creds, require_runtime=True)\n    configure_portal()',method)

if __name__=='__main__':unittest.main()
