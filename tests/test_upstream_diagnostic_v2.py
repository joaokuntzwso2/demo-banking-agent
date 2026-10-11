"""Tests for read-only log classifier; Python 3.11 compatible."""
import ast
import importlib.util
from pathlib import Path
from unittest import TestCase, mock

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('chat_upstream', ROOT/'scripts/diagnose-aurora-upstream.py')
d = importlib.util.module_from_spec(spec)
spec.loader.exec_module(d)

class UpstreamDiagnosticTests(TestCase):
    def test_python311_syntax(self):
        ast.parse((ROOT/'scripts/diagnose-aurora-upstream.py').read_text(), feature_version=(3, 11))

    def test_only_exception_types_and_allowlisted_locations(self):
        secret='sensitive-credential-987654321'
        sample = ('INFO request user C001 '+secret+'\nTraceback (most recent call last):\n'
            '  File "/app/agents/concierge.py", line 129, in concierge\n'
            '  File "/app/private/'+secret+'", line 10, in other\n'
            'httpx.ConnectError: secret='+secret+'\n')
        result = d.sanitize_traceback(sample)
        self.assertEqual(result['exception_types']['ConnectError'],1)
        self.assertEqual(result['source_locations'], [('agents/concierge.py','129')])
        self.assertNotIn(secret, str(result))

    def test_logs_do_not_expose_error_message_or_customer_url(self):
        sample='httpx.HTTPStatusError: 500 on http://secret-host/workflows/123?customer_id=C001'
        result=d.sanitize_traceback(sample)
        self.assertEqual(result['exception_types']['HTTPStatusError'],1)
        self.assertNotIn('http://secret-host',str(result))
        self.assertNotIn('C001',str(result))

    def test_read_only_command_sources(self):
        source=(ROOT/'scripts/diagnose-aurora-upstream.py').read_text()
        for term in ("'delete'", "'patch'", "'apply'", "'create'", "'exec'", "'secrets.env'", "print(content)", "print(result.stderr)"):
            self.assertNotIn(term,source)
        self.assertIn("'get', 'pods'",source)
        self.assertIn("'logs', 'pod/'",source)

    def test_choose_latest_matching_running_pods(self):
        def pod(name, stamp, phase='Running', restarts=0):
            return {'metadata':{'name':name,'creationTimestamp':stamp},
                    'status':{'phase':phase,'containerStatuses':[{'name':'main','restartCount':restarts}]},
                    'spec':{'containers':[{'name':'main'}]}}
        matches=d.choose_pods([pod('aurora-concierge-a','2026-10-10T18:00:00Z'),
            pod('aurora-concierge-b','2026-10-10T18:30:00Z',restarts=1),
            pod('aurora-concierge-c','2026-10-10T18:45:00Z',phase='Succeeded'),
            pod('aurora-dispute-abc','2026-10-10T18:59:00Z')], 'aurora-concierge-')
        self.assertEqual(matches,[('aurora-concierge-b','main',1),('aurora-concierge-a','main',0)])

    def test_error_from_inventory_is_sanitized(self):
        with mock.patch.object(d,'command', return_value=mock.Mock(returncode=1,stderr='very secret')):
            with self.assertRaisesRegex(RuntimeError, 'Cannot read Kubernetes') as ctx:
                d.inventory('aurora-demo')
            self.assertNotIn('very secret',str(ctx.exception))

    def test_previous_logs_only_if_restart(self):
        with mock.patch.object(d,'command',return_value=mock.Mock(returncode=0,stdout='')) as c:
            d.logs_for('aurora-demo','aurora-portal-abc','main','5m',previous=True)
            self.assertIn('--previous',c.call_args.args[0])

    def test_no_secrets_in_subprocess_args(self):
        with mock.patch.object(d,'command',return_value=mock.Mock(returncode=0,stdout='')) as c:
            d.logs_for('aurora-demo','aurora-portal-abc','main','5m')
            all_args=' '.join(c.call_args.args[0])
            self.assertNotIn('API_KEY',all_args)
            self.assertNotIn('CONCIERGE_URL',all_args)
