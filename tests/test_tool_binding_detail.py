"""Regressions for v1.0.0 MCP tool config summary versus detail responses."""
import importlib.util
import pathlib
import unittest
from unittest import mock

FILE=pathlib.Path(__file__).resolve().parent.parent/'scripts'/'aurora-full-reconcile.py'
spec=importlib.util.spec_from_file_location('aurora_mcp_tool_config_v9',FILE)
m=importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

BASE='/orgs/default/projects/banco-aurora/agents/aurora-account/tool-configs'
UUID='01234567-89ab-4cde-8123-0123456789ab'
SUMMARY={'uuid':UUID,'name':'aurora-banking','type':'mcp'}
DETAIL={**SUMMARY,'environmentVariables':[
    {'key':'url','name':'MCP_SERVER_URL'},
    {'key':'apikey','name':'MCP_PROXY_API_KEY'}
]}

def probe_list(p):
    if p==BASE: return {'configs':[SUMMARY]}
    return None

class Tests(unittest.TestCase):
    def test_reuse_existing_reads_detail_without_post(self):
        calls=[]
        def api(path, method='GET', data=None, allow_404=False):
            calls.append((path,method))
            if path==BASE:return {'configs':[SUMMARY]}
            if path==BASE+'/'+UUID:return DETAIL
            return None
        with mock.patch.object(m,'tool_config_paths',return_value=[BASE]),\
             mock.patch.object(m,'api',side_effect=api):
            m.bind_tool('aurora-account')
        self.assertEqual(calls.count((BASE+'/'+UUID,'GET')),1)
        self.assertFalse(any(method!='GET' for _,method in calls))

    def test_new_binding_reads_detail_after_create(self):
        calls=[]
        lists=iter([{'configs':[]}, {'configs':[SUMMARY]}])
        def api(path, method='GET', data=None, allow_404=False):
            calls.append((path,method,data))
            if path==BASE and method=='GET':return next(lists)
            if path==BASE and method=='POST':return {'uuid':UUID}
            if path==BASE+'/'+UUID:return DETAIL
            return None
        with mock.patch.object(m,'tool_config_paths',return_value=[BASE]),\
             mock.patch.object(m,'api',side_effect=api):
            m.bind_tool('aurora-account')
        posts=[x for x in calls if x[1]=='POST']
        self.assertEqual(len(posts),1)
        self.assertEqual(posts[0][2]['type'],'mcp')
        self.assertEqual(calls[-1][:2],(BASE+'/'+UUID,'GET'))

    def test_list_summary_is_not_mistaken_for_detail(self):
        with mock.patch.object(m,'api',return_value=None):
            with self.assertRaisesRegex(RuntimeError,'detail GET is unavailable'):
                m.verify_mcp_tool_config_detail(BASE,SUMMARY,'aurora-account')

    def test_reject_missing_detail_uuid_without_mutation(self):
        with mock.patch.object(m,'api') as client:
            with self.assertRaisesRegex(RuntimeError,'no stable UUID'):
                m.verify_mcp_tool_config_detail(BASE,{'name':'aurora-banking'},'aurora-account')
            client.assert_not_called()

    def test_repair_missing_url_mapping_in_verified_record(self):
        incomplete={**DETAIL,'environmentVariables':[{'key':'apikey','name':'MCP_PROXY_API_KEY'}]}
        calls=[]
        def api(path, method='GET', data=None, allow_404=False):
            calls.append((path,method,data))
            if method=='PUT':return {}
            return incomplete if sum(op=='GET' for _,op,_ in calls)==1 else DETAIL
        with mock.patch.object(m,'api',side_effect=api):
            m.verify_mcp_tool_config_detail(BASE,SUMMARY,'aurora-account')
        updates=[c for c in calls if c[1]=='PUT']
        self.assertEqual(len(updates),1)
        self.assertEqual(updates[0][0],BASE+'/'+UUID)
        self.assertEqual(updates[0][2],{'environmentVariables':DETAIL['environmentVariables']})
        self.assertEqual(len([c for c in calls if c[1]=='GET']),2)

    def test_no_success_when_update_does_not_persist(self):
        incomplete={**DETAIL,'environmentVariables':[]}
        with mock.patch.object(m,'api',return_value=incomplete) as api:
            with self.assertRaisesRegex(RuntimeError,'not persisted'):
                m.verify_mcp_tool_config_detail(BASE,SUMMARY,'aurora-account')
            self.assertEqual(sum(c.args[1]=='PUT' for c in api.call_args_list if len(c.args)>1),1)

    def test_do_not_update_unverified_detail(self):
        wrong={**DETAIL,'name':'another-proxy','environmentVariables':[]}
        with mock.patch.object(m,'api',return_value=wrong) as api:
            with self.assertRaisesRegex(RuntimeError,'unexpected proxy/type'):
                m.verify_mcp_tool_config_detail(BASE,SUMMARY,'aurora-account')
            self.assertFalse(any(len(c.args)>1 and c.args[1]=='PUT' for c in api.call_args_list))

    def test_reject_wrong_proxy_and_mismatched_uuid(self):
        bad={**DETAIL,'name':'not-aurora'}
        with mock.patch.object(m,'api',return_value=bad):
            with self.assertRaisesRegex(RuntimeError,'unexpected proxy/type'):
                m.verify_mcp_tool_config_detail(BASE,SUMMARY,'aurora-account')
        bad={**DETAIL,'uuid':'something-else'}
        with mock.patch.object(m,'api',return_value=bad):
            with self.assertRaisesRegex(RuntimeError,'UUID does not match'):
                m.verify_mcp_tool_config_detail(BASE,SUMMARY,'aurora-account')

    def test_reject_duplicate_configs_without_post(self):
        with mock.patch.object(m,'tool_config_paths',return_value=[BASE]),\
             mock.patch.object(m,'api',return_value={'configs':[SUMMARY,SUMMARY]}) as api:
            with self.assertRaisesRegex(RuntimeError,'Ambiguous MCP'):
                m.bind_tool('aurora-account')
            self.assertFalse(any(len(call.args)>1 and call.args[1]=='POST' for call in api.call_args_list))

if __name__=='__main__':unittest.main()
