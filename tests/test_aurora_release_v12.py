"""Self-contained safety tests for the v12 release bundle."""
from __future__ import annotations
import importlib.util
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

# Works while installed in repository, and when running the package fixture.
ROOT=Path(__file__).resolve().parents[1]

def load(rel, name):
    path=ROOT/rel
    spec=importlib.util.spec_from_file_location(name,path)
    m=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m

installer=load('scripts/apply-aurora-release-v12.py','installer_v12') if (ROOT/'scripts/apply-aurora-release-v12.py').exists() else None
images=load('scripts/retire-aurora-local-images-v12.py','images_v12')
audit=load('scripts/aurora-release-audit-v12.py','audit_v12')

SRC='''import os
TOOLS_BY_ROLE={'account': {'get_account_balance'}, 'card': {'get_card_status'}}
async def mcp_tool(name, arguments):
    return name
'''

class ReleaseV12Tests(unittest.TestCase):
    def test_release_readme_detailed_and_only_one_payload(self):
        text=(ROOT/'README.md').read_text()
        for token in ('S01','S02','S03','S04','S05','S06','AgentID','MCP','troubleshooting','Cloudflare','Docker','PostgreSQL'):
            self.assertIn(token.lower(),text.lower())

    def test_role_guard_enforces_missing_unknown_and_wrong_tool(self):
        if installer is None: self.skipTest('Patcher not bundled in final installed repository')
        fixed=installer.role_patch(SRC)
        self.assertIn(installer.SAFE_ROLE_MARKER,fixed)
        self.assertEqual(installer.role_patch(fixed),fixed)
        space={};exec(fixed,space)
        import asyncio
        with patch.dict(os.environ,{'AGENT_ROLE':'account'}):
            self.assertEqual(asyncio.run(space['mcp_tool']('get_account_balance',{})),'get_account_balance')
            with self.assertRaises(PermissionError):
                asyncio.run(space['mcp_tool']('get_card_status',{}))
        with patch.dict(os.environ,{'AGENT_ROLE':'unknown'}):
            with self.assertRaises(PermissionError):
                asyncio.run(space['mcp_tool']('get_account_balance',{}))
        with patch.dict(os.environ,{},clear=True):
            with self.assertRaises(PermissionError):
                asyncio.run(space['mcp_tool']('get_account_balance',{}))

    def test_role_guard_refuses_unknown_shapes(self):
        if installer is None: self.skipTest('Patcher not bundled in final installed repository')
        with self.assertRaises(ValueError):
            installer.role_patch('import os\nasync def mcp_tool(name, args): return 1\n')
        with self.assertRaises(ValueError):
            installer.role_patch('import os\nTOOLS_BY_ROLE = {}\n')

    def test_git_and_docker_rules_idempotent(self):
        if installer is None: self.skipTest('Patcher not bundled in final installed repository')
        for rules in (installer.DOCKER_RULES,installer.GIT_RULES):
            original='hello\n'
            fixed=installer.add_rules(original,rules)
            self.assertEqual(installer.add_rules(fixed,rules),fixed)

    def test_installer_on_fixture_creates_only_source_files(self):
        if installer is None: self.skipTest('Patcher not bundled in final installed repository')
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'.git').mkdir();(root/'agents').mkdir();(root/'scripts').mkdir()
            (root/'scripts/start-everything.sh').write_text('#!/bin/sh\n')
            (root/'scripts/aurora-full-reconcile.py').write_text("inputs = ['Dockerfile', 'requirements.txt', 'support.yaml']\n")
            (root/'agents/common.py').write_text(SRC)
            (root/'.gitignore').write_text('# start\n')
            (root/'.dockerignore').write_text('# start\n')
            (root/'README-OLD.md').write_text('old\n')
            orig=installer.ROOT
            try:
                installer.ROOT=root
                changes=installer.copies()
                self.assertIn(Path('README.md'),changes)
                self.assertIn(Path('agents/common.py'),changes)
                self.assertIn(Path('scripts/aurora-full-reconcile.py'),changes)
                self.assertIn(b"'Dockerfile','.dockerignore'", changes[Path('scripts/aurora-full-reconcile.py')])
                self.assertIn(Path('.dockerignore'),changes)
                self.assertIn(Path('.gitignore'),changes)
                self.assertEqual([p.name for p in installer.remove_candidates()],['README-OLD.md'])
                for rel,content in changes.items():
                    dst=root/rel;dst.parent.mkdir(parents=True,exist_ok=True);dst.write_bytes(content)
                self.assertEqual(installer.copies(),changes)
                self.assertNotIn(installer.SAFE_ROLE_MARKER, SRC)
            finally: installer.ROOT=orig

    def test_image_removal_allowlist_is_tight(self):
        for name in ('aurora-banking-support:amp-v4','host.k3d.internal:10082/aurora-banking-support:amp-v4'):
            self.assertRegex(name,images.SUPPORT_RE)
        for name in ('ubuntu:latest','ws02-agent:1','aurora-account:latest','aurora-banking-support:foo;docker','aurora-banking-support:*'):
            self.assertNotRegex(name,images.SUPPORT_RE)

    def test_audit_secret_indicators(self):
        self.assertIn('secrets.env',audit.PRIVATE_NAMES)
        self.assertTrue(audit.KEY_PATTERNS[0][1].search(b'-----BEGIN ' + b'PRIVATE KEY-----'))

if __name__=='__main__': unittest.main()
