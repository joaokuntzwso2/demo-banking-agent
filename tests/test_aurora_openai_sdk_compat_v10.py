import ast
import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

FILE = Path(__file__).resolve().parents[1] / 'scripts' / 'patch-aurora-openai-sdk-compat.py'
spec = importlib.util.spec_from_file_location('aurora_sdk_fix_v10', FILE)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

COMMON = '''import os
async def llm():
    if managed:
        # Agent Manager's OpenAI-compatible gateway accepts the configured
        options={'api_key':'', 'base_url':url,
                 'default_headers':{header:key, 'Authorization':''}}
    else:
        options={'api_key':key}
'''
CONCIERGE = '''import os,asyncio,json,httpx,uuid,re
async def plan(message):
    try:
        raw=await llm(guidance,message)
    except Exception as exc:
        if os.getenv('USE_LLM_PROVIDER','false').lower()=='true':
            raise HTTPException(503,'Governed model unavailable or rejected the request; no banking actions initiated') from exc
'''


class SDKCompatibilityTests(unittest.TestCase):
    def test_common_replaces_empty_sdk_api_key(self):
        text, result = mod.patch_common(COMMON)
        self.assertEqual(result, 'change')
        self.assertIn("'api_key':'gateway-only-placeholder'", text)
        self.assertNotIn("'api_key':''", text)
        self.assertIn("'default_headers':{header:key, 'Authorization':''}", text)
        self.assertIn("options={'api_key':key}", text)

    def test_common_idempotent(self):
        patched, _ = mod.patch_common(COMMON)
        self.assertEqual(mod.patch_common(patched), (patched, 'already'))

    def test_rejects_missing_header_override(self):
        with self.assertRaises(mod.PatchError):
            mod.patch_common(COMMON.replace("'Authorization':''", "'Authorization':'Bearer key'"))

    def test_rejects_duplicate_sdk_constructor_matches(self):
        with self.assertRaises(mod.PatchError):
            mod.patch_common(COMMON + COMMON)

    def test_rejects_unrecognized_sdk_constructor(self):
        with self.assertRaises(mod.PatchError):
            mod.patch_common(COMMON.replace("'api_key':''", "'api_key':'another'"))

    def test_concierge_safe_error_logging(self):
        patched, mode = mod.patch_concierge(CONCIERGE)
        self.assertEqual(mode, 'change')
        ast.parse(patched, feature_version=(3, 11))
        self.assertIn("type(exc).__name__, status", patched)
        self.assertIn('logging.getLogger', patched)
        self.assertNotIn('str(exc)', patched)
        self.assertNotIn('logging.exception(', patched)
        self.assertIn('raise HTTPException(503', patched)

    def test_concierge_idempotent(self):
        patched, _ = mod.patch_concierge(CONCIERGE)
        self.assertEqual(mod.patch_concierge(patched), (patched, 'already'))

    def test_concierge_unrecognized_source_fails(self):
        with self.assertRaises(mod.PatchError):
            mod.patch_concierge(CONCIERGE.replace("from exc", ""))

    def test_python311_grammar(self):
        ast.parse(FILE.read_text(), feature_version=(3, 11))

    def test_default_read_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'agents').mkdir()
            (root / 'agents/common.py').write_text(COMMON)
            (root / 'agents/concierge.py').write_text(CONCIERGE)
            with patch.object(mod, 'ROOT', root):
                self.assertEqual(mod.run(False), 0)
            self.assertEqual((root / 'agents/common.py').read_text(), COMMON)
            self.assertEqual((root / 'agents/concierge.py').read_text(), CONCIERGE)

    def test_apply_changes_exactly_two_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'agents').mkdir()
            (root / 'agents/common.py').write_text(COMMON)
            (root / 'agents/concierge.py').write_text(CONCIERGE)
            (root / 'other.py').write_text('unchanged')
            with patch.object(mod, 'ROOT', root), patch.object(mod, 'is_dirty', return_value=False):
                self.assertEqual(mod.run(True), 0)
                self.assertEqual(mod.run(True), 0)
            self.assertEqual((root / 'other.py').read_text(), 'unchanged')
            self.assertIn('gateway-only-placeholder', (root / 'agents/common.py').read_text())
            self.assertIn("logging.getLogger('aurora.llm')", (root / 'agents/concierge.py').read_text())

    def test_dirty_source_blocks_mutation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'agents').mkdir()
            (root / 'agents/common.py').write_text(COMMON)
            (root / 'agents/concierge.py').write_text(CONCIERGE)
            with patch.object(mod, 'ROOT', root), patch.object(mod, 'is_dirty', return_value=True):
                with self.assertRaises(mod.PatchError):
                    mod.run(True)
            self.assertEqual((root / 'agents/common.py').read_text(), COMMON)

    def test_symlink_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'agents').mkdir()
            (root / 'outside.py').write_text(COMMON)
            (root / 'agents/common.py').symlink_to(root / 'outside.py')
            (root / 'agents/concierge.py').write_text(CONCIERGE)
            with patch.object(mod, 'ROOT', root):
                with self.assertRaises(mod.PatchError):
                    mod.run(False)


if __name__ == '__main__':
    unittest.main()
