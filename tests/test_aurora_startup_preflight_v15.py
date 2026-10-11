"""Offline tests for v15: all subprocess calls mocked; zero infrastructure writes."""
import contextlib
import importlib.util
import io
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]


def load(path,name):
    spec=importlib.util.spec_from_file_location(name,path)
    mod=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


P=load(ROOT/'scripts'/'aurora-startup-preflight-v15.py','aurora_startup_v15')
I=load(ROOT/'scripts'/'apply-aurora-startup-preflight-v15.py','aurora_install_v15')


def result(code=0,out='',err=''):
    return subprocess.CompletedProcess([],code,stdout=out,stderr=err)


class AuthenticationTests(unittest.TestCase):
    def test_valid_session_does_not_login(self):
        with patch.object(P,'local_context_status',return_value='configured'), \
             patch.object(P,'auth_probe',return_value='valid'), \
             patch.object(P,'run') as run:
            with contextlib.redirect_stdout(io.StringIO()) as output:
                self.assertEqual(P.check_authentication(),0)
            self.assertIn('AURORA_AMCTL_AUTH=verified',output.getvalue())
            run.assert_not_called()

    def test_expired_session_interactively_renews_once(self):
        with patch.object(P,'local_context_status',side_effect=['configured','configured']), \
             patch.object(P,'auth_probe',side_effect=['expired','valid']), \
             patch.object(P.sys.stdin,'isatty',return_value=True), \
             patch.object(P,'run',return_value=result()) as run:
            with contextlib.redirect_stdout(io.StringIO()) as output:
                self.assertEqual(P.check_authentication(),0)
            self.assertIn('renewed_and_verified',output.getvalue())
            run.assert_called_once_with(['amctl','login','--url',P.AMP_URL],timeout=300,inherit=True)

    def test_missing_session_interactively_renews_once(self):
        with patch.object(P,'local_context_status',side_effect=['missing','configured']), \
             patch.object(P,'auth_probe',return_value='valid'), \
             patch.object(P.sys.stdin,'isatty',return_value=True), \
             patch.object(P,'run',return_value=result()):
            self.assertEqual(P.check_authentication(),0)

    def test_noninteractive_never_launches_browser(self):
        with patch.object(P,'local_context_status',return_value='configured'), \
             patch.object(P,'auth_probe',return_value='expired'), \
             patch.object(P.sys.stdin,'isatty',return_value=False), \
             patch.object(P,'run') as run:
            with self.assertRaisesRegex(P.PreflightError,'not interactive'):
                P.check_authentication()
            run.assert_not_called()

    def test_403_never_relogs(self):
        with patch.object(P,'local_context_status',return_value='configured'), \
             patch.object(P,'auth_probe',return_value='other'), \
             patch.object(P,'run') as run:
            with self.assertRaisesRegex(P.PreflightError,'non-authentication'):
                P.check_authentication()
            run.assert_not_called()

    def test_other_instance_never_relogs(self):
        with patch.object(P,'local_context_status',return_value='foreign'), \
             patch.object(P,'run') as run:
            with self.assertRaisesRegex(P.PreflightError,'not verified'):
                P.check_authentication()
            run.assert_not_called()

    def test_ambiguous_instance_never_relogs(self):
        with patch.object(P,'local_context_status',return_value='ambiguous'):
            with self.assertRaises(P.PreflightError):
                P.check_authentication()

    def test_failed_login_never_retries(self):
        with patch.object(P,'local_context_status',return_value='missing'), \
             patch.object(P.sys.stdin,'isatty',return_value=True), \
             patch.object(P,'run',return_value=result(code=1)) as run:
            with self.assertRaisesRegex(P.PreflightError,'not completed'):
                P.check_authentication()
            self.assertEqual(run.call_count,1)

    def test_login_success_requires_second_real_probe(self):
        with patch.object(P,'local_context_status',side_effect=['missing','configured']), \
             patch.object(P.sys.stdin,'isatty',return_value=True), \
             patch.object(P,'auth_probe',return_value='expired'), \
             patch.object(P,'run',return_value=result()):
            with self.assertRaisesRegex(P.PreflightError,'not verified'):
                P.check_authentication()

    def test_classification_is_specific(self):
        self.assertEqual(P.classify_auth_failure('oauth2: "invalid_grant" "Invalid refresh token"'),'expired')
        self.assertEqual(P.classify_auth_failure('HTTP 401: unauthorized'),'expired')
        self.assertEqual(P.classify_auth_failure('not logged in'),'missing')
        self.assertEqual(P.classify_auth_failure('HTTP 500 Failed to list environments'),'other')
        self.assertEqual(P.classify_auth_failure('HTTP 403 Forbidden'),'other')
        self.assertEqual(P.classify_auth_failure('connection refused'),'other')

    def test_auth_probe_recognizes_json_error_even_exit_zero(self):
        with patch.object(P,'run',return_value=result(out='{"status":"error","error":{"code":"AUTHENTICATION_FAILED","message":"invalid_grant"}}')):
            self.assertEqual(P.auth_probe(),'expired')

    def test_auth_probe_accepts_json_org_list(self):
        with patch.object(P,'run',return_value=result(out='{"data":[{"name":"default"}]}')):
            self.assertEqual(P.auth_probe(),'valid')

    def test_auth_probe_refuses_malformed_json(self):
        with patch.object(P,'run',return_value=result(out='not json')):
            with self.assertRaises(P.PreflightError):
                P.auth_probe()

    def test_context_refuses_foreign_url(self):
        with patch.object(P,'run',return_value=result(out='Instance: https://other-agent.example.com')):
            self.assertEqual(P.local_context_status(),'foreign')

    def test_context_accepts_local_url(self):
        with patch.object(P,'run',return_value=result(out='Instance: http://api.amp.localhost:8080')):
            self.assertEqual(P.local_context_status(),'configured')

    def test_context_invalid_grant_on_read(self):
        with patch.object(P,'run',return_value=result(code=1,err='oauth2: "invalid_grant"')):
            self.assertEqual(P.local_context_status(),'expired')

    def test_observed_invalid_grant_triggers_single_login(self):
        calls=[]
        def fake_run(args, **kwargs):
            calls.append(tuple(args))
            if args[:3] == ['amctl','context','show']:
                return result(out='Instance: http://api.amp.localhost:8080')
            if args[:2] == ['amctl','api']:
                return (result(code=1,err='X refresh token grant failed: oauth2: "invalid_grant" "Invalid refresh token"')
                        if not any(c[:2] == ('amctl','login') for c in calls)
                        else result(out='{"data":[]}'))
            if args[:2] == ['amctl','login']:
                return result()
            self.fail('Unexpected amctl call')
        with patch.object(P,'run',side_effect=fake_run), \
             patch.object(P.sys.stdin,'isatty',return_value=True):
            with contextlib.redirect_stdout(io.StringIO()) as output:
                self.assertEqual(P.check_authentication(),0)
            self.assertEqual(sum(c[:2] == ('amctl','login') for c in calls),1)
            self.assertIn('renewed_and_verified',output.getvalue())

    def test_auth_probe_no_secrets_logged(self):
        with patch.object(P,'run',return_value=result(out='{"data":{"secret":"not-to-be-printed"}}')):
            out=io.StringIO()
            with contextlib.redirect_stdout(out):
                self.assertEqual(P.auth_probe(),'valid')
            self.assertNotIn('not-to-be-printed',out.getvalue())


class DependencyTests(unittest.TestCase):
    def test_amctl_100_required(self):
        self.assertIsNone(P.validate_dependency_versions('amctl','amctl version 1.0.0'))
        self.assertIsNotNone(P.validate_dependency_versions('amctl','amctl version 0.7.0'))
        self.assertIsNotNone(P.validate_dependency_versions('amctl','amctl version 2.0.0'))

    def test_version_probe_detects_missing_tools(self):
        with patch.object(P.sys,'platform','darwin'), \
             patch.object(P.platform,'machine',return_value='arm64'), \
             patch.object(P.shutil,'which',return_value=None), \
             patch.object(P,'ROOT',Path('/does-not-exist')):
            err=io.StringIO()
            with contextlib.redirect_stderr(err):
                self.assertEqual(P.check_dependencies(),1)
            self.assertIn('MISSING: amctl',err.getvalue())
            self.assertIn('MISSING: colima',err.getvalue())
            self.assertIn('MISSING SOURCE',err.getvalue())

    def test_dependency_gate_passes_supported_mac_when_all_tools_present(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)
            for name in P.REQUIRED_SOURCE:
                target=root/name
                target.parent.mkdir(parents=True,exist_ok=True)
                target.write_text('fixture')
            def probe(args, **kwargs):
                if args[:2] == ('amctl','version') or args[:2] == ['amctl','version']:
                    return result(out='amctl version 1.0.0')
                return result(out='OK')
            with patch.object(P.sys,'platform','darwin'), \
                 patch.object(P.platform,'machine',return_value='arm64'), \
                 patch.object(P.sys,'version_info',(3,11,15)), \
                 patch.object(P.shutil,'which',return_value='/opt/homebrew/bin/tool'), \
                 patch.object(P,'ROOT',root), \
                 patch.object(P,'run',side_effect=probe):
                with contextlib.redirect_stdout(io.StringIO()) as output:
                    self.assertEqual(P.check_dependencies(),0)
                self.assertIn('AURORA_PREFLIGHT=verified',output.getvalue())

    def test_read_only_program_inventory(self):
        forbidden=('delete','stop','restart','apply','login','secret','-X','POST')
        for name,cmd in P.REQUIRED_PROGRAMS.items():
            self.assertFalse(any(x in cmd for x in forbidden),(name,cmd))

    def test_url_validation_no_creds_or_remote(self):
        self.assertTrue(P.safe_local_url(P.AMP_URL))
        for url in ('https://api.amp.localhost:8080','http://evil.com:8080',
                    'http://alice:password@api.amp.localhost:8080','http://api.amp.localhost:9999'):
            self.assertFalse(P.safe_local_url(url),url)


class InstallerTests(unittest.TestCase):
    @staticmethod
    def base():
        # Shape proven by reconstructed v11/v14.2 startup; production installer
        # refuses when any anchor is missing instead of guessing.
        return ('''#!/usr/bin/env bash
set -Eeuo pipefail
cd "$ROOT"
export PATH="$HOME/.local/bin:$PATH"
need python3
need kubectl
if [[ "$ACTION" == check ]]; then
  need amctl
  python3 scripts/verify-aurora-tunnel-public.py
  # AURORA_V14_MCP_FORWARD_GUARD
  python3 scripts/aurora-mcp-forward-guard-v14.py ensure --gateway-check
  python3 scripts/aurora-full-reconcile.py check
  exit $?
fi
if [[ "$ACTION" == up ]]; then
  bash scripts/start-agent-manager.sh up
  need amctl
  if ! amctl context show >/dev/null 2>&1; then
    log 'Agent Manager OAuth login required; opening official browser login.'
    amctl login --url http://api.amp.localhost:8080
  fi
  log 'Stage 3/6: Validate real HTTPS MCP origin; recover expired Quick Tunnels.'
fi
if [[ "$ACTION" == agents ]]; then
  need git; need cloudflared
  log 'Preflight: Verify published agent source and generated build folders.'
fi
log 'Stage 5/6: Create, build, configure and deploy six managed agents.'
python3 scripts/aurora-full-reconcile.py agents
''')

    def test_all_6_gates_idempotent_and_old_weak_check_removed(self):
        result=I.transform_start(self.base())
        self.assertNotIn('if ! amctl context show',result)
        for marker in I.MARKS:self.assertEqual(result.count(marker),1)
        self.assertEqual(result.count('scripts/aurora-startup-preflight-v15.py auth'),5)
        self.assertEqual(I.transform_start(result),result)

    def test_dependency_runs_before_all_startup_work(self):
        result=I.transform_start(self.base())
        self.assertLess(result.index(I.MARK_DEP),result.index('if [[ "$ACTION" == check ]]'))
        self.assertLess(result.index(I.MARK_DEP),result.index('bash scripts/start-agent-manager.sh up'))
        self.assertLess(result.index(I.MARK_STAGE3),result.index("log 'Stage 3/6:"))

    def test_unrecognized_source_never_patched(self):
        with self.assertRaises(I.RuntimeError if hasattr(I,'RuntimeError') else RuntimeError):
            I.transform_start(self.base().replace('need python3\nneed kubectl','need python3\n# custom\nneed kubectl'))

    def test_partial_patch_refused(self):
        with self.assertRaises(RuntimeError):
            I.transform_start(self.base()+I.MARK_DEP+'\n')

    def test_bash_parser_accepts_transformed_script(self):
        result=I.transform_start(self.base())
        with tempfile.NamedTemporaryFile(mode='w',suffix='.sh') as f:
            f.write(result);f.flush()
            p=subprocess.run(['bash','-n',f.name],capture_output=True)
            self.assertEqual(p.returncode,0,p.stderr)

    def test_installer_preview_makes_no_changes(self):
        with tempfile.TemporaryDirectory(prefix='demo-banking-agent') as d:
            repo=Path(d)/'demo-banking-agent'
            (repo/'scripts').mkdir(parents=True)
            (repo/'README.md').write_text('# Banco Aurora\n')
            source=repo/'scripts'/'start-everything.sh'
            source.write_text(self.base())
            f=io.StringIO()
            with contextlib.redirect_stdout(f):
                I.install(repo,False)
            self.assertEqual(source.read_text(),self.base())
            self.assertFalse((repo/'scripts'/'aurora-startup-preflight-v15.py').exists())
            self.assertIn('READ-ONLY',f.getvalue())


if __name__=='__main__':unittest.main()
