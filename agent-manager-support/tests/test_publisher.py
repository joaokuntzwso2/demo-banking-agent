import ast
import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch

HERE=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('aurora_publisher', HERE/'publish_proxy.py')
mod=importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
REPO=Path(__file__).resolve().parents[2]

class PublisherTests(unittest.TestCase):
    def test_exact_live_source_tools(self):
        self.assertEqual(mod.actual_tool_names(REPO),set(mod.ACTIONS))
        self.assertEqual(len(mod.ACTIONS),15)
        self.assertEqual(set(mod.ROLE_TOOLS),{'account','card','dispute','pix','wellness','risk'})
        for role,tools in mod.ROLE_TOOLS.items():
            self.assertTrue(set(tools).issubset(mod.ACTIONS),role)
        self.assertNotIn('concierge',mod.ROLE_TOOLS)

    def test_fetch_discovery_structures(self):
        tools=['get_account_balance','execute_approved_pix']
        for obj in ({'tools':[{'name':t} for t in tools]},
                    {'data':{'capabilities':{'tools':tools}}},
                    {'data':{'tools':{'tools':[{'name':t} for t in tools]}}}):
            self.assertEqual(mod.discovered_tool_names(obj),set(tools))

    def test_environment_uuid_resolution_known_response_shapes(self):
        uid = '13572468-aaaa-4bbb-8ccc-123456789abc'
        records = [
            {'name': 'default', 'uuid': uid},
        ]
        for shape in (
            {'environments': records},
            {'data': {'environments': records}},
            {'items': records},
            {'data': {'items': [{'name': 'default', 'id': uid}]}},
            {'results': [{'name': 'default', 'environmentUuid': uid}]},
        ):
            with self.subTest(shape=shape):
                self.assertEqual(mod.resolve_environment_uuid(shape, 'default'), uid)

    def test_environment_uuid_resolution_fails_closed(self):
        uid1 = '13572468-aaaa-4bbb-8ccc-123456789abc'
        uid2 = '13572468-aaaa-4bbb-8ccc-123456789abd'
        examples = [
            ({'environments': []}, 'default'),
            ({'environments': [{'name':'staging','uuid':uid1}]}, 'default'),
            ({'environments': [{'name':'default','id':'default'}]}, 'default'),
            ({'environments': [{'name':'default','uuid':uid1},
                                {'name':'default','uuid':uid2}]}, 'default'),
        ]
        for response, handle in examples:
            with self.subTest(response=response):
                with self.assertRaises(RuntimeError):
                    mod.resolve_environment_uuid(response, handle)

    def test_render_file_cleanup_trap_safe_after_function_return(self):
        import subprocess
        script = HERE / 'start.sh'
        content = script.read_text()
        self.assertIn('AURORA_RENDER_TMP', content)
        self.assertNotIn("trap 'rm -f \"$tmp\"' EXIT", content)
        # Test the actual trap statement in a tiny strict-mode shell.
        import re
        match = re.search(r"(?m)^  trap ('[^\n]+') EXIT$", content)
        self.assertIsNotNone(match)
        body = f"set -u; f() {{ local tmp; tmp=$(mktemp); AURORA_RENDER_TMP=$tmp; trap {match.group(1)} EXIT; }}; f; true"
        result = subprocess.run(['bash','-c',body], capture_output=True,text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_existing_proxy_wrong_url_fails_closed(self):
        with patch.object(mod,'apicall',return_value={
            'id':'aurora-banking',
            'endpoints':[{'upstream':{'main':{'url':'https://wrong.example/mcp'}}}]
        }):
            with self.assertRaises(RuntimeError):
                mod.ensure_known_proxy('/orgs/default/mcp-proxies/aurora-banking',
                                       'https://right.example/mcp','aurora-banking')

    def test_each_scope_maps_one_tool(self):
        self.assertEqual(len(set(mod.ACTIONS.values())),len(mod.ACTIONS))
        self.assertTrue(all(name for name in mod.ACTIONS.values()))

if __name__=='__main__':unittest.main()
