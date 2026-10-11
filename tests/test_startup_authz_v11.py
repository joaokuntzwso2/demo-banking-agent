"""Unit proofs for fail-closed native AgentID and first-run tunnel wiring."""
import ast
import importlib.util
import pathlib
import subprocess
import sys
import unittest
from unittest import mock

ROOT=pathlib.Path(__file__).resolve().parent.parent

def load(name,path):
    s=importlib.util.spec_from_file_location(name,ROOT/'scripts'/path)
    m=importlib.util.module_from_spec(s)
    s.loader.exec_module(m)
    return m

SEC=load('aurora_authz_native','verify-aurora-agentid-denial.py')
TUN=load('aurora_tunnel_health','verify-aurora-tunnel-public.py')
PATCH=load('aurora_installer','patch-aurora-startup-v11.py')

class NativeAuthzTests(unittest.TestCase):
    def test_python311_grammar(self):
        for path in ('verify-aurora-agentid-denial.py','verify-aurora-tunnel-public.py',
                     'patch-aurora-startup-v11.py','aurora-full-reconcile.py'):
            ast.parse((ROOT/'scripts'/path).read_text(), feature_version=(3,11))

    def test_real_denial_mandatory(self):
        self.assertTrue(SEC.validate_proof({'stage':'forbidden_tool','http':403}))
        for status in (0,200,201,202,400,401,404,429,500,503):
            with self.assertRaises(SEC.ProofError):
                SEC.validate_proof({'stage':'forbidden_tool','http':status})
        for stage in ('token','token_invalid','allowed_read','initialize','token_scope_unreadable',
                      'forbidden_scope_present','allowed_tool_error'):
            with self.assertRaises(SEC.ProofError):
                SEC.validate_proof({'stage':stage,'http':403})

    def test_no_mutation_tools_in_worker(self):
        w=SEC.WORKER
        self.assertIn("'get_account_balance'",w)
        self.assertIn("'get_card_status'",w)
        self.assertIn("'scope'",w)
        for bad in ('execute_approved_', 'request_pix', 'request_card_block', 'request_dispute',
                    'os.system', 'subprocess', 'eval(', 'exec('):
            self.assertNotIn(bad,w)

    def test_no_oauth_secrets_in_command_line(self):
        with mock.patch.object(SEC,'kubectl') as run:
            run.return_value.returncode=0
            run.return_value.stdout='{"stage":"forbidden_tool","http":403}'
            # run_worker performed inline in main; assert source contract, never command payload
            s=(ROOT/'scripts'/'verify-aurora-agentid-denial.py').read_text()
            self.assertIn('stdin=payload',s)
            self.assertNotIn('f"Authorization: Bearer',s)
            self.assertIn("'exec','-i'",s)

    def test_account_pod_uniqueness_and_ready(self):
        def pod(name, ready=True):
            return {'metadata':{'name':name,'namespace':'dp-default-banco-aurora-default-abcd'},
                'status':{'phase':'Running','containerStatuses':[{'name':'main','ready':ready}]},
                'spec':{'containers':[{'name':'main'}]}}
        x=pod('aurora-account-a1')
        self.assertEqual(SEC.account_pod({'items':[x]})[:2],('dp-default-banco-aurora-default-abcd','aurora-account-a1'))
        with self.assertRaises(SEC.ProofError):SEC.account_pod({'items':[x,pod('aurora-account-b2')]})
        with self.assertRaises(SEC.ProofError):SEC.account_pod({'items':[pod('aurora-account-a1',False)]})

    def test_target_url_bound_to_governed_proxy(self):
        self.assertEqual(SEC.verified_mcp_url('http://default-default.gateway.localhost:19080/aurora-banking/mcp'),
                         'http://default-default.gateway.localhost:19080/aurora-banking/mcp')
        for bad in ('http://evil.invalid/mcp','http://default-default.gateway.localhost:19080/evil/mcp',
                    'http://localhost:18101/mcp', 'https://a:b@default-default.gateway.localhost/aurora-banking/mcp'):
            with self.assertRaises(SEC.ProofError):SEC.verified_mcp_url(bad)

    def test_injected_plaintext_identity_secret_rejected(self):
        with self.assertRaisesRegex(SEC.ProofError,'plaintext'):
            SEC.read_env('demo',{'env':[{'name':'AMP_AGENTID_CLIENT_SECRET','value':'abc'}]},
                         'AMP_AGENTID_CLIENT_SECRET')

    def test_no_shell_in_oauth_security_gate(self):
        reconciler=(ROOT/'scripts'/'aurora-full-reconcile.py').read_text()
        self.assertIn("cmd([sys.executable,'scripts/verify-aurora-agentid-denial.py','--run']",reconciler)
        self.assertNotIn("'bash','-lc',os.environ['AURORA_MCP_AUTHZ_VERIFY_CMD']",reconciler)
        self.assertNotIn('not configured. Set AURORA_MCP_AUTHZ_VERIFY_CMD',reconciler)

class TunnelTests(unittest.TestCase):
    def test_reject_wrong_public_origin(self):
        for origin in ('http://x.trycloudflare.com','https://evil.example',
            'https://evil.trycloudflare.com/redirect','https://x.trycloudflare.com/?api_key=abc',
            'https://victim:secret@x.trycloudflare.com'):
            with self.assertRaises(TUN.TunnelError):TUN.origin_url(origin)
        self.assertEqual(TUN.origin_url('https://alpha.trycloudflare.com'),
                         'https://alpha.trycloudflare.com/mcp')

    def test_public_401_requires_matching_origin_bridge_marker(self):
        from tempfile import TemporaryDirectory
        with TemporaryDirectory() as directory:
            f=pathlib.Path(directory)/'url';f.write_text('https://check.trycloudflare.com\n')
            with mock.patch.object(TUN,'STATE',f):
                with mock.patch.object(TUN,'http_probe',side_effect=[(401,{'x-aurora-origin-bridge':'sentinel'}),
                                                                     (401,{'x-aurora-origin-bridge':'sentinel'})]):
                    TUN.check()
                with mock.patch.object(TUN,'http_probe',side_effect=[(401,{'x-aurora-origin-bridge':'sentinel'}),
                                                                     (401,{'x-aurora-origin-bridge':'wrong'})]):
                    with self.assertRaises(TUN.TunnelError):TUN.check()
                with mock.patch.object(TUN,'http_probe',side_effect=[(401,{}),(401,{})]):
                    with self.assertRaises(TUN.TunnelError):TUN.check()

    def test_startup_reconciles_expired_tunnel_for_agents_and_up(self):
        script=(ROOT/'scripts'/'start-everything.sh').read_text()
        self.assertIn("bash scripts/publish-mcp-secure-tunnel.sh stop",script)
        self.assertGreaterEqual(script.count('scripts/verify-aurora-tunnel-public.py'),4)
        self.assertGreaterEqual(script.count('AURORA_ALLOW_EPHEMERAL_PROXY_UPDATE=true'),2)
        self.assertEqual(subprocess.run(['bash','-n',str(ROOT/'scripts'/'start-everything.sh')]).returncode,0)

    def test_source_patcher_idempotent(self):
        s=(ROOT/'scripts'/'start-everything.sh').read_text()
        r=(ROOT/'scripts'/'aurora-full-reconcile.py').read_text()
        a,b=PATCH.transform(s,r)
        self.assertEqual((a,b),(s,r))

    def test_original_first_install_and_repeat(self):
        s=(ROOT/'scripts'/'start-everything.sh').read_text()
        r=(ROOT/'scripts'/'aurora-full-reconcile.py').read_text()
        old_s=(s.replace(PATCH.NEW_TUNNEL,PATCH.TUNNEL_BLOCK,1)
                .replace(PATCH.AFTER_CHECK,PATCH.BEFORE_CHECK,1)
                .replace(PATCH.AFTER_AGENTS,PATCH.BEFORE_AGENTS,1))
        old_r=r.replace(PATCH.AFTER_DENIAL,PATCH.BEFORE_DENIAL,1)
        patched=PATCH.transform(old_s,old_r)
        self.assertEqual(patched,(s,r))
        self.assertEqual(PATCH.transform(*patched),patched)

if __name__=='__main__':unittest.main()
