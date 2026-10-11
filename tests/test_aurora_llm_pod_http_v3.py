"""Focused Python 3.11-safe tests for in-Pod governed model diagnosis."""
import importlib.util
from pathlib import Path
import subprocess
import unittest
import json
import sys
from http.server import HTTPServer, BaseHTTPRequestHandler
from threading import Thread
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts' / 'diagnose-aurora-llm-pod-http.py'
spec = importlib.util.spec_from_file_location('aurora_pod_llm_v3', SCRIPT)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

class FakeMod:
    CTX = 'k3d-amp-local'

class PodHTTPTests(unittest.TestCase):
    def test_python311_compiles(self):
        compile(SCRIPT.read_text(), str(SCRIPT), 'exec')
        compile(m.WORKER, '<worker>', 'exec')

    def test_http_success(self):
        self.assertEqual(m.validate_result({'kind':'http','status':200,'valid':True}),
                         {'kind':'http','status':200,'valid':True})

    def test_http_error(self):
        r=m.validate_result({'kind':'http','status':403,'valid':False})
        self.assertIn('authentication/authorization',m.explain(r))

    def test_timeout(self):
        r=m.validate_result({'kind':'transport','class':'TimeoutError'})
        self.assertIn('transport failure', m.explain(r))

    def test_reject_out_of_range_status(self):
        for val in (0, 600, -1, '200', True):
            with self.subTest(val=val):
                with self.assertRaises(m.DiagnosticError):
                    m.validate_result({'kind':'http','status':val,'valid':False})

    def test_reject_invalid_http_shape(self):
        with self.assertRaises(m.DiagnosticError):
            m.validate_result({'kind':'http','status':200,'valid':'yes'})

    def test_reject_raw_message(self):
        with self.assertRaises(m.DiagnosticError):
            m.validate_result({'kind':'unsupported'})

    def test_ignores_untrusted_http_fields(self):
        r=m.validate_result({'kind':'http','status':200,'valid':False,'body':'SENSITIVE'})
        self.assertNotIn('body',r)

    def test_reject_unknown_transport(self):
        with self.assertRaises(m.DiagnosticError):
            m.validate_result({'kind':'transport','class':'SensitiveToken'})

    def test_no_secrets_in_argv_or_environment(self):
        p=subprocess.CompletedProcess(['kubectl'],0,'{"kind":"http","status":200,"valid":true}','')
        with patch.object(m.subprocess,'run',return_value=p) as mocked:
            data={'url':'http://model.default-default:22893/foo','key':'SUPER_SECRET_DO_NOT_PRINT','model':'gpt-4.1-mini','system':'hello'}
            result=m.run_worker(FakeMod(),'ns','pod','ephem',data)
            args=mocked.call_args.args[0]
            kwargs=mocked.call_args.kwargs
            self.assertEqual(result['status'],200)
            self.assertEqual(args[-3], 'python3')
            self.assertNotIn('SUPER_SECRET_DO_NOT_PRINT',' '.join(args))
            self.assertNotIn('SUPER_SECRET_DO_NOT_PRINT',m.WORKER)
            self.assertIn('SUPER_SECRET_DO_NOT_PRINT', kwargs['input'])
            self.assertNotIn('env', kwargs)
            self.assertNotIn('shell', kwargs)
            self.assertTrue(kwargs['capture_output'])

    def test_raw_kubectl_error_sanitized(self):
        p=subprocess.CompletedProcess(['kubectl'],1,'','Error private-host SECRET_TOKEN forbidden')
        with patch.object(m.subprocess,'run',return_value=p):
            with self.assertRaises(m.DiagnosticError) as ctx:
                m.run_worker(FakeMod(),'ns','pod','ephem',{'key':'SECRET_TOKEN'})
        self.assertNotIn('SECRET_TOKEN',str(ctx.exception))
        self.assertIn('RBAC',str(ctx.exception))

    def test_oversized_input_fails_closed(self):
        with self.assertRaises(m.DiagnosticError):
            m.run_worker(FakeMod(),'ns','pod','ephem',{'secret':'x'*40000})

    def test_invalid_json_stdout_redacted(self):
        p=subprocess.CompletedProcess(['kubectl'],0,'PRIVATE_RESPONSE_TOKEN','')
        with patch.object(m.subprocess,'run',return_value=p):
            with self.assertRaises(m.DiagnosticError) as ctx:
                m.run_worker(FakeMod(),'ns','pod','ephem',{'key':'secret'})
        self.assertNotIn('PRIVATE_RESPONSE_TOKEN',str(ctx.exception))

    def test_no_runtime_writes(self):
        t=SCRIPT.read_text()
        self.assertNotIn('kubectl apply',t)
        self.assertNotIn('kubectl patch',t)
        self.assertNotIn('secret create',t)
        self.assertNotIn('deployment restart',t)
        self.assertIn("'--attach=false'", t)

    def test_header_path_and_proxy_disabled(self):
        self.assertIn("'API-Key': raw['key']",m.WORKER)
        self.assertIn('ProxyHandler({})',m.WORKER)
        self.assertIn("'hello'",m.WORKER)
        self.assertIn("'/chat/completions'",m.WORKER)
        self.assertNotIn('print(raw)',m.WORKER)

    def test_get_preflight_does_not_call_ephemeral(self):
        # Test invariant on the code path without requiring a live cluster.
        import inspect
        src=inspect.getsource(m.main)
        self.assertLess(src.index('if not args.smoke:'),src.index('create_ephemeral('))

    def test_image_error_categorization(self):
        p=subprocess.CompletedProcess(['kubectl'],1,'','ImagePullBackOff and PRIVATE_CLUSTER_URL')
        self.assertIn('image',m.safe_error(p,'Pod').lower())
        self.assertNotIn('PRIVATE_CLUSTER_URL',m.safe_error(p,'Pod'))

    def run_real_worker_locally(self, status):
        # HTTP requests/headers are tested against an in-process fake provider.
        # Production worker restricts hosts to Kubernetes Services; only the
        # local unit-test copy is allowed to reach 127.0.0.1.
        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                self.server.seen_path = self.path
                self.server.seen_header = self.headers.get('API-Key')
                self.server.seen_auth = self.headers.get('Authorization')
                payload = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                self.server.seen_user = payload['messages'][-1]['content']
                self.send_response(status)
                self.send_header('Content-Type','application/json')
                self.end_headers()
                self.wfile.write(json.dumps({'choices':[{'message':{'content':'ok'}}], 'marker':'PRIVATE_RESPONSE_BODY'}).encode())
            def log_message(self, *args):
                return
        srv = HTTPServer(('127.0.0.1',0),Handler)
        t=Thread(target=srv.serve_forever,daemon=True);t.start()
        try:
            code = m.WORKER.replace("u.hostname.endswith(('.svc.cluster.local', '.default-default'))", "u.hostname == '127.0.0.1'")
            payload={'url':f'http://127.0.0.1:{srv.server_port}/model/v1',
                     'key':'PRIVATE_GATEWAY_KEY','model':'gpt-4.1-mini', 'system':m.MODEL_PROMPT}
            proc=subprocess.run([sys.executable,'-c',code], input=json.dumps(payload),text=True,
                                capture_output=True,timeout=10)
            self.assertEqual(proc.returncode,0,proc.stderr[:300])
            self.assertNotIn('PRIVATE_GATEWAY_KEY',proc.stdout)
            self.assertNotIn('PRIVATE_RESPONSE_BODY',proc.stdout)
            self.assertEqual(srv.seen_path,'/model/v1/chat/completions')
            self.assertEqual(srv.seen_header,'PRIVATE_GATEWAY_KEY')
            self.assertEqual(srv.seen_user,'hello')
            return json.loads(proc.stdout)
        finally:
            srv.shutdown();srv.server_close();t.join(timeout=2)

    def test_worker_real_http_200(self):
        self.assertEqual(self.run_real_worker_locally(200),{'kind':'http','status':200,'valid':True})

    def test_worker_real_http_403_no_response_leak(self):
        self.assertEqual(self.run_real_worker_locally(403),{'kind':'http','status':403,'valid':False})

    def test_pod_transport_status_group(self):
        self.assertIn('gateway/upstream failed',m.explain({'kind':'http','status':503,'valid':False}))
        self.assertIn('pod network',m.explain({'kind':'http','status':200,'valid':True}))

if __name__=='__main__':unittest.main()
