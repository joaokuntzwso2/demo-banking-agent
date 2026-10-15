"""No-network regression tests for the Aurora public MCP verification path."""
import importlib.util
import os
from pathlib import Path
import unittest
from unittest import mock

PATH = Path(__file__).resolve().parents[1] / 'scripts/verify-aurora-tunnel-public.py'
spec = importlib.util.spec_from_file_location('aurora_public_tunnel_verifier', PATH)
verifier = importlib.util.module_from_spec(spec)
spec.loader.exec_module(verifier)


class PublicTunnelStabilizationTests(unittest.TestCase):
    MARKER = {'x-aurora-origin-auth': 'required'}
    URL = 'https://example.trycloudflare.com/mcp'

    def test_uses_doh_if_python_dns_and_normal_curl_fail(self):
        with (mock.patch.dict(os.environ, {'AURORA_PUBLIC_VERIFY_WAIT_SECONDS': '0'}),
              mock.patch.object(verifier, 'http_probe', side_effect=verifier.TunnelError('DNS')),
              mock.patch.object(verifier, '_curl_probe_https',
                                side_effect=[verifier.TunnelError('DNS'), (401, self.MARKER)]) as curl):
            self.assertEqual(verifier.stable_public_probe(self.URL, self.MARKER),
                             (401, self.MARKER))
            self.assertEqual(curl.call_count, 2)
            self.assertFalse(curl.call_args_list[0].kwargs['dns_over_https'])
            self.assertTrue(curl.call_args_list[1].kwargs['dns_over_https'])

    def test_rejects_generic_401_without_origin_marker(self):
        with (mock.patch.dict(os.environ, {'AURORA_PUBLIC_VERIFY_WAIT_SECONDS': '0'}),
              mock.patch.object(verifier, 'http_probe', return_value=(401, {})),
              mock.patch.object(verifier, '_curl_probe_https', return_value=(401, {}))):
            with self.assertRaisesRegex(verifier.TunnelError, 'after 0s stabilization'):
                verifier.stable_public_probe(self.URL, self.MARKER)

    def test_rejects_wrong_origin_marker(self):
        with (mock.patch.dict(os.environ, {'AURORA_PUBLIC_VERIFY_WAIT_SECONDS': '0'}),
              mock.patch.object(verifier, 'http_probe', return_value=(401, {'x-aurora-origin-auth': 'wrong'})),
              mock.patch.object(verifier, '_curl_probe_https', return_value=(401, {'x-aurora-origin-auth': 'wrong'}))):
            with self.assertRaises(verifier.TunnelError):
                verifier.stable_public_probe(self.URL, self.MARKER)

    def test_successful_urllib_request_does_not_invoke_curl(self):
        with (mock.patch.dict(os.environ, {'AURORA_PUBLIC_VERIFY_WAIT_SECONDS': '0'}),
              mock.patch.object(verifier, 'http_probe', return_value=(401, self.MARKER)),
              mock.patch.object(verifier, '_curl_probe_https') as curl):
            self.assertEqual(verifier.stable_public_probe(self.URL, self.MARKER),
                             (401, self.MARKER))
            curl.assert_not_called()

    def test_same_hostname_is_retried_without_tunnel_rotation(self):
        with (mock.patch.dict(os.environ, {'AURORA_PUBLIC_VERIFY_WAIT_SECONDS': '5'}),
              mock.patch.object(verifier, 'http_probe',
                                side_effect=[(503, {}), (401, self.MARKER)]) as normal,
              mock.patch.object(verifier, '_curl_probe_https', return_value=(503, {})) as curl,
              mock.patch('time.monotonic', return_value=0),
              mock.patch('time.sleep') as sleeper):
            self.assertEqual(verifier.stable_public_probe(self.URL, self.MARKER),
                             (401, self.MARKER))
            self.assertEqual(normal.call_count, 2)
            self.assertEqual(curl.call_count, 2)
            sleeper.assert_called_once()
            for call in normal.call_args_list:
                self.assertEqual(call.args[0], self.URL)

    def test_curl_preserves_tls_and_uses_last_response_headers(self):
        import subprocess
        def fake_run(command, **kwargs):
            self.assertNotIn('--insecure', command)
            self.assertNotIn('-k', command)
            header_file = Path(command[command.index('--dump-header') + 1])
            header_file.write_text('HTTP/1.1 200 Connection established\r\n\r\n'
                                   'HTTP/2 401\r\nX-Aurora-Origin-Auth: required\r\n\r\n')
            return subprocess.CompletedProcess(command, 0, '401', '')
        with mock.patch('subprocess.run', side_effect=fake_run):
            self.assertEqual(verifier._curl_probe_https(self.URL, dns_over_https=True),
                             (401, self.MARKER))

    def test_wait_budget_is_bounded(self):
        with mock.patch.dict(os.environ, {'AURORA_PUBLIC_VERIFY_WAIT_SECONDS': '9999'}):
            with self.assertRaisesRegex(verifier.TunnelError, '0..600'):
                verifier.stable_public_probe(self.URL, self.MARKER)


if __name__ == '__main__':
    unittest.main()
