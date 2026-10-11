"""Safety-focused local tests for narrow Core egress policy."""
import importlib.util
import pathlib
import types
import unittest
from unittest.mock import patch

ROOT = pathlib.Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('aurora_egress', ROOT / 'scripts' / 'diagnose-aurora-core-egress.py')
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


def pod(name='aurora-concierge-xyz', ns='dp-default-banco-aurora-default-990774f5',
        labels=None, ip='10.42.0.11'):
    return {'metadata': {'name': name, 'namespace': ns, 'labels': labels if labels is not None else {'app.kubernetes.io/name': 'aurora-concierge'}},
            'status': {'phase': 'Running', 'podIP': ip, 'containerStatuses': [{'name': 'main', 'ready': True}]}}


def pol(name='default-deny', selector=None, egress=None):
    return {'metadata': {'name': name}, 'spec': {'podSelector': selector if selector is not None else {},
            'policyTypes': ['Egress'], 'egress': egress if egress is not None else []}}


class CoreEgressTests(unittest.TestCase):
    def test_python311_compile(self):
        compile((ROOT / 'scripts' / 'diagnose-aurora-core-egress.py').read_text(), 'file', 'exec')

    def test_selector_matching(self):
        self.assertTrue(m.match_selector({'matchLabels': {'app': 'x'}}, {'app': 'x'}))
        self.assertFalse(m.match_selector({'matchLabels': {'app': 'x'}}, {'app': 'y'}))
        self.assertTrue(m.match_selector({'matchExpressions': [{'key': 'env', 'operator': 'In', 'values': ['a', 'b']}]}, {'env': 'a'}))
        self.assertTrue(m.match_selector({'matchExpressions': [{'key': 'abc', 'operator': 'NotIn', 'values': ['b']}]}, {'env': 'a'}))
        self.assertTrue(m.match_selector({'matchExpressions': [{'key': 'env', 'operator': 'Exists'}]}, {'env': 'a'}))
        self.assertTrue(m.match_selector({'matchExpressions': [{'key': 'env', 'operator': 'DoesNotExist'}]}, {}))
        self.assertFalse(m.match_selector({'matchExpressions': [{'key': 'env', 'operator': 'DoesNotExist'}]}, {'env': 'a'}))

    def test_unique_stable_selector(self):
        p = pod()
        self.assertEqual(m.choose_concierge_selector(p, [p, pod(name='aurora-account-xyz', labels={'app.kubernetes.io/name': 'aurora-account'})]),
                         {'app.kubernetes.io/name': 'aurora-concierge'})

    def test_reject_namespace_wide_selector(self):
        p = pod(labels={'team': 'banking'})
        with self.assertRaisesRegex(RuntimeError, 'unique stable'):
            m.choose_concierge_selector(p, [p])

    def test_reject_shared_selector(self):
        p = pod()
        p2 = pod(name='aurora-accounts-123')
        with self.assertRaisesRegex(RuntimeError, 'unique stable'):
            m.choose_concierge_selector(p, [p, p2])

    def test_denied_by_default_isolation(self):
        status, findings = m.classify([pol()], {'app': 'concierge'}, {'kubernetes.io/metadata.name': 'aurora-demo'}, {'app': 'core'}, '10.42.0.25')
        self.assertEqual(status, 'denied')
        self.assertEqual(findings, [('default-deny', False)])

    def test_nonselecting_policy_unrestricted(self):
        status, _ = m.classify([pol(selector={'matchLabels': {'app': 'other'}})], {'app': 'concierge'}, {}, {}, '10.42.0.25')
        self.assertEqual(status, 'unrestricted')

    def test_narrow_policy_permits(self):
        p = m.plan_policy('dp-test', {'app.kubernetes.io/name': 'aurora-concierge'}, {'app': 'core'})
        core_ns = {'kubernetes.io/metadata.name': 'aurora-demo'}
        status, findings = m.classify([pol(), p], {'app.kubernetes.io/name': 'aurora-concierge'}, core_ns, {'app': 'core'}, '10.42.0.25')
        self.assertEqual(status, 'allowed')
        self.assertEqual(findings[1][1], True)
        self.assertEqual(p['spec']['policyTypes'], ['Egress'])
        self.assertEqual(p['spec']['egress'][0]['ports'], [{'protocol': 'TCP', 'port': 8100}])
        self.assertEqual(len(p['spec']['egress'][0]['to']), 1)
        self.assertNotIn('ingress', p['spec'])

    def test_source_selector_never_empty(self):
        p = m.plan_policy('dp-test', {'app': 'aurora-concierge'}, {'app': 'core'})
        self.assertTrue(p['spec']['podSelector']['matchLabels'])
        self.assertTrue(p['spec']['egress'][0]['to'][0]['podSelector']['matchLabels'])

    def test_ipblock_matching_pod_is_uncertain(self):
        p = pol(egress=[{'to': [{'ipBlock': {'cidr': '10.42.0.0/16'}}], 'ports': [{'port': 8100}]}])
        status, _ = m.classify([p], {}, {}, {}, '10.42.0.25')
        self.assertEqual(status, 'uncertain')

    def test_ipblock_not_matching_pod_is_denied(self):
        p = pol(egress=[{'to': [{'ipBlock': {'cidr': '192.168.0.0/16'}}], 'ports': [{'port': 8100}]}])
        status, _ = m.classify([p], {}, {}, {}, '10.42.0.25')
        self.assertEqual(status, 'denied')

    def test_podselector_alone_not_cross_namespace(self):
        self.assertFalse(m.peer_match({'podSelector': {'matchLabels': {'app': 'core'}}}, {}, {'app': 'core'}, '10.42.0.25'))

    def test_named_port_uncertain(self):
        self.assertIsNone(m.port_match([{'port': 'http'}]))

    def test_no_changes_without_apply(self):
        plan = m.plan_policy('dp-test', {'app': 'aurora-concierge'}, {'app': 'core'})
        with patch.object(m, 'audit', return_value=('denied', [('default-deny', False)], plan, [pol()])), patch.object(m, 'run') as run:
            self.assertEqual(m.main([]), 0)
            run.assert_not_called()

    def test_no_changes_if_already_allowed(self):
        plan = m.plan_policy('dp-test', {'app': 'aurora-concierge'}, {'app': 'core'})
        with patch.object(m, 'audit', return_value=('allowed', [('allow', True)], plan, [pol()])), patch.object(m, 'run') as run:
            self.assertEqual(m.main(['--apply']), 0)
            run.assert_not_called()

    def test_no_changes_if_uncertain(self):
        plan = m.plan_policy('dp-test', {'app': 'aurora-concierge'}, {'app': 'core'})
        with patch.object(m, 'audit', return_value=('uncertain', [('ipblock', None)], plan, [pol()])), patch.object(m, 'run') as run:
            self.assertEqual(m.main(['--apply']), 2)
            run.assert_not_called()

    def test_refuses_overwriting_policy(self):
        plan = m.plan_policy('dp-test', {'app': 'aurora-concierge'}, {'app': 'core'})
        existing = pol(name=m.POLICY_NAME)
        with patch.object(m, 'audit', return_value=('denied', [('deny', False)], plan, [existing])), patch.object(m, 'run') as run:
            self.assertEqual(m.main(['--apply']), 1)
            run.assert_not_called()

    def test_server_dryrun_then_create(self):
        plan = m.plan_policy('dp-test', {'app': 'aurora-concierge'}, {'app': 'core'})
        with patch.object(m, 'audit', return_value=('denied', [('deny', False)], plan, [pol()])), patch.object(m, 'run', side_effect=[types.SimpleNamespace(returncode=0), types.SimpleNamespace(returncode=0)]) as run:
            self.assertEqual(m.main(['--apply']), 0)
            self.assertEqual(run.call_count, 2)
            self.assertEqual(run.call_args_list[0].args[0][:3], ['create', '--dry-run=server', '-f'])
            self.assertEqual(run.call_args_list[1].args[0][:2], ['create', '-f'])

    def test_dryrun_failure_prevents_create(self):
        plan = m.plan_policy('dp-test', {'app': 'aurora-concierge'}, {'app': 'core'})
        with patch.object(m, 'audit', return_value=('denied', [('deny', False)], plan, [pol()])), patch.object(m, 'run', return_value=types.SimpleNamespace(returncode=1)) as run:
            self.assertEqual(m.main(['--apply']), 1)
            run.assert_called_once()

    def test_no_credential_access_or_workload_mutation(self):
        code = (ROOT / 'scripts' / 'diagnose-aurora-core-egress.py').read_text()
        self.assertNotIn('get secrets', code)
        self.assertNotIn("'deploy'", code)
        self.assertNotIn("'delete'", code)
        self.assertNotIn('envFrom', code)


if __name__ == '__main__':
    unittest.main()
