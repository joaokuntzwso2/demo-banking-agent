"""Verify conservative downgrade only for an OCI-verified missing interpreter.

No live cluster, secrets, or writes. The same live pod/Secret fixtures are used
by the v8 test suite.
"""
import importlib.util
import sys
from pathlib import Path
import unittest
from unittest import mock
import types
import json
import base64
import subprocess

ROOT=Path(__file__).resolve().parents[1]
SPEC=importlib.util.spec_from_file_location('aurora_v93',ROOT/'scripts/aurora-full-reconcile.py')
m=importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(m)
KEYS=['BANK_CORE_READ_KEY','BANK_APPROVAL_KEY','CONCIERGE_CALL_KEY',
      'CONCIERGE_APPROVAL_KEY','SPECIALIST_CALL_KEY','SPECIALIST_EXECUTION_KEY',
      *(f'AGENT_{x.upper()}_API_KEY' for x in m.ROLES)]
CREDS={x:'TEST-safe-secret-'+x for x in KEYS}
NS='dp-default-banco-aurora-default-test'

def proc(stdout=None,stderr='',returncode=0):
    return types.SimpleNamespace(stdout=stdout or '',stderr=stderr,returncode=returncode)

def command_fixture(*,bad=None,ready=True):
    """Build a fake kubectl GET side effect which rejects ALL writes."""
    data={k:base64.b64encode((v if k!=bad else 'WRONG').encode()).decode()
          for k,v in CREDS.items()}
    def cli(c,**kw):
        assert c[0]=='kubectl',c
        assert 'get' in c and 'exec' not in c, 'Kubernetes mutation or exec in fake inventory'
        if c[c.index('get')+1]=='svc':
            return proc(json.dumps({'items':[{'metadata':{'name':'aurora-concierge','namespace':NS}}]}))
        if c[c.index('get')+1]=='pods':
            return proc(json.dumps({'items':[{'metadata':{'name':'aurora-concierge-abc'},
               'spec':{'containers':[{'name':'main','envFrom':[{'secretRef':{'name':'mcp-agent-secrets'}}]}]},
               'status':{'phase':'Running','containerStatuses':[{'name':'main','ready':ready}]}}]}))
        if c[c.index('get')+1]=='secret':
            return proc(json.dumps({'metadata':{'namespace':NS},'data':data}))
        raise AssertionError(c)
    return cli

class MinimalImageCases(unittest.TestCase):
    def run_audit(self,*,bad=None,ready=True,mode='no-python',strict=False):
        calls=[]
        def no_exec(*args,**kw):
            calls.append('exec')
            if mode=='no-python':
                raise m.PodPythonUnavailable('All allowed Python paths are absent')
            if mode=='timeout':
                raise RuntimeError('Kubernetes exec timed out')
            if mode=='rbac':
                raise RuntimeError('forbidden: cannot exec')
            if mode=='bad-hmac':
                return proc(json.dumps({k:'0'*64 for k in KEYS}))
            raise AssertionError(mode)
        with mock.patch.object(m,'cmd',side_effect=command_fixture(bad=bad,ready=ready)) as kubectl, \
             mock.patch.object(m,'exec_concierge_python_proof',side_effect=no_exec), \
             mock.patch.object(m,'log') as logger:
            result=m.verify_concierge_managed_secrets(CREDS,require_runtime=True,strict_process=strict)
        return result,logger,kubectl,calls

    def test_minimal_image_allows_pod_secret_proof_but_reports_process_unverified(self):
        result,logger,kubectl,exec_calls=self.run_audit()
        self.assertEqual(result,'kubernetes-only')
        self.assertEqual(kubectl.call_count,3)
        self.assertEqual(exec_calls,['exec'])
        message=' '.join(str(c) for c in logger.call_args_list)
        self.assertIn('NOT proven',message)
        self.assertIn('Portal/gateway and end-to-end checks',message)
        for v in CREDS.values():self.assertNotIn(v,message)

    def test_strict_process_fails_closed(self):
        with self.assertRaisesRegex(m.PodPythonUnavailable,'All allowed'):
            self.run_audit(strict=True)

    def test_mismatched_secret_is_rejected_even_on_minimal_image(self):
        with self.assertRaisesRegex(RuntimeError,'AGENT_PIX_API_KEY'):
            self.run_audit(bad='AGENT_PIX_API_KEY')

    def test_unready_pod_is_not_accepted(self):
        with self.assertRaisesRegex(RuntimeError,'not Ready'):
            self.run_audit(ready=False)

    def test_permission_and_timeout_remain_fatal(self):
        for mode in ['rbac','timeout','bad-hmac']:
            with self.subTest(mode=mode),self.assertRaises(RuntimeError):
                self.run_audit(mode=mode)

    def test_bad_yaml_bad_name_does_not_degrade(self):
        self.assertTrue(issubclass(m.PodPythonUnavailable,RuntimeError))
        self.assertFalse(issubclass(subprocess.TimeoutExpired,m.PodPythonUnavailable))

    def test_source_python311_grammar(self):
        import ast
        for name in ('aurora-full-reconcile.py','verify-aurora-concierge-secrets.py'):
            ast.parse((ROOT/'scripts'/name).read_text(),feature_version=(3,11))

if __name__=='__main__':unittest.main()
