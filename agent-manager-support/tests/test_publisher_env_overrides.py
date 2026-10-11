"""Regression: secure tunnel variables must survive loading support.env.

No networking, Kubernetes, Cloudflare, or Agent Manager API calls are made.
"""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

SOURCE = Path(__file__).resolve().parents[1] / 'start.sh'


class PublisherEnvironmentTests(unittest.TestCase):
    def run_publish(self, extra_env, config=''):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            support = root / 'agent-manager-support'
            support.mkdir()
            (root / 'mock_core').mkdir()
            (root / 'mcp_server').mkdir()
            (root / 'mock_core' / 'app.py').write_text('# test fixture\n')
            (root / 'mcp_server' / 'app.py').write_text('# test fixture\n')
            shutil.copy(SOURCE, support / 'start.sh')
            (support / 'support.env').write_text('''DEPLOY_TARGET=kubernetes
KUBE_CONTEXT=k3d-amp-local
KUBE_NAMESPACE=aurora-demo
AMP_PUBLISH=true
AURORA_MCP_UPSTREAM_URL=http://aurora-mcp.aurora-demo.svc.cluster.local:8101/mcp
AMP_DISCOVERY_MODE=control-plane
''' + config)
            (support / 'publish_proxy.py').write_text('# substituted by a stub executable\n')
            bindir = root / 'bin'
            bindir.mkdir()
            (bindir / 'amctl').write_text('#!/bin/sh\nexit 0\n')
            (bindir / 'kubectl').write_text('''#!/bin/sh
if [ "$1" = config ] && [ "$2" = current-context ]; then
  echo k3d-amp-local
fi
exit 0
''')
            # Only log the presence of a key, never its value.
            (bindir / 'python3').write_text('''#!/bin/sh
printf 'CAPTURE_URL=%s\\n' "$AURORA_MCP_UPSTREAM_URL"
printf 'CAPTURE_DISCOVERY=%s\\n' "${AURORA_MCP_DISCOVERY_URL-}"
printf 'CAPTURE_MODE=%s\\n' "${AMP_DISCOVERY_MODE-}"
if [ -n "${AURORA_MCP_ORIGIN_KEY-}" ]; then echo 'CAPTURE_HAS_ORIGIN_KEY=yes'; else echo 'CAPTURE_HAS_ORIGIN_KEY=no'; fi
''')
            for p in bindir.iterdir():
                p.chmod(0o755)
            env = os.environ.copy()
            for key in ('AURORA_MCP_UPSTREAM_URL', 'AURORA_MCP_DISCOVERY_URL',
                        'AURORA_MCP_ORIGIN_KEY', 'AMP_DISCOVERY_MODE'):
                env.pop(key, None)
            env.update(extra_env)
            env['PATH'] = f'{bindir}:{env["PATH"]}'
            return subprocess.run(['bash', str(support / 'start.sh'), 'publish'],
                                  capture_output=True, text=True, env=env, timeout=10)

    def test_explicit_tunnel_overrides_config_defaults(self):
        url = 'https://example-tunnel.trycloudflare.com/mcp'
        secret = 'synthetic-test-secret-do-not-log-' * 2
        result = self.run_publish({
            'AURORA_MCP_UPSTREAM_URL': url,
            'AURORA_MCP_DISCOVERY_URL': 'http://aurora-mcp.aurora-demo.svc.cluster.local:8101/mcp',
            'AURORA_MCP_ORIGIN_KEY': secret,
            'AMP_DISCOVERY_MODE': 'cluster',
        })
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('CAPTURE_URL=' + url, result.stdout)
        self.assertIn('CAPTURE_MODE=cluster', result.stdout)
        self.assertIn('CAPTURE_HAS_ORIGIN_KEY=yes', result.stdout)
        self.assertNotIn(secret, result.stdout + result.stderr)

    def test_config_defaults_apply_without_runtime_override(self):
        result = self.run_publish({})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('CAPTURE_URL=http://aurora-mcp.aurora-demo.svc.cluster.local:8101/mcp', result.stdout)
        self.assertIn('CAPTURE_MODE=control-plane', result.stdout)
        self.assertIn('CAPTURE_HAS_ORIGIN_KEY=no', result.stdout)

    def test_environment_override_wins_even_if_file_contains_a_different_secret(self):
        result = self.run_publish({
            'AURORA_MCP_UPSTREAM_URL': 'https://demo.trycloudflare.com/mcp',
            'AURORA_MCP_ORIGIN_KEY': 'runtime-secret-' * 5,
        }, config='AURORA_MCP_ORIGIN_KEY=old-config-secret\n')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('CAPTURE_URL=https://demo.trycloudflare.com/mcp', result.stdout)
        self.assertIn('CAPTURE_HAS_ORIGIN_KEY=yes', result.stdout)
        self.assertNotIn('runtime-secret', result.stdout + result.stderr)
        self.assertNotIn('old-config-secret', result.stdout + result.stderr)

    def test_start_script_parses_with_bash(self):
        subprocess.run(['bash', '-n', str(SOURCE)], check=True)


if __name__ == '__main__':
    unittest.main()
