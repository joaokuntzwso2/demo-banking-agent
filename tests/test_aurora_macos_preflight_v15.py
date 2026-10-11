"""Offline v15 macOS preflight + guarded installer contracts."""
import contextlib
import importlib.util
import io
from pathlib import Path
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    obj = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(obj)
    return obj


pre = load(ROOT/'scripts/aurora-macos-preflight-v15.py', 'aurora_v15_preflight')
installer = load(ROOT/'scripts/apply-aurora-macos-preflight-v15.py', 'aurora_v15_installer')


class Version:
    def __init__(self, maj, minor, micro=0):
        self.major, self.minor, self.micro = maj, minor, micro
    def __getitem__(self, key):
        return (self.major, self.minor, self.micro)[key]


class PreflightTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo = Path(self.tmp.name)
        for name in pre.REQUIRED_FILES:
            f = self.repo/name
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text('fixture\n')

    def check(self, version=(3,11), platform='Darwin', missing=(), failing=(), overrides=None):
        overrides = overrides or {}
        missing=set(missing)
        failing=set(failing)
        commands=[]
        def which(name):
            return None if name in missing else '/mock/bin/'+name
        def probe(argv, timeout=8):
            commands.append(tuple(argv))
            name = ' '.join(argv)
            if name in failing:
                return 7, 'SENSITIVE_TOKEN_SUPPRESSED'
            if name in overrides:
                return 0, overrides[name]
            if 'amctl' in argv[0]:return 0, 'amctl version 1.0.0 (commit 123)'
            if 'pip' in argv:return 0, 'pip 24.1 from /tmp/mock'
            if 'compose' in argv:return 0, 'Docker Compose version v2.32.1'
            if 'buildx' in argv:return 0, 'github.com/docker/buildx v0.18.0'
            if 'colima' in argv:return 0, 'colima version 0.8.1'
            if 'cloudflared' in argv:return 0, 'cloudflared version 2026.10.1'
            if 'k3d' in argv:return 0, 'k3d version v5.8.1\nk3s version v1.31.2'
            if 'kubectl' in argv:return 0, '{"clientVersion": {"gitVersion": "v1.32.1"}}'
            if 'docker' in argv:return 0, 'Docker version 27.0.1'
            if 'git' in argv:return 0, 'git version 2.48.1'
            if 'curl' in argv:return 0, 'curl 8.7.1'
            if 'openssl' in argv:return 0, 'LibreSSL 3.3.6'
            if 'lsof' in argv:return 0, 'lsof version 4.99'
            return 0, 'v1.2.3'
        out,err=io.StringIO(),io.StringIO()
        with mock.patch.object(pre.platform, 'system', return_value=platform), \
             mock.patch.object(pre.platform, 'machine', return_value='arm64'), \
             mock.patch.object(pre.sys, 'version_info', Version(*version)), \
             mock.patch.object(pre.shutil, 'which', side_effect=which), \
             mock.patch.object(pre, 'probe', side_effect=probe), \
             contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            success=pre.Check().run('up',self.repo)
        return success,out.getvalue(),err.getvalue(),commands

    def test_mac_python311_and_amctl100_pass(self):
        ok,out,err,cmds=self.check()
        self.assertTrue(ok,err)
        self.assertIn('VERIFIED:',out)
        self.assertIn(('docker','compose','version','--short'),cmds)
        self.assertIn(('docker','buildx','version'),cmds)
        self.assertNotIn('SENSITIVE_TOKEN_SUPPRESSED',out+err)

    def test_python312_supported(self):
        ok,*_=self.check(version=(3,12))
        self.assertTrue(ok)

    def test_python310_rejected_without_auto_install(self):
        ok,out,err,_=self.check(version=(3,10))
        self.assertFalse(ok)
        self.assertIn('INCOMPATIBLE python3',err)
        self.assertIn('no startup operations attempted',err)

    def test_untested_python313_rejected(self):
        ok,out,err,_=self.check(version=(3,13))
        self.assertFalse(ok)
        self.assertIn('INCOMPATIBLE python3',err)

    def test_non_mac_refused(self):
        ok,out,err,_=self.check(platform='Linux')
        self.assertFalse(ok)
        self.assertIn('INCOMPATIBLE macOS',err)

    def test_all_missing_dependencies_reported_together(self):
        ok,out,err,_=self.check(missing=('docker','kubectl','amctl','cloudflared'))
        self.assertFalse(ok)
        for name in ('docker', 'kubectl', 'amctl', 'cloudflared'):
            self.assertIn('MISSING '+name,err)
        self.assertIn('FAILED:',err)

    def test_amctl_must_match100(self):
        ok,out,err,_=self.check(overrides={'amctl version':'amctl version 1.1.0'})
        self.assertFalse(ok)
        self.assertIn('INCOMPATIBLE amctl: version 1.1.0',err)

    def test_amctl_unrecognized_output_refused(self):
        ok,out,err,_=self.check(overrides={'amctl version': 'Version 1.0.0 might be available'})
        self.assertFalse(ok)
        self.assertIn('UNUSABLE amctl',err)

    def test_docker_compose_v1_refused(self):
        ok,out,err,_=self.check(overrides={'docker compose version --short':'1.29.2'})
        self.assertFalse(ok)
        self.assertIn('Compose v2 required',err)

    def test_broken_cli_no_raw_stderr_leak(self):
        ok,out,err,_=self.check(failing=('cloudflared --version',))
        self.assertFalse(ok)
        self.assertIn('UNUSABLE cloudflared',err)
        self.assertNotIn('SENSITIVE_TOKEN_SUPPRESSED',out+err)

    def test_missing_script_blocks_preflight(self):
        (self.repo/pre.REQUIRED_FILES[-1]).unlink()
        ok,out,err,_=self.check()
        self.assertFalse(ok)
        self.assertIn('repository:Dockerfile',err)

    def test_no_runtime_network_or_mutating_subprocess(self):
        ok,out,err,cmds=self.check()
        self.assertTrue(ok)
        for argv in cmds:
            command=' '.join(argv)
            for forbidden in (' cluster start', 'cluster create', ' login ', ' apply ', ' install ',
                              ' curl http', 'agent build', 'down -v', 'up -d'):
                self.assertNotIn(forbidden, command)

    def test_bad_version_not_accepted(self):
        ok,out,err,_=self.check(overrides={'kubectl version --client -o json':'[REDACTED] invalid'})
        self.assertFalse(ok)
        self.assertIn('UNUSABLE kubectl',err)


class InstallerTests(unittest.TestCase):
    def fixture(self):
        return '''#!/usr/bin/env bash
set -Eeuo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ACTION="${1:-up}"
die(){ exit 1; }
cd "$ROOT"
export PATH="$HOME/.local/bin:$PATH"
need python3
need kubectl
# AURORA_V14_MCP_FORWARD_GUARD
log 'Stage 0/6: Verify source'
python3 scripts/aurora-full-reconcile.py check
'''

    def test_injection_is_before_any_existing_toolchecks_or_startup(self):
        result=installer.transform_start(self.fixture())
        self.assertLess(result.index(installer.MARK),result.index('need python3'))
        self.assertLess(result.index(installer.MARK),result.index('Stage 0/6'))
        self.assertIn(' --action "$ACTION"',result)
        self.assertIn('no startup operations attempted',result)

    def test_injection_idempotent(self):
        first=installer.transform_start(self.fixture())
        self.assertEqual(installer.transform_start(first),first)

    def test_unknown_startup_source_refused(self):
        with self.assertRaisesRegex(RuntimeError, 'anchor'):
            installer.transform_start(self.fixture().replace('export PATH="$HOME/.local/bin:$PATH"','export PATH=/unknown'))

    def test_broken_old_security_guard_refused(self):
        with self.assertRaisesRegex(RuntimeError, 'v14 MCP security gates'):
            installer.transform_start(self.fixture().replace('AURORA_V14_MCP_FORWARD_GUARD','REMOVED'))

    def test_partial_injection_refused(self):
        src=self.fixture().replace('need python3',installer.MARK+'\nneed python3')
        with self.assertRaisesRegex(RuntimeError,'Partial'):
            installer.transform_start(src)


class EndToEndInstallerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo = Path(self.tmp.name)/'demo-banking-agent'
        (self.repo/'scripts').mkdir(parents=True)
        (self.repo/'tests').mkdir()
        (self.repo/'agent-manager-support').mkdir()
        source = InstallerTests().fixture()
        (self.repo/'scripts/start-everything.sh').write_text(source)
        (self.repo/'README.md').write_text('# Banco Aurora\n')

    def test_preview_is_pure_and_apply_is_idempotent(self):
        before=(self.repo/'scripts/start-everything.sh').read_text()
        out=io.StringIO()
        with contextlib.redirect_stdout(out):
            installer.install(self.repo, False)
        self.assertEqual(before,(self.repo/'scripts/start-everything.sh').read_text())
        self.assertFalse((self.repo/'scripts/aurora-macos-preflight-v15.py').exists())
        with contextlib.redirect_stdout(io.StringIO()):
            installer.install(self.repo, True)
        saved=(self.repo/'scripts/start-everything.sh').read_text()
        self.assertEqual(saved.count(installer.MARK),1)
        self.assertTrue((self.repo/'tests/test_aurora_macos_preflight_v15.py').exists())
        with contextlib.redirect_stdout(io.StringIO()):
            installer.install(self.repo, True)
        self.assertEqual(saved,(self.repo/'scripts/start-everything.sh').read_text())

    def test_symlink_target_refused_before_modification(self):
        target=self.repo/'scripts/aurora-macos-preflight-v15.py'
        target.symlink_to(self.repo/'README.md')
        before=(self.repo/'scripts/start-everything.sh').read_text()
        with self.assertRaisesRegex(RuntimeError,'symlink'):
            installer.plan(self.repo)
        self.assertEqual(before,(self.repo/'scripts/start-everything.sh').read_text())


if __name__ == '__main__':
    unittest.main()
