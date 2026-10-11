"""Python 3 / OCI exec portability tests; no real cluster, no live credentials."""
import ast
import importlib.util
from pathlib import Path
import subprocess
import types
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
PATH = ROOT / 'scripts/aurora-full-reconcile.py'
SPEC=importlib.util.spec_from_file_location('reconciler_v92', PATH)
m=importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(m)

class PodPythonProbeTests(unittest.TestCase):
    def args(self):
        return ('dp-default-banco-aurora-default-test','aurora-concierge-123',
                'import json; print(json.dumps({"ok":true}))', 'f0'*32,
                ['AGENT_ACCOUNT_API_KEY','AGENT_CARD_API_KEY'])
    @staticmethod
    def missing(path):
        return types.SimpleNamespace(returncode=1, stdout='', stderr=(
            'error: Internal error occurred: OCI runtime exec failed: '
            f'exec: "{path}": executable file not found in $PATH'))
    @staticmethod
    def good():
        return types.SimpleNamespace(returncode=0,stdout='{"ok":true}',stderr='')

    def test_prefers_python3_without_trying_python_alias(self):
        with mock.patch.object(m,'cmd',return_value=self.good()) as proc:
            res=m.exec_concierge_python_proof(*self.args())
        self.assertEqual(res.stdout,'{"ok":true}')
        self.assertEqual(proc.call_count,1)
        c=proc.call_args.args[0]
        self.assertEqual(c[c.index('--')+1], 'python3')
        self.assertEqual(c[-2:], ['AGENT_ACCOUNT_API_KEY','AGENT_CARD_API_KEY'])

    def test_missing_python3_tries_python312(self):
        calls=[]
        def run(c,**kw):
            tool=c[c.index('--')+1]
            calls.append(tool)
            return self.missing(tool) if tool=='python3' else self.good()
        with mock.patch.object(m,'cmd',side_effect=run):
            m.exec_concierge_python_proof(*self.args())
        self.assertEqual(calls,['python3','python3.12'])

    def test_missing_all_interpreters_fails_closed(self):
        with mock.patch.object(m,'cmd',side_effect=lambda c,**kw:self.missing(c[c.index('--')+1])) as proc:
            with self.assertRaisesRegex(RuntimeError,'none of the expected Python 3'):
                m.exec_concierge_python_proof(*self.args())
        self.assertEqual(proc.call_count,7)

    def test_permission_denied_never_falls_back(self):
        denied=types.SimpleNamespace(returncode=1,stdout='',stderr='Error from server (Forbidden): pods/exec is forbidden')
        with mock.patch.object(m,'cmd',return_value=denied) as proc:
            with self.assertRaisesRegex(RuntimeError,'not an executable-not-found'):
                m.exec_concierge_python_proof(*self.args())
        self.assertEqual(proc.call_count,1)

    def test_missing_pod_does_not_count_as_missing_executable(self):
        missing=types.SimpleNamespace(returncode=1,stdout='',stderr='Error from server (NotFound): pods "x" not found')
        with mock.patch.object(m,'cmd',return_value=missing) as proc:
            with self.assertRaises(RuntimeError):
                m.exec_concierge_python_proof(*self.args())
        self.assertEqual(proc.call_count,1)

    def test_python_runtime_failure_never_retries(self):
        traceback=types.SimpleNamespace(returncode=1, stdout='',stderr='Traceback: ValueError: failed to read environment')
        with mock.patch.object(m,'cmd',return_value=traceback) as proc:
            with self.assertRaises(RuntimeError):
                m.exec_concierge_python_proof(*self.args())
        self.assertEqual(proc.call_count,1)

    def test_timeout_fails_closed(self):
        with mock.patch.object(m,'cmd',side_effect=subprocess.TimeoutExpired('kubectl',40)) as proc:
            with self.assertRaisesRegex(RuntimeError,'timed out'):
                m.exec_concierge_python_proof(*self.args())
        self.assertEqual(proc.call_count,1)

    def test_absolutepath_missing_error_then_success(self):
        calls=[]
        def run(c,**kw):
            exe=c[c.index('--')+1];calls.append(exe)
            if exe=='/usr/local/bin/python3':return self.good()
            return types.SimpleNamespace(returncode=1,stdout='',stderr=f'OCI runtime exec failed: exec: "{exe}": stat {exe}: no such file or directory')
        with mock.patch.object(m,'cmd',side_effect=run):
            m.exec_concierge_python_proof(*self.args())
        self.assertEqual(calls[-1],'/usr/local/bin/python3')
        self.assertEqual(len(calls),4)

    def test_no_credential_values_in_argv(self):
        with mock.patch.object(m,'cmd',return_value=self.good()) as proc:
            m.exec_concierge_python_proof(*self.args())
        flat=' '.join(proc.call_args.args[0])
        self.assertNotIn('TEST-SECRET-NEVER-SEND',flat)
        self.assertIn('AGENT_ACCOUNT_API_KEY',flat)

    def test_python311_grammar_compiles(self):
        ast.parse(PATH.read_text(),filename=str(PATH),feature_version=(3,11))

if __name__=='__main__':unittest.main()
