"""Test the real production generator without modifying the working repository."""
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class GeneratedOpenAPITests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='aurora-openapi-test-')
        self.addCleanup(self.tmp.cleanup)
        self.repo = Path(self.tmp.name)
        # Complete minimum dependency tree for the real prepare-apps.py.
        for name in ('agents', 'banking_common', 'knowledge', 'mcp_server'):
            origin = ROOT / name
            if origin.exists():
                shutil.copytree(origin, self.repo / name,
                                ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
            else:
                (self.repo / name).mkdir()
        (self.repo / 'requirements.txt').write_text('fastapi\n')
        generator = self.repo / 'deployment/agent-manager/scripts/prepare-apps.py'
        generator.parent.mkdir(parents=True)
        shutil.copy2(ROOT/'deployment/agent-manager/scripts/prepare-apps.py', generator)
        result = subprocess.run([sys.executable, str(generator)], cwd=self.repo,
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_all_six_build_folders_provide_parseable_openapi(self):
        apps=self.repo/'deployment/agent-manager/apps'
        self.assertEqual({p.name for p in apps.iterdir() if p.is_dir()},
            {'aurora-'+x for x in ('account','card','dispute','pix','wellness','concierge')})
        for app in apps.iterdir():
            self.assertTrue((app/'main.py').is_file())
            o = json.loads((app/'openapi.json').read_text())
            self.assertEqual(o['openapi'], '3.0.3')
            self.assertIn('/chat', o['paths'])
            self.assertIn('/health', o['paths'])
            self.assertIn('post', o['paths']['/chat'])
            self.assertEqual(o['paths']['/chat']['post']['requestBody']['content']['application/json']['schema']['$ref'],
                              '#/components/schemas/ChatRequest')
            if app.name == 'aurora-concierge':
                self.assertIn('/approve', o['paths'])
                self.assertIn('/workflow/{workflow_id}', o['paths'])
                self.assertNotIn('/execute', o['paths'])
            else:
                self.assertIn('/execute', o['paths'])
                self.assertNotIn('/approve', o['paths'])

    def test_contract_requires_banking_approval_fields(self):
        apps=self.repo/'deployment/agent-manager/apps'
        pix=json.loads((apps/'aurora-pix/openapi.json').read_text())
        concierge=json.loads((apps/'aurora-concierge/openapi.json').read_text())
        self.assertEqual(set(pix['components']['schemas']['ExecuteRequest']['required']),
            {'approval_id','payload'})
        self.assertEqual(set(concierge['components']['schemas']['ApprovalDecision']['required']),
            {'approval_id','approved','workflow_id'})
        self.assertEqual(set(concierge['components']['schemas']['ChatRequest']['required']), {'message'})

    def test_output_stable_across_repeated_generation(self):
        apps=self.repo/'deployment/agent-manager/apps'
        before={p.name:(p/'openapi.json').read_bytes() for p in apps.iterdir()}
        subprocess.run([sys.executable, str(self.repo/'deployment/agent-manager/scripts/prepare-apps.py')],
                       cwd=self.repo,check=True,capture_output=True)
        self.assertEqual(before, {p.name:(p/'openapi.json').read_bytes() for p in apps.iterdir()})

    def test_declared_endpoints_match_decorators_in_canonical_sources(self):
        agents=self.repo/'agents'
        if not (agents/'common.py').exists():self.skipTest('Canonical source unavailable')
        common=(agents/'common.py').read_text()
        specialists=(agents/'specialists.py').read_text()
        concierge=(agents/'concierge.py').read_text()
        for route in ("@app.get('/health')", "@app.post('/chat'"):
            self.assertIn(route,common)
        self.assertIn("@app.post('/execute')",specialists)
        self.assertIn("@app.post('/approve')",concierge)
        self.assertIn("@app.get('/workflow/{workflow_id}')",concierge)

if __name__ == '__main__': unittest.main()
