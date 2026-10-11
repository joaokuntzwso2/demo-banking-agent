"""Prove the CLI audit actually recognizes observed v1.0.0 detail documents."""
import importlib.util
import io
import json
import pathlib
import unittest
from unittest import mock

ROOT=pathlib.Path(__file__).resolve().parents[1]
FIX=json.loads((ROOT/'tests/fixtures/amp_100_llm_live.json').read_text())
import sys
sys.path.insert(0,str(ROOT/'scripts'))
spec=importlib.util.spec_from_file_location('aurora_llm_audit_command',ROOT/'scripts/diagnose-aurora-llm.py')
m=importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

class AuditRuntimeTest(unittest.TestCase):
    def test_real_captured_account_and_card_report_verified(self):
        def fake_get(path):
            name='aurora-account' if '/aurora-account/' in path else 'aurora-card'
            if path.endswith('/model-configs'):
                return FIX[name]['list']
            return FIX[name]['detail']
        output=io.StringIO()
        with mock.patch.object(m,'read_json',return_value=FIX['provider_list']), \
             mock.patch.object(m,'get',side_effect=fake_get) as get, \
             mock.patch('sys.stdout',new=output):
            self.assertEqual(m.main(['--agents','aurora-account','aurora-card']),0)
        self.assertIn('aurora-account: VERIFIED',output.getvalue())
        self.assertIn('aurora-card: VERIFIED',output.getvalue())
        self.assertEqual(get.call_count,4)

if __name__=='__main__':unittest.main()
