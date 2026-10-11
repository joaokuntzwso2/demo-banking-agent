"""WSO2 AMP 1.0.0 build/config race: production contract-driven regressions.

These tests use the real captured build object fields (buildParameters and
status) and assert the orchestration does not issue speculative POST/PUT.
"""
import importlib.util
from pathlib import Path
import subprocess
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('aurora_build_lock_v4',ROOT/'scripts/aurora-full-reconcile.py')
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
SHA = '634794269c3f' + 'a' * 28
WRONG_SHA = 'b' * 40


def existing_build(status='Building', name='aurora-dispute', sha=SHA):
    return {'agentName': name, 'buildId': 'job-1', 'status': status,
            'buildParameters': {'commitId': sha,
                                'repoUrl': m.REPO,
                                'branch': m.REF,
                                'appPath': '/deployment/agent-manager/apps/' + name}}


class Clock:
    def __init__(self):
        self.now = 0
    def monotonic(self):
        return self.now
    def sleep(self, seconds):
        self.now += seconds


class BuildLockCases(unittest.TestCase):
    def setUp(self):
        # Keep unit tests instantaneous while production defaults to a small
        # lock-release delay after a verified conflicting build completes.
        self._settle = mock.patch.object(m,'BUILD_LOCK_RETRY_DELAY',0)
        self._settle.start()
        self.addCleanup(self._settle.stop)

    def test_all_specialists_build_before_llm_mcp_writes_and_deploy(self):
        events = []
        def note(label, ret=None):
            def inner(*args, **kwargs):
                events.append((label, args[0] if args else None))
                return ret
            return inner
        with mock.patch.object(m,'verify_proxy'), mock.patch.object(m,'get_git_commit',return_value=SHA), \
             mock.patch.object(m,'ensure_project'),mock.patch.object(m,'ensure_llm_provider'), \
             mock.patch.object(m,'service_secrets',return_value={}), \
             mock.patch.object(m,'ensure_agent',side_effect=note('create',True)), \
             mock.patch.object(m,'build_agent',side_effect=note('build')), \
             mock.patch.object(m,'bind_llm',side_effect=note('llm')), \
             mock.patch.object(m,'bind_tool',side_effect=note('mcp')), \
             mock.patch.object(m,'wait_for_existing_build',side_effect=note('readiness',True)), \
             mock.patch.object(m,'deploy_agent',side_effect=note('deploy')), \
             mock.patch.object(m,'provision_gateway_key',side_effect=note('key')), \
             mock.patch.object(m,'ensure_identity_roles'), \
             mock.patch.object(m,'verify_concierge_managed_secrets',side_effect=note('concierge-secrets')), \
             mock.patch.object(m,'agent_json',return_value={
                 'config': {f'AGENT_{r.upper()}_API_KEY': 'secret-reference' for r in m.ROLES}
             }), mock.patch.object(m,'resolve_invoke_url',return_value='https://example.test'), \
             mock.patch.object(m,'configure_portal'):
            m.managed_agents()
        for role in m.ROLES:
            name = 'aurora-' + role
            seq = [label for label, agent in events if agent == name]
            self.assertEqual(seq,['create','build','llm','mcp','readiness','deploy','key'],name)
        seq = [label for label, agent in events if agent == 'aurora-concierge']
        self.assertEqual(seq,['create','build','llm','readiness','deploy'])
        self.assertEqual([tag for tag,_ in events].count('concierge-secrets'),1)

    def test_predeployment_missing_build_is_fail_closed(self):
        with mock.patch.object(m,'verify_proxy'),mock.patch.object(m,'get_git_commit',return_value=SHA), \
             mock.patch.object(m,'ensure_project'),mock.patch.object(m,'ensure_llm_provider'), \
             mock.patch.object(m,'service_secrets',return_value={}), \
             mock.patch.object(m,'ensure_agent',return_value=False), \
             mock.patch.object(m,'build_agent'), mock.patch.object(m,'bind_llm'), \
             mock.patch.object(m,'bind_tool'), \
             mock.patch.object(m,'wait_for_existing_build',return_value=False), \
             mock.patch.object(m,'deploy_agent') as deploy, \
             mock.patch.object(m,'provision_gateway_key') as keys:
            with self.assertRaisesRegex(RuntimeError,'cannot confirm remote managed build before deployment'):
                m.managed_agents()
        deploy.assert_not_called()
        keys.assert_not_called()

    def test_existing_auto_build_is_reused_and_not_recreated(self):
        b = existing_build('Completed')
        with mock.patch.object(m,'latest_build',side_effect=[None,b]), \
             mock.patch.object(m,'verified_build_revision',return_value=SHA), \
             mock.patch.object(m,'state_get',return_value={}), \
             mock.patch.object(m,'state_save'), \
             mock.patch.object(m,'cmd') as cli, \
             mock.patch.object(m.time,'sleep'):
            m.build_agent('aurora-dispute',SHA,force=True)
            cli.assert_not_called()

    def test_auto_build_queued_waits_and_completes_without_create(self):
        queued=existing_build('Queued')
        completed=existing_build('Completed')
        clock=Clock()
        with mock.patch.object(m,'latest_build',side_effect=[None,queued,completed]), \
             mock.patch.object(m,'state_get',return_value={}), \
             mock.patch.object(m,'state_save') as saved, \
             mock.patch.object(m,'cmd') as cli, \
             mock.patch.object(m.time,'monotonic',side_effect=clock.monotonic), \
             mock.patch.object(m.time,'sleep',side_effect=clock.sleep):
            m.build_agent('aurora-dispute',SHA,force=True)
            cli.assert_not_called()
            saved.assert_called_once()

    def test_no_automatic_build_after_grace_expiration_creates_once(self):
        clock=Clock()
        completed=existing_build('Completed')
        with mock.patch.object(m,'BUILD_DISCOVERY_TIMEOUT',3), \
             mock.patch.object(m,'BUILD_DISCOVERY_INTERVAL',1), \
             mock.patch.object(m,'latest_build',side_effect=[None,None,None,None,completed]), \
             mock.patch.object(m,'state_get',return_value={}), \
             mock.patch.object(m,'state_save'), \
             mock.patch.object(m,'cmd') as cli, \
             mock.patch.object(m.time,'monotonic',side_effect=clock.monotonic), \
             mock.patch.object(m.time,'sleep',side_effect=clock.sleep):
            m.build_agent('aurora-dispute',SHA,force=True)
            self.assertEqual(cli.call_count,1)
            self.assertEqual(cli.call_args.args[0][:4],['amctl','agent','build','create'])

    def test_build_create_rejected_due_to_existing_build_recovers_without_retry(self):
        clock=Clock()
        conflict=RuntimeError('Agent Manager POST /x: amctl: HTTP 409 '
                              '{"code":"CONFLICT","message":"A build is already in progress for this agent; try again once it finishes"}')
        with mock.patch.object(m,'BUILD_DISCOVERY_TIMEOUT',0), \
             mock.patch.object(m,'latest_build',side_effect=[None,existing_build('Queued'),existing_build('Completed')]), \
             mock.patch.object(m,'state_get',return_value={}), \
             mock.patch.object(m,'state_save'), \
             mock.patch.object(m,'cmd',side_effect=conflict) as cli, \
             mock.patch.object(m.time,'monotonic',side_effect=clock.monotonic), \
             mock.patch.object(m.time,'sleep',side_effect=clock.sleep):
            m.build_agent('aurora-dispute',SHA,force=True)
            self.assertEqual(cli.call_count,1)

    def test_build_create_conflict_with_wrong_sha_refuses_write(self):
        conflict=RuntimeError('HTTP 409: A build is already in progress for this agent')
        with mock.patch.object(m,'BUILD_DISCOVERY_TIMEOUT',0), \
             mock.patch.object(m,'latest_build',side_effect=[None, existing_build(sha=WRONG_SHA)]), \
             mock.patch.object(m,'state_get',return_value={}), \
             mock.patch.object(m,'cmd',side_effect=conflict) as cli:
            with self.assertRaisesRegex(RuntimeError,'unverified or different Git revision'):
                m.build_agent('aurora-dispute',SHA,force=True)
            self.assertEqual(cli.call_count,1)

    def test_config_409_with_queued_matching_build_retries_after_readback(self):
        conflict=RuntimeError('Agent Manager POST /orgs/x: amctl: HTTP 409 '
                              '{"code":"CONFLICT","message":"A build is already in progress for this agent; try again once it finishes"}')
        events=[]
        def operation():
            events.append('re-read-collection')
            if len(events)==1:
                raise conflict
            return 'verified'
        with mock.patch.object(m,'latest_build',side_effect=[existing_build('Queued'),existing_build('Completed')]), \
             mock.patch.object(m,'state_get',return_value={}), \
             mock.patch.object(m,'state_save'), \
             mock.patch.object(m,'cmd') as cli, \
             mock.patch.object(m.time,'sleep'):
            self.assertEqual(m.reconcile_with_build_lock('aurora-dispute',SHA,'MCP',operation=operation),'verified')
            self.assertEqual(events,['re-read-collection','re-read-collection'])
            cli.assert_not_called()

    def test_concurrent_config_writer_already_created_config_no_duplicate_post(self):
        conflict=RuntimeError('HTTP 409 {"message":"A build is already in progress for this agent"}')
        # First invocation attempts a new config. On next read, a concurrent
        # writer has created it. The recovery only verifies it, not posts again.
        reads=[{'configs':[]},{'configs':[]},None,None,
               {'configs':[]},{'configs':[]},None,None]
        # Route probing in the real bind_tool is covered separately; here the
        # contract is that every retry invokes the read/compare operation anew.
        calls=[]
        def reconcile():
            calls.append('read')
            if len(calls)==1: raise conflict
            calls.append('verified-existing-record')
        with mock.patch.object(m,'latest_build',return_value=existing_build('Completed')), \
             mock.patch.object(m,'state_get',return_value={}), \
             mock.patch.object(m,'state_save'), \
             mock.patch.object(m,'cmd') as cli:
            m.reconcile_with_build_lock('aurora-dispute',SHA,'MCP',operation=reconcile)
        self.assertEqual(calls,['read','read','verified-existing-record'])
        cli.assert_not_called()

    def test_actual_mcp_post_409_concurrent_creation_not_duplicated(self):
        conflict=RuntimeError('Agent Manager POST /mcp-configs: amctl: HTTP 409 '
                              '{"code":"CONFLICT","message":"A build is already in progress for this agent; try again once it finishes"}')
        attempted=[]
        posts=[]
        def fake_api(path, method='GET', data=None, allow_404=False):
            if not path.endswith('/mcp-configs') and '/mcp-configs/' not in path:
                return None
            if path.endswith('/mcp-configs') and method=='GET':
                attempted.append('collection-get')
                if len([a for a in attempted if a=='collection-get'])==1:
                    return {'configs':[]}
                return {'configs':[{'uuid':'mcp-1','type':'mcp','name':m.PROXY}]}
            if path.endswith('/mcp-configs') and method=='POST':
                posts.append(data)
                raise conflict
            if path.endswith('/mcp-configs/mcp-1') and method=='GET':
                return {'uuid':'mcp-1','type':'mcp','name':m.PROXY,
                        'environmentVariables':m.mcp_binding_payload()['environmentVariables']}
            raise AssertionError((path,method))
        # bind_tool uses four route probes per reconciliation attempt, so
        # the second GET must see the concurrent writer's newly-added config.
        with mock.patch.object(m,'api',side_effect=fake_api), \
             mock.patch.object(m,'latest_build',return_value=existing_build('Completed')), \
             mock.patch.object(m,'state_get',return_value={}), \
             mock.patch.object(m,'state_save'), \
             mock.patch.object(m,'cmd') as cli:
            m.reconcile_with_build_lock('aurora-dispute',SHA,'MCP',
                                        operation=lambda: m.bind_tool('aurora-dispute'))
        self.assertEqual(len(posts),1)
        cli.assert_not_called()

    def test_actual_mcp_post_409_followed_by_single_safe_create(self):
        conflict=RuntimeError('Agent Manager POST /mcp-configs: amctl: HTTP 409 '
                              '{"code":"CONFLICT","message":"A build is already in progress for this agent"}')
        posts=[]
        present=False
        def fake_api(path, method='GET', data=None, allow_404=False):
            nonlocal present
            if not path.endswith('/mcp-configs') and '/mcp-configs/' not in path:
                return None
            if path.endswith('/mcp-configs') and method=='GET':
                if present:
                    return {'configs':[{'uuid':'mcp-1','type':'mcp','name':m.PROXY}]}
                return {'configs':[]}
            if path.endswith('/mcp-configs') and method=='POST':
                posts.append(data)
                if len(posts)==1: raise conflict
                present=True
                return {'uuid':'mcp-1'}
            if path.endswith('/mcp-configs/mcp-1') and method=='GET':
                return {'uuid':'mcp-1','type':'mcp','name':m.PROXY,
                        'environmentVariables':m.mcp_binding_payload()['environmentVariables']}
            raise AssertionError((path,method))
        with mock.patch.object(m,'api',side_effect=fake_api), \
             mock.patch.object(m,'latest_build',return_value=existing_build('Completed')), \
             mock.patch.object(m,'state_get',return_value={}), \
             mock.patch.object(m,'state_save'), \
             mock.patch.object(m,'cmd') as cli:
            m.reconcile_with_build_lock('aurora-dispute',SHA,'MCP',
                                        operation=lambda: m.bind_tool('aurora-dispute'))
        self.assertEqual(len(posts),2)
        cli.assert_not_called()

    def test_other_409_never_retry(self):
        other=RuntimeError('HTTP 409: {"message":"Duplicate configuration name"}')
        operation=mock.Mock(side_effect=other)
        with mock.patch.object(m,'latest_build') as inv:
            with self.assertRaisesRegex(RuntimeError,'Duplicate configuration name'):
                m.reconcile_with_build_lock('aurora-dispute',SHA,'MCP',operation=operation)
        operation.assert_called_once()
        inv.assert_not_called()

    def test_403_never_retry(self):
        operation=mock.Mock(side_effect=RuntimeError('amctl: HTTP 403 forbidden'))
        with mock.patch.object(m,'latest_build') as inv:
            with self.assertRaisesRegex(RuntimeError,'HTTP 403'):
                m.reconcile_with_build_lock('aurora-dispute',SHA,'MCP',operation=operation)
        operation.assert_called_once()
        inv.assert_not_called()

    def test_409_without_existing_build_refuses_retry(self):
        conflict=RuntimeError('HTTP 409: A build is already in progress for this agent')
        operation=mock.Mock(side_effect=conflict)
        with mock.patch.object(m,'BUILD_DISCOVERY_TIMEOUT',0), \
             mock.patch.object(m,'latest_build',return_value=None), \
             mock.patch.object(m,'cmd') as cli:
            with self.assertRaisesRegex(RuntimeError,'no build appeared'):
                m.reconcile_with_build_lock('aurora-dispute',SHA,'MCP',operation=operation)
        operation.assert_called_once()
        cli.assert_not_called()

    def test_409_failed_build_refuses_retry(self):
        conflict=RuntimeError('HTTP 409: A build is already in progress for this agent')
        operation=mock.Mock(side_effect=conflict)
        with mock.patch.object(m,'latest_build',return_value=existing_build('Failed')), \
             mock.patch.object(m,'cmd') as cli:
            with self.assertRaisesRegex(RuntimeError,"build status 'failed'"):
                m.reconcile_with_build_lock('aurora-dispute',SHA,'MCP',operation=operation)
        operation.assert_called_once()
        cli.assert_not_called()

    def test_409_invalid_build_source_refuses_retry(self):
        conflict=RuntimeError('HTTP 409: A build is already in progress for this agent')
        operation=mock.Mock(side_effect=conflict)
        wrong = existing_build('Building')
        wrong['buildParameters']['appPath']='/deployment/agent-manager/apps/other'
        with mock.patch.object(m,'latest_build',return_value=wrong), \
             mock.patch.object(m,'cmd') as cli:
            with self.assertRaisesRegex(RuntimeError,'source coordinates do not match'):
                m.reconcile_with_build_lock('aurora-dispute',SHA,'MCP',operation=operation)
        operation.assert_called_once()
        cli.assert_not_called()

    def test_read_inventory_timeout_refuses_retry(self):
        conflict=RuntimeError('HTTP 409: A build is already in progress for this agent')
        operation=mock.Mock(side_effect=conflict)
        with mock.patch.object(m,'latest_build',side_effect=subprocess.TimeoutExpired('amctl',120)), \
             mock.patch.object(m,'cmd') as cli:
            with self.assertRaisesRegex(RuntimeError,'inventory timed out'):
                m.reconcile_with_build_lock('aurora-dispute',SHA,'MCP',operation=operation)
        operation.assert_called_once()
        cli.assert_not_called()

    def test_two_sequential_conflicts_then_success_max_three_reads(self):
        conflict=RuntimeError('HTTP 409: A build is already in progress for this agent')
        operation=mock.Mock(side_effect=[conflict,conflict,'verified'])
        with mock.patch.object(m,'latest_build',return_value=existing_build('Completed')), \
             mock.patch.object(m,'state_get',return_value={}), \
             mock.patch.object(m,'state_save'), \
             mock.patch.object(m,'cmd') as cli:
            self.assertEqual(m.reconcile_with_build_lock('aurora-dispute',SHA,'MCP',operation=operation),'verified')
        self.assertEqual(operation.call_count,3)
        cli.assert_not_called()

    def test_permanent_409_stops_at_bounded_attempts(self):
        conflict=RuntimeError('HTTP 409: A build is already in progress for this agent')
        operation=mock.Mock(side_effect=conflict)
        with mock.patch.object(m,'latest_build',return_value=existing_build('Completed')), \
             mock.patch.object(m,'state_get',return_value={}), \
             mock.patch.object(m,'state_save'), \
             mock.patch.object(m,'cmd') as cli:
            with self.assertRaisesRegex(RuntimeError,'repeated build-lock'):
                m.reconcile_with_build_lock('aurora-dispute',SHA,'MCP',operation=operation)
        self.assertEqual(operation.call_count,3)
        cli.assert_not_called()


if __name__ == '__main__':
    unittest.main()
