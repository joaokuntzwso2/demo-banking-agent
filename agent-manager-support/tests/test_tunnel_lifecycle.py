"""Execute the real ensure_tunnel Bash function with stubbed external IO.

Protects against the original 'previous local tunnel processes exist' failure
and catches accidentally restarting a healthy public URL on repeat startup.
"""
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[2]
SCRIPT=ROOT/'scripts'/'publish-mcp-secure-tunnel.sh'
SRC=SCRIPT.read_text()
FUNC=SRC[SRC.index('ensure_tunnel(){'):SRC.index('\ncase "$ACTION" in',SRC.index('ensure_tunnel(){'))]

class TunnelLifecycle(unittest.TestCase):
    def simulate(self, *, processes='all', public_ok=True, local_ok=True,
                 announced=True, key=True):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)
            (path/'url').write_text('https://valid-demo.trycloudflare.com')
            if key:(path/'origin.key').write_text('secret-for-test')
            proc_ok=('0' if processes=='all' else '1')
            pub_ok=('0' if public_ok else '1')
            loc_ok=('0' if local_ok else '1')
            shell=f'''set -Eeuo pipefail
STATE="{path}"
KUBE_CONTEXT='k3d-amp-local'
KUBE_NAMESPACE='aurora-demo'
need() {{ :; }}
kubectl() {{ :; }}
proc_running() {{ return {proc_ok}; }}
log_url() {{ echo '{'https://valid-demo.trycloudflare.com' if announced else 'https://different-demo.trycloudflare.com'}'; }}
verify_local_origin() {{ return {loc_ok}; }}
verify_public_origin() {{ return {pub_ok}; }}
stop_all() {{ echo STOP; }}
start_tunnel_fresh() {{ echo START; }}
{FUNC}
ensure_tunnel
'''
            return subprocess.run(['bash','-c',shell],text=True,capture_output=True)

    def test_fully_healthy_is_reused_without_restart(self):
        run=self.simulate()
        self.assertEqual(run.returncode,0,run.stderr)
        self.assertIn('Reusing verified tunnel',run.stdout)
        self.assertNotIn('STOP',run.stdout)
        self.assertNotIn('START',run.stdout)

    def test_partial_or_stale_state_is_repaired(self):
        run=self.simulate(processes='partial')
        self.assertEqual(run.returncode,0,run.stderr)
        self.assertIn('STOP',run.stdout)
        self.assertIn('START',run.stdout)

    def test_public_probe_failure_repairs_tunnel(self):
        run=self.simulate(public_ok=False)
        self.assertEqual(run.returncode,0,run.stderr)
        self.assertIn('STOP',run.stdout)
        self.assertIn('START',run.stdout)

    def test_stale_cloudflared_url_repairs_tunnel(self):
        run=self.simulate(announced=False)
        self.assertEqual(run.returncode,0,run.stderr)
        self.assertIn('STOP',run.stdout)
        self.assertIn('START',run.stdout)

    def test_missing_secret_repairs_tunnel(self):
        run=self.simulate(key=False)
        self.assertEqual(run.returncode,0,run.stderr)
        self.assertIn('STOP',run.stdout)
        self.assertIn('START',run.stdout)

if __name__=='__main__':unittest.main()
