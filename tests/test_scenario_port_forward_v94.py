"""v9.4: no fixed client-side 8300, no SSH tunnel termination, strict E2E."""
import ast
import importlib.util
import json
import pathlib
import subprocess
import tempfile
import types
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('aurora_v94', ROOT/'scripts/aurora-full-reconcile.py')
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


class FakeForwardProcess:
    def __init__(self, *args, **kwargs):
        self.args = args[0]
        self.kwargs = kwargs
        self.terminated = False
        self.killed = False
        self.waited = False
        self.status = None
        kwargs['stdout'].write(b'Forwarding from 127.0.0.1:44123 -> 8300\n')
        kwargs['stdout'].flush()

    def poll(self):
        return self.status

    def terminate(self):
        self.terminated = True
        self.status = -15

    def kill(self):
        self.killed = True
        self.status = -9

    def wait(self, timeout=None):
        self.waited = True
        return self.status


class OkResponse:
    status = 200
    def __enter__(self):
        return self
    def __exit__(self, *exc):
        pass


class ScenarioForwardV94Tests(unittest.TestCase):
    def test_parser_valid_ipv4_loopback_only(self):
        b = b'Forwarding from 127.0.0.1:44123 -> 8300\n'
        self.assertEqual(m.portal_forward_local_port(b), 44123)
        self.assertEqual(m.portal_forward_local_port(b'Forwarding from 0.0.0.0:44123 -> 8300\n'), None)
        self.assertEqual(m.portal_forward_local_port(b'Forwarding from 127.0.0.1:44123 -> 8100\n'), None)
        self.assertEqual(m.portal_forward_local_port(b'Handling connection for 44123\n'), None)

    def test_invalid_and_conflicting_forward_ports_are_rejected(self):
        self.assertIsNone(m.portal_forward_local_port(b'Forwarding from 127.0.0.1:0 -> 8300\n'))
        self.assertIsNone(m.portal_forward_local_port(b'Forwarding from 127.0.0.1:65536 -> 8300\n'))
        with self.assertRaisesRegex(RuntimeError, 'conflicting'):
            m.portal_forward_local_port(b'Forwarding from 127.0.0.1:44123 -> 8300\n'
                                        b'Forwarding from 127.0.0.1:44567 -> 8300\n')
        with self.assertRaisesRegex(RuntimeError, 'encoding'):
            m.portal_forward_local_port('Forwarding from 127.0.0.1:44123 -> 8300')

    def test_old_log_line_is_not_adopted(self):
        with tempfile.TemporaryDirectory() as td:
            logfile = pathlib.Path(td)/'portal-forward.log'
            logfile.write_bytes(b'Forwarding from 127.0.0.1:8300 -> 8300\n')
            marker = logfile.stat().st_size
            with logfile.open('ab') as out:
                out.write(b'Forwarding from 127.0.0.1:49301 -> 8300\n')
            proc = mock.Mock()
            proc.poll.return_value = None
            self.assertEqual(m.await_portal_forward(proc, logfile, marker, timeout=1), 49301)

    def test_immediately_exited_subprocess_never_adopted(self):
        with tempfile.TemporaryDirectory() as td:
            logfile = pathlib.Path(td)/'portal-forward.log'
            logfile.write_bytes(b'Forwarding from 127.0.0.1:44123 -> 8300\n')
            proc = mock.Mock()
            proc.poll.return_value = 1
            with self.assertRaisesRegex(RuntimeError, 'exited unexpectedly'):
                m.await_portal_forward(proc, logfile, 0)

    def test_port_forward_wait_is_bounded(self):
        with tempfile.TemporaryDirectory() as td:
            logfile = pathlib.Path(td)/'portal-forward.log'
            logfile.write_bytes(b'')
            proc = mock.Mock()
            proc.poll.return_value = None
            with mock.patch.object(m.time,'monotonic',side_effect=[0,31]):
                with self.assertRaisesRegex(RuntimeError,'within 30 seconds'):
                    m.await_portal_forward(proc, logfile, 0)

    def run_fake_scenario(self, *, fail_scenarios=False, fail_readiness=False):
        with tempfile.TemporaryDirectory() as td:
            support = pathlib.Path(td)
            e2e_bin = support/'.venv-e2e'/'bin'/'python'
            e2e_bin.parent.mkdir(parents=True)
            e2e_bin.write_text('')
            (support/'secrets.env').write_text('EXAMPLE=redacted\n')
            procs = []
            cli_calls = []
            opens = []
            def fake_popen(*args, **kwargs):
                proc = FakeForwardProcess(*args, **kwargs)
                procs.append(proc)
                return proc
            def fake_cmd(args, **kwargs):
                cli_calls.append(args)
                if 'scripts/run-executive-scenarios.py' in args:
                    self.assertEqual(args[args.index('--portal')+1], 'http://127.0.0.1:44123')
                    if fail_scenarios:
                        raise RuntimeError('E2E failed')
                    (support/'executive-scenario-report.json').write_text(json.dumps({
                        'scenarios':[{'scenario':f'S0{x}','passed':True} for x in range(1,7)]}))
                return types.SimpleNamespace(returncode=0)
            def fake_urlopen(url, timeout=0):
                opens.append(url)
                if fail_readiness:
                    raise OSError('not ready')
                return OkResponse()
            with mock.patch.object(m, 'SUPPORT', support), \
                 mock.patch.object(m, 'cmd', side_effect=fake_cmd), \
                 mock.patch.object(m.subprocess, 'Popen', side_effect=fake_popen), \
                 mock.patch('urllib.request.urlopen', side_effect=fake_urlopen), \
                 mock.patch.object(m.time, 'sleep'):
                if fail_scenarios:
                    with self.assertRaisesRegex(RuntimeError,'E2E failed'):
                        m.verify_scenarios()
                elif fail_readiness:
                    with self.assertRaisesRegex(RuntimeError,'did not become reachable'):
                        m.verify_scenarios()
                else:
                    m.verify_scenarios()
            self.assertEqual(len(procs), 1)
            proc = procs[0]
            self.assertEqual(proc.args[proc.args.index('port-forward')+2], ':8300')
            self.assertEqual(proc.args[-1], '127.0.0.1')
            self.assertNotIn('8300:8300', proc.args)
            self.assertTrue(proc.terminated and proc.waited)
            self.assertFalse(proc.killed)
            self.assertTrue(all('127.0.0.1:44123/' in x for x in opens))
            self.assertNotIn('8300', ' '.join(opens))
            return cli_calls

    def test_success_uses_owned_random_port_not_ssh_8300(self):
        cli_calls = self.run_fake_scenario()
        self.assertTrue(any('scripts/run-executive-scenarios.py' in call for call in cli_calls))

    def test_scenario_error_still_terminates_our_own_forward(self):
        self.run_fake_scenario(fail_scenarios=True)

    def test_portal_not_ready_still_terminates_our_own_forward(self):
        self.run_fake_scenario(fail_readiness=True)

    def test_python311_grammar_compatible(self):
        ast.parse((ROOT/'scripts/aurora-full-reconcile.py').read_text(), feature_version=(3,11))


if __name__ == '__main__':
    unittest.main()
