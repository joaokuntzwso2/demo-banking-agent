"""Regression tests for the public-tunnel verification and relay marker."""
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import urlopen
import importlib.util
import subprocess
import tempfile
import threading
import unittest
import os

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / 'scripts' / 'publish-mcp-secure-tunnel.sh'
BRIDGE = ROOT / 'agent-manager-support' / 'secure_origin_bridge.py'


def load_bridge():
    spec = importlib.util.spec_from_file_location('aurora_tunnel_bridge_v2', BRIDGE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TunnelReliabilityTests(unittest.TestCase):
    def test_bash_syntax(self):
        subprocess.run(['bash', '-n', str(SCRIPT)], check=True)

    def test_public_check_never_uses_insecure_or_forced_no_proxy(self):
        source = SCRIPT.read_text()
        self.assertNotIn('curl --insecure', source)
        self.assertNotIn('curl -k ', source)
        self.assertIn('has_origin_marker "$STATE/public-headers.txt"', source)
        self.assertIn('verify_public_origin "$url"', source)
        self.assertIn('diagnose) print_diagnostics', source)
        self.assertIn('DoH', source)

    def test_authenticated_bridge_marks_its_own_401(self):
        bridge = load_bridge()
        class TestHandler(bridge.Handler):
            origin_key = 'synthetic-secret-0123456789' * 3
        server = ThreadingHTTPServer(('127.0.0.1', 0), TestHandler)
        t = threading.Thread(target=server.serve_forever, daemon=True)
        t.start()
        try:
            with self.assertRaises(HTTPError) as ex:
                urlopen(f'http://127.0.0.1:{server.server_port}/mcp', timeout=3)
            self.assertEqual(ex.exception.code, 401)
            self.assertEqual(ex.exception.headers.get('X-Aurora-Origin-Auth'), 'required')
            self.assertEqual(ex.exception.headers.get('Cache-Control'), 'no-store')
        finally:
            server.shutdown()
            server.server_close()

    def test_false_401_without_bridge_marker_is_rejected(self):
        # Test marker parsing in isolation with the actual shell function.
        script = SCRIPT.read_text()
        start = script.index('has_origin_marker(){')
        end = script.index('\nverify_local_origin(){', start)
        with tempfile.TemporaryDirectory() as d:
            marked = Path(d) / 'marked'
            false = Path(d) / 'false'
            marked.write_bytes(b'HTTP/2 401\r\nX-Aurora-Origin-Auth: required\r\n\r\n')
            false.write_bytes(b'HTTP/2 401\r\nServer: cloudflare\r\n\r\n')
            fun = script[start:end]
            ok = subprocess.run(['bash', '-c', fun + f'\nhas_origin_marker "{marked}"'], capture_output=True)
            no = subprocess.run(['bash', '-c', fun + f'\nhas_origin_marker "{false}"'], capture_output=True)
            self.assertEqual(ok.returncode, 0, ok.stderr)
            self.assertNotEqual(no.returncode, 0)


if __name__ == '__main__':
    unittest.main()
