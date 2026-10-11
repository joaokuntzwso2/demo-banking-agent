"""No-network tests for the operator-authorized k3d discovery path."""
import importlib.util
import json
from pathlib import Path
import subprocess
import unittest
from unittest.mock import patch

HERE=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('aurora_publisher', HERE/'publish_proxy.py')
mod=importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
URL='http://aurora-mcp.aurora-demo.svc.cluster.local:8101/mcp'

def descriptors(names):
    return [{'name':name,'description':'Synthetic banking '+name,
             'inputSchema':{'type':'object','properties':{'customer_id':{'type':'string'}}}}
            for name in names]


class ClusterDiscoveryTests(unittest.TestCase):
    def test_exact_url_passes_and_reads_live_inventory(self):
        result=subprocess.CompletedProcess(args=[],returncode=0,stdout='startup text\nAURORA_MCP_TOOLS_JSON='+json.dumps(descriptors(sorted(mod.ACTIONS)))+'\n',stderr='')
        with patch.object(mod.subprocess,'run',return_value=result) as runner:
            self.assertEqual({x['name'] for x in mod.discover_cluster_tools(URL,'aurora-demo','k3d-amp-local')},set(mod.ACTIONS))
            argv=runner.call_args.args[0]
            self.assertEqual(argv[:7],['kubectl','--context','k3d-amp-local','-n','aurora-demo','exec','deployment/aurora-mcp'])
            self.assertEqual(argv[-1],URL)
            self.assertEqual(runner.call_args.kwargs['timeout'],90)

    def test_deny_other_hosts_and_paths_without_network(self):
        bad=[
            'http://example.com:8101/mcp',
            'http://127.0.0.1:8101/mcp',
            'http://aurora-mcp.other.svc.cluster.local:8101/mcp',
            'http://aurora-mcp.aurora-demo.svc.cluster.local:8100/mcp',
            'http://aurora-mcp.aurora-demo.svc.cluster.local:8101/other',
            'https://aurora-mcp.aurora-demo.svc.cluster.local:8101/mcp',
            URL+'?x=1',
            'http://user:password@aurora-mcp.aurora-demo.svc.cluster.local:8101/mcp',
        ]
        with patch.object(mod.subprocess,'run') as runner:
            for url in bad:
                with self.subTest(url=url), self.assertRaises(RuntimeError):
                    mod.discover_cluster_tools(url,'aurora-demo','k3d-amp-local')
            runner.assert_not_called()

    def test_deny_empty_and_unsafe_context(self):
        with patch.object(mod.subprocess,'run') as runner:
            for context in ('','k3d amp','$(whoami)'):
                with self.assertRaises(RuntimeError):
                    mod.discover_cluster_tools(URL,'aurora-demo',context)
            runner.assert_not_called()

    def test_fail_closed_on_discovery_failure(self):
        cases=[
            subprocess.CompletedProcess([],1,'','connection refused'),
            subprocess.CompletedProcess([],0,'warning only',''),
            subprocess.CompletedProcess([],0,'AURORA_MCP_TOOLS_JSON=["same", "same"]',''),
            subprocess.CompletedProcess([],0,'AURORA_MCP_TOOLS_JSON=[{"name":"one"}]',''),
            subprocess.CompletedProcess([],0,'AURORA_MCP_TOOLS_JSON=not-json',''),
        ]
        for result in cases:
            with patch.object(mod.subprocess,'run',return_value=result), self.assertRaises(RuntimeError):
                mod.discover_cluster_tools(URL,'aurora-demo','k3d-amp-local')

    def test_mcp_program_uses_sdk_handshake_and_list_tools(self):
        script=mod.CLUSTER_DISCOVERY_PROGRAM
        self.assertIn('await session.initialize()',script)
        self.assertIn('await session.list_tools()',script)
        self.assertIn('streamable_http_client(',script)
        self.assertIn('result.tools',script)
        self.assertIn('model_dump(',script)
        self.assertIn('inputSchema',mod.validate_tool_descriptors(descriptors(['one']))[0])

    def test_create_proxy_payload_preserves_real_schema(self):
        src=descriptors(sorted(mod.ACTIONS))
        payload=mod.build_proxy_payload('aurora-banking',URL,'3ca08496-7c42-4ef5-969f-eeb658bbef91',src)
        tools=payload['endpoints'][0]['capabilities']['tools']
        self.assertEqual(len(tools),15)
        self.assertEqual([x['name'] for x in tools],sorted(mod.ACTIONS))
        self.assertTrue(all(isinstance(x,dict) for x in tools))
        self.assertEqual(tools[0]['inputSchema'],{'type':'object','properties':{'customer_id':{'type':'string'}}})
        self.assertEqual(payload['endpoints'][0]['environments'][0]['environmentUuid'],'3ca08496-7c42-4ef5-969f-eeb658bbef91')
        self.assertTrue(payload['endpoints'][0]['security']['identity']['enabled'])

    def test_refuse_name_only_and_missing_schemas(self):
        for invalid in (['list_beneficiaries'],[{'name':'list_beneficiaries'}],
                        [{'name':'a','inputSchema':[] }],
                        [{'name':'a','inputSchema':{'type':'string'}}],
                        descriptors(['dup','dup'])):
            with self.subTest(invalid=invalid),self.assertRaises(RuntimeError):
                mod.validate_tool_descriptors(invalid)

    def test_nested_control_plane_discovery_keeps_schema(self):
        data={'data':{'capabilities':{'tools':descriptors(['get_account_balance'])}}}
        tool=mod.discovered_tool_descriptors(data)[0]
        self.assertEqual(tool['name'],'get_account_balance')
        self.assertEqual(tool['inputSchema']['type'],'object')
        with self.assertRaises(RuntimeError):
            mod.discovered_tool_descriptors({'capabilities':{'tools':['get_account_balance']}})

if __name__=='__main__': unittest.main()
