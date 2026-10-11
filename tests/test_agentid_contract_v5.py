"""WSO2 AMP 1.0.0 AgentID contract: GET agents/groups vs POST assignments.

Fixtures come from the tagged Go DTO definition, not from an unobserved live
Aurora response. The read-back cases reflect the exact reported failure.
"""
import copy
import importlib.util
import pathlib
import unittest
from unittest import mock

ROOT=pathlib.Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('aurora_agentid_v5', ROOT/'scripts/aurora-full-reconcile.py')
m=importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
BASE=f'/orgs/{m.ORG}/environments/{m.ENV}/agent-identities'


def inventory():
    roles=[{'id':f'role-{role}','name':f'aurora-{role}'} for role in m.ROLES]
    agents=[{'id':f'control-plane-{role}', 'name':f'aurora-{role}',
             'projectName':m.PROJECT,'thunderAgentId':f'thunder-{role}'} for role in m.ROLES]
    return roles,agents


def responses(current=None, *, mutate_on_add=True):
    """Model a backend with persistent role assignments and exact CLI paths."""
    roles,agents=inventory()
    current={role:set(current.get(role,set())) for role in m.ROLES} if current is not None else {r:set() for r in m.ROLES}
    requests=[]
    def service(path,method='GET',data=None,allow_404=False):
        requests.append((method,path,copy.deepcopy(data)))
        if path==BASE+'/roles':return {'roles': copy.deepcopy(roles)}
        if path==BASE+'/agents':return {'agents': copy.deepcopy(agents)}
        assert path.startswith(BASE+'/roles/'),path
        role=path.split('/roles/')[1].split('/')[0].removeprefix('role-')
        assert role in m.ROLES,(path,role)
        if method=='GET':
            assignees=[{'id':ident,'type':'agent'} for ident in sorted(current[role])]
            return {'agents':assignees,'groups':[]}
        if method=='POST' and path.endswith('/assignments/add'):
            assert data=={'assignments':[{'id':'thunder-'+role,'type':'agent'}]},data
            if mutate_on_add:current[role].add('thunder-'+role)
            return {}
        raise AssertionError((method,path))
    return service,requests,current

class ContractCases(unittest.TestCase):
    def test_get_contract_uses_agents_not_assignments(self):
        self.assertEqual(m.agent_role_assignees({'agents':[{'id':'thunder-account','type':'agent'}],
                                                  'groups':[]},role_name='aurora-account'),
                         {'thunder-account'})

    def test_go_omitempty_empty_object_is_no_grants(self):
        self.assertEqual(m.agent_role_assignees({},role_name='aurora-account'),set())

    def test_group_only_is_not_direct_agent_grant(self):
        self.assertEqual(m.agent_role_assignees({'groups':[{'id':'group-1','name':'team'}]},
                                               role_name='aurora-account'),set())

    def test_wrong_post_request_shape_never_accepted_as_get(self):
        with self.assertRaisesRegex(RuntimeError,'unexpected assignments request shape'):
            m.agent_role_assignees({'assignments':[{'id':'x','type':'agent'}]},role_name='account')

    def test_reject_unrecognized_wrapper_or_key(self):
        for item in ({'data':{'agents':[]}}, {'people':[]},{'agents':[],'unexpected':True}, []):
            with self.subTest(item=item),self.assertRaises(RuntimeError):
                m.agent_role_assignees(item,role_name='account')

    def test_reject_malformed_agents_groups_and_missing_type(self):
        for obj in ({'agents':None},{'agents':{}}, {'groups':{}},
                    {'agents':[{'id':'abc'}]}, {'agents':[{'type':'agent'}]},
                    {'agents':[{'id':'a','type':'user'}]},
                    {'groups':[{'name':'missing id'}]}, {'agents':['abc']}):
            with self.subTest(obj=obj),self.assertRaises(RuntimeError):
                m.agent_role_assignees(obj,role_name='account')

    def test_duplicate_same_agent_record_is_rejected(self):
        with self.assertRaisesRegex(RuntimeError,'duplicate agent assignments'):
            m.agent_role_assignees({'agents':[{'id':'a','type':'agent'},
                                               {'id':'a','type':'agent'}]},role_name='account')

    def test_all_five_reused_without_post_repeatable(self):
        svc,calls,_=responses({r:{'thunder-'+r} for r in m.ROLES})
        with mock.patch.object(m,'api',side_effect=svc),mock.patch.object(m.time,'sleep') as sleep:
            m.ensure_identity_roles()
            m.ensure_identity_roles()
        self.assertFalse([c for c in calls if c[0]!='GET'])
        sleep.assert_not_called()

    def test_missing_grants_post_exactly_once_then_verify_readback(self):
        svc,calls,state=responses()
        with mock.patch.object(m,'api',side_effect=svc),mock.patch.object(m.time,'sleep'):
            m.ensure_identity_roles()
            m.ensure_identity_roles()
        self.assertEqual(len([x for x in calls if x[0]=='POST']),5)
        self.assertTrue(all(state[r]=={'thunder-'+r} for r in m.ROLES))

    def test_previous_run_granted_account_only_rest_missing(self):
        svc,calls,_=responses({'account':{'thunder-account'}})
        with mock.patch.object(m,'api',side_effect=svc),mock.patch.object(m.time,'sleep'):
            m.ensure_identity_roles()
        writes=[c for c in calls if c[0]=='POST']
        self.assertEqual(len(writes),4)
        self.assertTrue(all('role-account' not in c[1] for c in writes))

    def test_check_is_read_only_with_missing_grants(self):
        svc,calls,_=responses()
        with mock.patch.object(m,'api',side_effect=svc),mock.patch.object(m.time,'sleep'):
            with self.assertRaisesRegex(RuntimeError,'check is read-only'):
                m.ensure_identity_roles(read_only=True)
        self.assertFalse([c for c in calls if c[0]=='POST'])

    def test_check_is_read_only_with_full_grants(self):
        svc,calls,_=responses({r:{'thunder-'+r} for r in m.ROLES})
        with mock.patch.object(m,'api',side_effect=svc):
            m.ensure_identity_roles(read_only=True)
        self.assertFalse([c for c in calls if c[0]=='POST'])

    def test_preflight_malformed_late_role_prevents_earlier_write(self):
        svc,calls,_=responses()
        def broken(path,method='GET',data=None,allow_404=False):
            if path.endswith('/role-wellness/assignments'):
                return {'assignments':[]}
            return svc(path,method,data,allow_404)
        with mock.patch.object(m,'api',side_effect=broken):
            with self.assertRaisesRegex(RuntimeError,'request shape'):
                m.ensure_identity_roles()
        self.assertFalse([c for c in calls if c[0]=='POST'])

    def test_identity_without_thunder_id_is_not_guessed_from_control_plane_id(self):
        svc,calls,_=responses()
        def broken(path,method='GET',data=None,allow_404=False):
            if path==BASE+'/agents':
                xs=inventory()[1]
                xs[0].pop('thunderAgentId')
                return {'agents':xs}
            return svc(path,method,data,allow_404)
        with mock.patch.object(m,'api',side_effect=broken):
            with self.assertRaisesRegex(RuntimeError,'missing role id or thunderAgentId'):
                m.ensure_identity_roles()
        self.assertFalse([c for c in calls if c[0]=='POST'])

    def test_duplicate_identity_name_or_role_prevents_writes(self):
        svc,calls,_=responses()
        for resource in ('roles','agents'):
            with self.subTest(resource=resource):
                def broken(path,method='GET',data=None,allow_404=False):
                    if path==BASE+'/'+resource:
                        xs=inventory()[0 if resource=='roles' else 1]
                        return {resource:xs+[copy.deepcopy(xs[0])]}
                    return svc(path,method,data,allow_404)
                with mock.patch.object(m,'api',side_effect=broken):
                    with self.assertRaisesRegex(RuntimeError,'Cannot uniquely match'):
                        m.ensure_identity_roles()
        self.assertFalse([c for c in calls if c[0]=='POST'])

    def test_duplicate_thunder_agent_ids_across_roles_prevents_writes(self):
        svc,calls,_=responses()
        def broken(path,method='GET',data=None,allow_404=False):
            if path==BASE+'/agents':
                agents=inventory()[1]
                agents[1]['thunderAgentId']=agents[0]['thunderAgentId']
                return {'agents':agents}
            return svc(path,method,data,allow_404)
        with mock.patch.object(m,'api',side_effect=broken):
            with self.assertRaisesRegex(RuntimeError,'same Thunder identity ID'):
                m.ensure_identity_roles()
        self.assertFalse([c for c in calls if c[0]=='POST'])

    def test_unpersisted_post_readback_fails_without_duplicate(self):
        svc,calls,_=responses(mutate_on_add=False)
        with mock.patch.object(m,'api',side_effect=svc),mock.patch.object(m.time,'sleep') as sleep:
            with self.assertRaisesRegex(RuntimeError,'not verified after read-back'):
                m.ensure_identity_roles()
        self.assertEqual(len([x for x in calls if x[0]=='POST']),1)
        self.assertEqual(sleep.call_count,2)

    def test_delayed_persistence_retries_get_only(self):
        svc,calls,state=responses()
        reads=0
        def delayed(path,method='GET',data=None,allow_404=False):
            nonlocal reads
            if path.endswith('/role-account/assignments') and 'thunder-account' in state['account']:
                reads += 1
                if reads<3:return {'agents':[]}
            return svc(path,method,data,allow_404)
        with mock.patch.object(m,'api',side_effect=delayed),mock.patch.object(m.time,'sleep'):
            m.ensure_identity_roles()
        self.assertEqual(len([x for x in calls if x[0]=='POST']),5)
        self.assertEqual(reads,3)

    def test_conflicting_concurrent_add_accepts_only_exact_readback(self):
        svc,calls,state=responses()
        def conflict(path,method='GET',data=None,allow_404=False):
            if method=='POST' and path.endswith('/role-account/assignments/add'):
                state['account'].add('thunder-account')
                raise RuntimeError('amctl: HTTP 409 CONFLICT')
            return svc(path,method,data,allow_404)
        with mock.patch.object(m,'api',side_effect=conflict),mock.patch.object(m.time,'sleep'):
            m.ensure_identity_roles()
        self.assertEqual(len([x for x in calls if x[0]=='POST']),4)
        self.assertIn('thunder-account',state['account'])

    def test_unrelated_409_without_matching_get_is_failure(self):
        svc,calls,_=responses()
        def conflict(path,method='GET',data=None,allow_404=False):
            if method=='POST':raise RuntimeError('amctl: HTTP 409 unrelated conflict')
            return svc(path,method,data,allow_404)
        with mock.patch.object(m,'api',side_effect=conflict),mock.patch.object(m.time,'sleep'):
            with self.assertRaisesRegex(RuntimeError,'409'):
                m.ensure_identity_roles()
        self.assertFalse([x for x in calls if x[0]=='POST'])

    def test_permission_denied_no_retries(self):
        svc,calls,_=responses()
        def forbidden(path,method='GET',data=None,allow_404=False):
            if method=='POST':raise RuntimeError('HTTP 403 FORBIDDEN')
            return svc(path,method,data,allow_404)
        with mock.patch.object(m,'api',side_effect=forbidden), mock.patch.object(m.time,'sleep') as sleeper:
            with self.assertRaisesRegex(RuntimeError,'403'):
                m.ensure_identity_roles()
        sleeper.assert_not_called()

    def test_project_mismatched_identity_rejected_before_writes(self):
        svc,calls,_=responses()
        def mismatch(path,method='GET',data=None,allow_404=False):
            if path==BASE+'/agents':
                a=inventory()[1]
                a[0]['projectName']='some-other-project'
                return {'agents':a}
            return svc(path,method,data,allow_404)
        with mock.patch.object(m,'api',side_effect=mismatch):
            with self.assertRaisesRegex(RuntimeError,'Cannot uniquely match'):
                m.ensure_identity_roles()
        self.assertFalse([x for x in calls if x[0]=='POST'])

    def test_read_only_check_calls_reconciler_with_explicit_readonly(self):
        with mock.patch.object(m,'verify_proxy'), mock.patch.object(m,'llm_provider_inventory',
              return_value=[{'id':m.LLM_PROVIDER,'template':'openai'}]), \
             mock.patch.object(m,'cmd') as c, mock.patch.object(m,'agent_json',return_value={'name':'x'}), \
             mock.patch.object(m,'ensure_identity_roles') as ids, \
             mock.patch.object(m,'verify_scenarios'), \
             mock.patch.dict(m.os.environ,{'AURORA_MCP_AUTHZ_VERIFY_CMD':'echo passed'}):
            c.return_value.returncode=0
            m.check()
            ids.assert_called_once_with(read_only=True)

if __name__=='__main__':unittest.main()
