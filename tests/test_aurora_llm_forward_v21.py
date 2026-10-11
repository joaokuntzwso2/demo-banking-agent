"""Regression tests for stdout-based kubectl port-forward readiness."""
import importlib.util
from pathlib import Path
import subprocess
import sys
import unittest
from unittest import mock

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts' / 'diagnose-aurora-llm-http.py'
spec = importlib.util.spec_from_file_location('aurora_llm_http_v21', SCRIPT)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
ORIGINAL_POPEN = subprocess.Popen

class FakeModule:
    CTX = 'k3d-amp-local'


def simulated_kubectl(code, observed=None):
    """Provide a local Python subprocess in place of kubectl for isolated tests."""
    def launch(args, **kw):
        if observed is not None:
            observed.append((list(args), dict(kw)))
        return ORIGINAL_POPEN([sys.executable, '-u', '-c', code], **kw)
    return launch


class ForwardReadinessTests(unittest.TestCase):
    def test_compiles_under_python311_grammar(self):
        compile(SCRIPT.read_text(), str(SCRIPT), 'exec')

    def test_stdout_forward_ready_is_recognized(self):
        script = 'import sys,time; sys.stdout.write("Forwarding from 127.0.0.1:60566 -> 22893\\n");sys.stdout.flush();time.sleep(5)'
        observed=[]
        with mock.patch.object(m.subprocess, 'Popen', side_effect=simulated_kubectl(script,observed)):
            proc, port = m.start_forward(FakeModule(), 'model-runtime', 'default-default', 22893)
            try:
                self.assertEqual(port, 60566)
                self.assertIsNone(proc.poll())
            finally:
                m.stop_forward(proc)
        args, kwargs = observed[0]
        self.assertIn('service/model-runtime',args)
        self.assertIn(':22893',args)
        self.assertIn('127.0.0.1',args)
        self.assertIs(kwargs['stdout'], subprocess.PIPE)
        self.assertIs(kwargs['stderr'], subprocess.STDOUT)
        self.assertIs(kwargs['stdin'], subprocess.DEVNULL)

    def test_stderr_forward_ready_is_also_recognized(self):
        script = 'import sys,time;sys.stderr.write("Forwarding from 127.0.0.1:44109 -> 22893\\n");sys.stderr.flush();time.sleep(5)'
        with mock.patch.object(m.subprocess, 'Popen', side_effect=simulated_kubectl(script)):
            proc, port = m.start_forward(FakeModule(), 'runtime', 'default-default', 22893)
            try:
                self.assertEqual(port,44109)
            finally:
                m.stop_forward(proc)

    def test_denied_port_forward_sanitizes_error(self):
        script = 'import sys; sys.stderr.write("Error from server (Forbidden): FOR_PRIVATE_CLUSTER_SECRET\\n");sys.exit(1)'
        with mock.patch.object(m.subprocess, 'Popen', side_effect=simulated_kubectl(script)):
            with self.assertRaises(m.CheckError) as ctx:
                m.start_forward(FakeModule(), 'runtime', 'ns', 22893)
        self.assertIn('authorization denied',str(ctx.exception))
        self.assertNotIn('FOR_PRIVATE_CLUSTER_SECRET',str(ctx.exception))

    def test_missing_target_sanitized(self):
        script = 'import sys;sys.stderr.write("Error from server (NotFound): services SECRET_ENDPOINT not found\\n");sys.exit(1)'
        with mock.patch.object(m.subprocess, 'Popen', side_effect=simulated_kubectl(script)):
            with self.assertRaises(m.CheckError) as ctx:
                m.start_forward(FakeModule(),'runtime','ns',22893)
        self.assertIn('target missing',str(ctx.exception))
        self.assertNotIn('SECRET_ENDPOINT',str(ctx.exception))

    def test_listener_bind_problem(self):
        script = 'import sys;sys.stderr.write("Unable to listen on any of the requested ports\\n");sys.exit(1)'
        with mock.patch.object(m.subprocess, 'Popen', side_effect=simulated_kubectl(script)):
            with self.assertRaises(m.CheckError) as ctx:
                m.start_forward(FakeModule(),'runtime','ns',22893)
        self.assertIn('listener could not bind',str(ctx.exception))

    def test_no_readiness_marker(self):
        script = 'import sys;sys.stdout.write("port-forward status: starting then failed\\n");sys.exit(1)'
        with mock.patch.object(m.subprocess, 'Popen', side_effect=simulated_kubectl(script)):
            with self.assertRaises(m.CheckError) as ctx:
                m.start_forward(FakeModule(),'runtime','ns',22893)
        self.assertIn('No forwarding confirmation',str(ctx.exception))

    def test_never_print_raw_process_output(self):
        script = 'import sys;sys.stderr.write("Forbidden private-gateway-service FAKETOKEN\\n");sys.exit(1)'
        import io
        from contextlib import redirect_stdout, redirect_stderr
        capture=io.StringIO()
        with mock.patch.object(m.subprocess, 'Popen', side_effect=simulated_kubectl(script)):
            with redirect_stdout(capture), redirect_stderr(capture):
                with self.assertRaises(m.CheckError):
                    m.start_forward(FakeModule(),'runtime','ns',22893)
        self.assertNotIn('FAKETOKEN',capture.getvalue())

    def test_shell_is_not_used(self):
        script = 'import sys,time;sys.stdout.write("Forwarding from 127.0.0.1:54682 -> 22893\\n");sys.stdout.flush();time.sleep(5)'
        args=[]
        with mock.patch.object(m.subprocess, 'Popen', side_effect=simulated_kubectl(script,args)):
            proc, port=m.start_forward(FakeModule(),'model-runtime','default-default',22893)
            m.stop_forward(proc)
        self.assertNotIn('shell',args[0][1])
        self.assertNotIn('API-Key', ' '.join(args[0][0]))

if __name__=='__main__':unittest.main()
