"""Network/ownership/installer regressions for the loopback MCP watchdog."""
import importlib.util
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import socket
import subprocess
import tempfile
import threading
import types
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]

def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

watch = load(ROOT/'scripts'/'aurora-mcp-forward-guard-v14.py', 'aurora_v14_watch')
installer = load(ROOT/'scripts'/'apply-aurora-reliability-v14.py', 'aurora_v14_installer')


class MCPHandler(BaseHTTPRequestHandler):
    status = 200
    response_body = b'{"jsonrpc":"2.0","id":1,"result":{"serverInfo":{"name":"test"}}}'
    captured_method = None
    def do_POST(self):
        self.server.last_method = self.path
        length = int(self.headers.get('Content-Length', '0'))
        body = self.rfile.read(length)
        self.server.last_payload = body
        self.send_response(self.status)
        self.send_header('Content-Type', 'application/json')
        self.end_headers()
        self.wfile.write(self.response_body)
    def log_message(self, *_):
        pass


class WatchTests(unittest.TestCase):
    def test_true_initialize_and_502_failure(self):
        server = ThreadingHTTPServer(('127.0.0.1', 0), MCPHandler)
        server.last_method = None
        th = threading.Thread(target=server.serve_forever, daemon=True)
        th.start()
        try:
            with mock.patch.object(watch,'LOCAL_PORT',server.server_port):
                self.assertTrue(watch.mcp_initialize())
                self.assertEqual(server.last_method, '/mcp')
                self.assertIn(b'"method": "initialize"', server.last_payload)
                with mock.patch.object(MCPHandler, 'status', 502):
                    self.assertFalse(watch.mcp_initialize())
                with mock.patch.object(MCPHandler, 'response_body', b'{"jsonrpc":"2.0","result":{}}'):
                    self.assertFalse(watch.mcp_initialize())
        finally:
            server.shutdown()
            server.server_close()
            th.join(timeout=2)

    def test_stale_forward_repair_when_listener_missing(self):
        with mock.patch.object(watch, 'listen', return_value=True), \
             mock.patch.object(watch, 'mcp_initialize', return_value=False), \
             mock.patch.object(watch, 'stop_known_failed_forward') as stop, \
             mock.patch.object(watch, 'start_forward') as start, \
             mock.patch.object(watch.time, 'sleep'):
            self.assertTrue(watch.repair())
            stop.assert_called_once()
            start.assert_called_once()

    def test_live_forward_is_not_restarted(self):
        with mock.patch.object(watch, 'listen', return_value=True), \
             mock.patch.object(watch, 'mcp_initialize', return_value=True), \
             mock.patch.object(watch, 'stop_known_failed_forward') as stop, \
             mock.patch.object(watch, 'start_forward') as start:
            self.assertFalse(watch.repair())
            stop.assert_not_called()
            start.assert_not_called()

    def test_missing_bridge_fails_closed(self):
        with mock.patch.object(watch, 'listen', return_value=False), \
             mock.patch.object(watch, 'start_forward') as start:
            with self.assertRaisesRegex(RuntimeError, 'bridge'):
                watch.repair()
            start.assert_not_called()

    def test_unrelated_listener_is_never_killed(self):
        with mock.patch.object(watch, 'listener_pids', return_value=[12345]), \
             mock.patch.object(watch, 'is_our_kubectl', return_value=False), \
             mock.patch.object(watch.os, 'kill') as kill:
            with self.assertRaisesRegex(RuntimeError, 'not owned'):
                watch.stop_known_failed_forward()
            kill.assert_not_called()

    def test_gateway_502_triggers_bounded_repair_and_recheck(self):
        fail = types.SimpleNamespace(returncode=1, stdout='AGENTID_GATEWAY_DENIAL=not_verified: stage=initialize, HTTP=502')
        pass_ = types.SimpleNamespace(returncode=0, stdout='AGENTID_GATEWAY_DENIAL=verified: read and forbidden HTTP=403')
        with mock.patch.object(watch.subprocess, 'run', side_effect=[fail, pass_]) as run, \
             mock.patch.object(watch, 'repair') as recover, \
             mock.patch.object(watch.time, 'sleep'):
            watch.gateway_proof()
            self.assertEqual(run.call_count, 2)
            recover.assert_called_once()

    def test_recognize_exact_kubectl_argv(self):
        arg = 'kubectl --context k3d-amp-local -n aurora-demo port-forward svc/aurora-mcp 18101:8101 --address 127.0.0.1'
        with mock.patch.object(watch,'_process_args', return_value=arg):
            self.assertTrue(watch.is_our_kubectl(52))
        with mock.patch.object(watch,'_process_args', return_value=arg.replace('aurora-mcp','aurora-db')):
            self.assertFalse(watch.is_our_kubectl(52))
        with mock.patch.object(watch,'_process_args', return_value='ssh -L 18101:foo:8101 host'):
            self.assertFalse(watch.is_our_kubectl(52))

    def test_identical_manual_forward_without_pid_record_is_preserved(self):
        with mock.patch.object(watch, 'listener_pids', return_value=[6789]), \
             mock.patch.object(watch, 'is_our_kubectl', return_value=True), \
             mock.patch.object(watch, 'known_forward_owner', return_value=False), \
             mock.patch.object(watch.os, 'kill') as kill:
            with self.assertRaisesRegex(RuntimeError, 'not owned'):
                watch.stop_known_failed_forward()
            kill.assert_not_called()

    def test_real_gateway_proof_must_include_read_and_403(self):
        p = types.SimpleNamespace(returncode=0, stdout='AGENTID_GATEWAY_DENIAL=verified: permitted read, HTTP=403')
        with mock.patch.object(watch.subprocess,'run', return_value=p):
            watch.gateway_proof()
        with mock.patch.object(watch.subprocess,'run', return_value=types.SimpleNamespace(returncode=1,stdout='HTTP=401')):
            with self.assertRaisesRegex(RuntimeError, 'proof failed'):
                watch.gateway_proof()

    def test_startup_patcher_is_idempotent_and_does_not_remove_security_gates(self):
        src = '''#!/bin/bash
if [[ "$ACTION" == check ]]; then
  need amctl
  python3 scripts/verify-aurora-tunnel-public.py \\
    || die broken
  python3 scripts/aurora-full-reconcile.py check
  exit $?
fi
if [[ "$ACTION" == up ]]; then
  log 'Stage 4/6: Verify identity-secured MCP registration and tool scopes.'
fi
log 'Stage 6/6: Check role assignments, endpoints, and portal integration.'
python3 scripts/aurora-full-reconcile.py check
'''
        p = installer.transform_start(src)
        self.assertEqual(p.count(installer.MARK),3)
        self.assertEqual(installer.transform_start(p),p)
        self.assertIn('verify-aurora-tunnel-public.py',p)
        self.assertEqual(p.count('ensure --gateway-check'),2)
        self.assertEqual(p.count('aurora-full-reconcile.py check'),2)


if __name__ == '__main__':
    unittest.main()
