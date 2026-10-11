#!/usr/bin/env python3
"""Offline, read-only prerequisites gate for Banco Aurora macOS startup.

Runs BEFORE Agent Manager/Colima/Kubernetes/tunnel operations. Never installs,
logs into services, contacts the network, or touches application state.
Host Python 3.11/3.12 is supported by the tested workstation; this is distinct
from managed runtime/debug container Python versions.
"""
import argparse
import importlib.util
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
PY_SUPPORTED = ((3, 11), (3, 12))
TOOLS = (
    ('git', ('git', '--version'), 'brew install git'),
    ('docker', ('docker', '--version'), 'Install Docker CLI and Docker Engine for Colima'),
    ('docker compose', ('docker', 'compose', 'version', '--short'), 'Install the Docker Compose v2 CLI plugin'),
    ('docker buildx', ('docker', 'buildx', 'version'), 'Install the Docker Buildx CLI plugin'),
    ('colima', ('colima', 'version'), 'brew install colima'),
    ('k3d', ('k3d', 'version'), 'brew install k3d'),
    ('kubectl', ('kubectl', 'version', '--client', '-o', 'json'), 'brew install kubectl'),
    ('cloudflared', ('cloudflared', '--version'), 'brew install cloudflared'),
    ('amctl', ('amctl', 'version'), 'Install the WSO2 Agent Manager v1.0.0 matching amctl CLI'),
    ('curl', ('curl', '--version'), 'Use the macOS curl command or install it with Homebrew'),
    ('openssl', ('openssl', 'version'), 'Install OpenSSL/LibreSSL and put it on PATH'),
    ('lsof', ('lsof', '-v'), 'Install the macOS lsof utility'),
)
# Commands used by local shell process management; they do not all have safe
# cross-version --version flags (macOS/BSD utilities are particularly variable).
COMMAND_ONLY = (
    'bash', 'sh', 'ps', 'nohup', 'kill', 'mktemp', 'base64', 'tar',
    'awk', 'head', 'grep', 'sed', 'uname', 'date', 'cp', 'mkdir',
    'rm', 'sleep', 'chmod',
)
REQUIRED_FILES = (
    'scripts/start-everything.sh',
    'scripts/start-agent-manager.sh',
    'scripts/publish-mcp-secure-tunnel.sh',
    'scripts/aurora-full-reconcile.py',
    'scripts/sync-managed-agent-source.py',
    'scripts/verify-aurora-tunnel-public.py',
    'scripts/verify-aurora-agentid-denial.py',
    'scripts/aurora-mcp-forward-guard-v14.py',
    'agent-manager-support/start.sh',
    'agent-manager-support/support.env.example',
    'requirements.txt',
    'Dockerfile',
)


def probe(args, timeout=8):
    """Capture only; do not expose potentially sensitive subprocess output."""
    try:
        p = subprocess.run(list(args), capture_output=True, text=True, timeout=timeout,
                           stdin=subprocess.DEVNULL)
        return p.returncode, (p.stdout + '\n' + p.stderr)[:4096]
    except (OSError, subprocess.TimeoutExpired, UnicodeError):
        return -1, ''


def version_of(text):
    match = re.search(r'(?<![0-9])v?(\d+)\.(\d+)(?:\.(\d+))?(?![0-9])', text)
    return '.'.join(x for x in match.groups() if x is not None) if match else ''


class Check:
    def __init__(self):
        self.passed = []
        self.failed = []

    def ok(self, name, version=''):
        self.passed.append(name)
        print('[aurora-preflight] OK   ' + name + (f' ({version})' if version else ''))

    def fail(self, name, kind, detail, hint):
        self.failed.append(name)
        print(f'[aurora-preflight] {kind} {name}: {detail}', file=sys.stderr)
        print(f'[aurora-preflight]       Fix: {hint}', file=sys.stderr)

    def tool(self, name, argv, hint):
        executable = argv[0]
        if not shutil.which(executable):
            self.fail(name, 'MISSING', 'executable not on PATH', hint)
            return
        code, output = probe(argv)
        if code != 0:
            self.fail(name, 'UNUSABLE', 'installed but offline version command failed or timed out', hint)
            return
        if name == 'lsof':
            # On macOS, `lsof -v` writes its version to stderr. Do not impose a
            # Linux-specific output contract on the BSD utility.
            self.ok(name)
            return
        version = version_of(output)
        if not version:
            self.fail(name, 'UNUSABLE', 'could not identify installed version', hint)
            return
        if name == 'amctl':
            match = re.search(r'(?im)^amctl\s+version\s+v?(\d+\.\d+\.\d+)(?![0-9])', output)
            if not match:
                self.fail(name, 'UNUSABLE', 'unrecognized amctl version output', hint)
                return
            version = match.group(1)
        if name == 'amctl' and version != '1.0.0':
            self.fail(name, 'INCOMPATIBLE', f'version {version}; this deployment requires 1.0.0', hint)
            return
        if name == 'docker compose':
            major = int(version.split('.')[0])
            if major < 2:
                self.fail(name, 'INCOMPATIBLE', f'version {version}; Compose v2 required', hint)
                return
        if name == 'k3d' and int(version.split('.')[0]) < 5:
            self.fail(name, 'INCOMPATIBLE', f'version {version}; k3d v5 or later required', hint)
            return
        self.ok(name, version)

    def run(self, action='up', root=ROOT):
        if platform.system() != 'Darwin':
            self.fail('macOS', 'INCOMPATIBLE', 'this launcher targets macOS', 'Run on a supported Mac')
        else:
            self.ok('macOS', platform.machine())

        py = sys.version_info[:2]
        if py not in PY_SUPPORTED:
            self.fail('python3', 'INCOMPATIBLE',
                      f'active interpreter {sys.version_info.major}.{sys.version_info.minor}; tested host versions are 3.11 and 3.12',
                      'Install Python 3.11/3.12 and select it using PATH or a virtual environment')
        else:
            self.ok('python3', '.'.join(str(x) for x in sys.version_info[:3]))
            # These are used to create/maintain the E2E isolated environment.
            for module in ('ssl', 'venv', 'ensurepip'):
                if importlib.util.find_spec(module) is None:
                    self.fail('python3:' + module, 'MISSING', 'standard-library module not installed',
                              'Install a complete Python 3.11/3.12 distribution')
                else:
                    self.ok('python3:' + module)
            code, _ = probe((sys.executable, '-c', 'import ssl, venv, ensurepip'))
            if code:
                self.fail('python3:standard-library', 'UNUSABLE',
                          'SSL, venv or ensurepip could not be imported',
                          'Install a complete compatible Python distribution')
            else:
                self.ok('python3:standard-library')
            code, _ = probe((sys.executable, '-m', 'pip', '--version'))
            if code:
                self.fail('python3:pip', 'UNUSABLE', 'pip not available for active Python',
                          'Provision pip for the selected Python interpreter')
            else:
                self.ok('python3:pip')
        if action == 'status':
            # Status is a diagnostic and must remain available with a broken
            # toolchain; the full gate applies to commands that execute work.
            print('[aurora-preflight] STATUS: only platform/Python assessed; full toolchain gate applies to up/agents/check')
            return not self.failed

        for name in COMMAND_ONLY:
            if shutil.which(name):
                self.ok(name)
            else:
                self.fail(name, 'MISSING', 'executable not on PATH',
                          'Install or restore this macOS system command on PATH')
        for name, argv, hint in TOOLS:
            self.tool(name, argv, hint)
        for rel in REQUIRED_FILES:
            path = root / rel
            if not path.is_file() or path.is_symlink():
                self.fail('repository:' + rel, 'MISSING', 'required source file absent or is a symlink',
                          'Restore the tracked file from the reviewed Banco Aurora repository')
        if self.failed:
            print(f'[aurora-preflight] FAILED: {len(self.failed)} requirement(s) not satisfied; '
                  'no startup operations attempted', file=sys.stderr)
            return False
        print(f'[aurora-preflight] VERIFIED: {len(self.passed)} offline checks passed; '
              'runtime/network/credentials are validated by the later startup gates')
        return True


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--action', choices=('up', 'agents', 'check', 'status'), default='up')
    args = parser.parse_args(argv)
    return 0 if Check().run(args.action) else 1


if __name__ == '__main__':
    sys.exit(main())
