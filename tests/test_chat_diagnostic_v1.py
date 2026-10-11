"""Safe diagnostics: no bank mutations, key printing, or hardcoded local ports."""
import ast
import importlib.util
import io
from pathlib import Path
import unittest
from unittest import mock
import urllib.error

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('diagnose_chat',ROOT/'scripts/diagnose-aurora-chat.py')
d=importlib.util.module_from_spec(spec)
spec.loader.exec_module(d)

class FakeHTTPErrorOpener:
    def __init__(self, body): self.body=body
    def open(self, req, timeout):
        raise urllib.error.HTTPError(req.full_url,503,'Unavailable',{},io.BytesIO(self.body))

class ChatDiagnosticTests(unittest.TestCase):
    def test_safe_detail_scrubs_private_keys(self):
        private={'CONCIERGE_API_KEY':'super-very-private-gateway-key-001',
                 'DEMO_ACCESS_CODE':'local-access-code-secret-201'}
        body=b'{"detail":"upstream super-very-private-gateway-key-001 and local-access-code-secret-201 rejected"}'
        message=d.safe_detail(body,private)
        self.assertNotIn('super-very-private',message)
        self.assertNotIn('local-access-code',message)
        self.assertIn('[REDACTED]',message)

    def test_http_exception_returns_safe_detail_without_secrets(self):
        key='not-to-show-gateway-key'
        opener=FakeHTTPErrorOpener(('{"detail":"Bearer '+key+'"}').encode())
        status,obj,detail=d.request(opener,'POST','http://127.0.0.1:1234/api/chat',
                                      {'GATEWAY_KEY':key},payload={'message':'balance'})
        self.assertEqual(status,503)
        self.assertNotIn(key,detail)
        self.assertIsNone(obj)

    def test_no_bank_reset_approval_kubectl_mutation_or_plaintext_secrets(self):
        source=(ROOT/'scripts/diagnose-aurora-chat.py').read_text()
        self.assertNotIn("'/api/reset'",source)
        self.assertNotIn("'/approve'",source)
        self.assertNotIn("'kubectl','patch'",source)
        self.assertNotIn("'kubectl','delete'",source)
        self.assertNotIn("'kubectl','apply'",source)
        self.assertIn("--','python','-c',POD_PROBE",source)
        self.assertIn("':8300'",source)
        self.assertIn("--address', '127.0.0.1'",source)
        self.assertNotIn("'8300:8300'",source)

    def test_port_forward_teardown_only_owned_process(self):
        fake=mock.Mock()
        fake.poll.return_value=None
        fake.wait.return_value=0
        log=mock.Mock()
        d.terminate_forward(fake,log)
        fake.terminate.assert_called_once()
        fake.wait.assert_called_once()
        log.close.assert_called_once()

    def test_python311_syntax(self):
        ast.parse((ROOT/'scripts/diagnose-aurora-chat.py').read_text(),feature_version=(3,11))

    def test_inpod_probe_never_emits_full_chat_response(self):
        self.assertIn('AURORA_CHAT_DIAG=',d.POD_PROBE)
        self.assertNotIn("print(res.text)",d.POD_PROBE)
        self.assertNotIn("print(key)",d.POD_PROBE)

if __name__=='__main__':unittest.main()
