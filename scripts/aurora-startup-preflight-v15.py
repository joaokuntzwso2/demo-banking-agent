#!/usr/bin/env python3
"""Banco Aurora/macOS preflight: zero infrastructure mutations.

`dependencies` checks the workstation before any startup work; `auth` verifies
an authenticated WSO2 API READ and performs one interactive login when the
session is genuinely missing or expired. Never prints secrets or CLI bodies.
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import re
import shutil
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
AMP_URL = 'http://api.amp.localhost:8080'
AMCTL_VERSION_RE = re.compile(r'(?<!\d)v?1\.0\.\d+(?!\d)')
REQUIRED_PROGRAMS = {
    'git': ('git', '--version'),
    'docker': ('docker', '--version'),
    'colima': ('colima', 'version'),
    'k3d': ('k3d', 'version'),
    'kubectl': ('kubectl', 'version', '--client'),
    'cloudflared': ('cloudflared', '--version'),
    'amctl': ('amctl', 'version'),
    'curl': ('curl', '--version'),
    'openssl': ('openssl', 'version'),
}
REQUIRED_UTILITIES = ('bash', 'lsof', 'nohup', 'ps', 'kill', 'grep', 'sed', 'awk', 'mktemp', 'tar', 'unzip')
REQUIRED_SOURCE = (
    'scripts/start-everything.sh', 'scripts/start-agent-manager.sh',
    'scripts/aurora-full-reconcile.py', 'scripts/sync-managed-agent-source.py',
    'scripts/publish-mcp-secure-tunnel.sh',
    'scripts/aurora-mcp-forward-guard-v14.py',
    'scripts/verify-aurora-agentid-denial.py',
)
MISSING_SESSION = re.compile(
    r'not logged in|not authenticated|login required|no active instance|'
    r'no configured instance|not configured|please log in|run [`\']?amctl login|'
    r'missing.*(?:token|credentials)|no current instance', re.I)
EXPIRED_SESSION = re.compile(
    r'invalid_grant|invalid refresh token|refresh token grant failed|'
    r'token (?:has )?expired|expired (?:access|refresh) token|'
    r'invalid (?:access|bearer) token|(?:http\s*)?401\b|unauthenticated|'
    r'(?:access|bearer) token.*invalid', re.I)


class PreflightError(Exception):
    pass


def run(argv, *, timeout=15, inherit=False):
    """Do not print args, stdout or stderr: they may contain sensitive data."""
    try:
        if inherit:
            return subprocess.run(argv, cwd=ROOT, timeout=timeout, check=False)
        return subprocess.run(argv, cwd=ROOT, capture_output=True, text=True,
                              timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise PreflightError(f'{argv[0]} unavailable or timed out ({type(exc).__name__})') from None


def classify_auth_failure(output):
    if EXPIRED_SESSION.search(output):
        return 'expired'
    if MISSING_SESSION.search(output):
        return 'missing'
    return 'other'


def safe_local_url(raw):
    try:
        url = urlsplit(raw.strip().rstrip('.,)\"\''))
        return url.scheme == 'http' and url.hostname == 'api.amp.localhost' \
            and url.port == 8080 and not url.username and not url.password
    except ValueError:
        return False


def local_context_status():
    """Return configured/missing/expired/foreign/ambiguous using read-only CLI."""
    p = run(['amctl', 'context', 'show'], timeout=15)
    data = (p.stdout or '') + '\n' + (p.stderr or '')
    if p.returncode:
        kind = classify_auth_failure(data)
        if kind != 'other':
            return kind
        raise PreflightError('Cannot inspect amctl context; inspect `amctl context show` locally')
    urls = re.findall(r'https?://[^\s\]}>\"\']+', data)
    if urls:
        return 'configured' if any(safe_local_url(u) for u in urls) and all(
            safe_local_url(u) for u in urls) else 'foreign'
    # Older formatting may show only a named active instance. Disambiguate by
    # asking the CLI's read-only inventory; never edit ~/.amctl/config.yaml.
    p2 = run(['amctl', 'context', 'instance', 'list'], timeout=15)
    if p2.returncode:
        return 'ambiguous'
    lines = [line for line in p2.stdout.splitlines() if 'api.amp.localhost' in line]
    active = [line for line in lines if re.search(r'(^\s*\*|\bactive\b|\bcurrent\b)', line, re.I)]
    if len(active) == 1 and AMP_URL in active[0]:
        return 'configured'
    if len(lines) == 1 and len(re.findall(r'https?://', p2.stdout)) == 1 and AMP_URL in lines[0]:
        return 'configured'
    return 'ambiguous'


def auth_probe():
    """An actual authenticated GET; unlike `context show`, exercises token refresh."""
    p = run(['amctl', 'api', '/orgs'], timeout=35)
    output = (p.stdout or '') + '\n' + (p.stderr or '')
    if p.returncode:
        return classify_auth_failure(output)
    try:
        value = json.loads(p.stdout)
    except (ValueError, TypeError):
        raise PreflightError('Agent Manager /orgs did not return JSON; endpoint not verified')
    if isinstance(value, dict) and value.get('status') == 'error':
        err = value.get('error') or {}
        code = str(err.get('code') or '') if isinstance(err, dict) else ''
        desc = str(err.get('message') or '') if isinstance(err, dict) else ''
        return classify_auth_failure(code + ' ' + desc)
    return 'valid'


def validate_dependency_versions(name, output):
    if name == 'amctl' and not AMCTL_VERSION_RE.search(output):
        return 'expected WSO2 Agent Manager amctl 1.0.x'
    return None


def check_dependencies():
    errors = []
    if sys.platform != 'darwin':
        errors.append('macOS required (detected non-Darwin operating system)')
    if platform.machine().lower() != 'arm64':
        errors.append('native Apple Silicon arm64 required (avoid Rosetta/x86_64 terminal)')
    if not ((3, 11) <= sys.version_info[:2] <= (3, 12)):
        errors.append('Python 3.11 or 3.12 required; selected python3 is '
                      + '.'.join(map(str, sys.version_info[:3])))
    for name, command in REQUIRED_PROGRAMS.items():
        if not shutil.which(name):
            errors.append(f'MISSING: {name}')
            continue
        try:
            p = run(command, timeout=12)
        except PreflightError:
            errors.append(f'UNUSABLE: {name} (version probe unavailable)')
            continue
        if p.returncode:
            errors.append(f'UNUSABLE: {name} (version probe exited {p.returncode})')
            continue
        msg = validate_dependency_versions(name, (p.stdout or '') + (p.stderr or ''))
        if msg:
            errors.append(f'UNSUPPORTED: {name}; {msg}')
    for name in REQUIRED_UTILITIES:
        if not shutil.which(name):
            errors.append(f'MISSING: {name} (macOS utility)')
    for args, desc in [(['python3', '-m', 'pip', '--version'], 'Python pip'),
                       (['python3', '-m', 'venv', '--help'], 'Python venv')]:
        if (args[0] == 'docker' and not shutil.which('docker')) or \
           (args[0] == 'python3' and not shutil.which('python3')):
            continue
        try:
            p = run(args, timeout=12)
            if p.returncode:
                errors.append(f'UNUSABLE: {desc}')
        except PreflightError:
            errors.append(f'UNUSABLE: {desc}')
    for name in REQUIRED_SOURCE:
        path = ROOT / name
        if not path.is_file() or path.is_symlink():
            errors.append(f'MISSING SOURCE: {name}')
    if errors:
        print('AURORA_PREFLIGHT=failed; no Colima/Kubernetes/tunnel actions started', file=sys.stderr)
        for problem in errors:
            print('  - ' + problem, file=sys.stderr)
        print('Install missing tools (Homebrew for macOS utilities; WSO2 amctl 1.0.x '
              'per official CLI instructions), select Python 3.11/3.12, then rerun startup.', file=sys.stderr)
        return 1
    print('AURORA_PREFLIGHT=verified; macOS arm64, Python, required CLI tools and source files')
    print('AURORA_PREFLIGHT_NOTE=Docker Compose/Buildx are optional for this k3d startup; daemon/cluster checked after Stage 1')
    return 0


def check_authentication():
    context = local_context_status()
    if context in ('foreign', 'ambiguous'):
        raise PreflightError(
            'amctl active instance is not verified as the local http://api.amp.localhost:8080; '
            'inspect `amctl context show` / `amctl context instance list` before changing it')
    verdict = 'missing' if context == 'missing' else ('expired' if context == 'expired' else auth_probe())
    if verdict == 'valid':
        print('AURORA_AMCTL_AUTH=verified; authenticated read of local Agent Manager succeeded')
        return 0
    if verdict not in ('missing', 'expired'):
        raise PreflightError(
            'Agent Manager authenticated GET failed for a non-authentication reason '
            '(such as HTTP 403/404/500 or network failure). No login or proxy changes attempted; '
            'check Agent Manager API health and amctl locally')
    if not sys.stdin.isatty():
        raise PreflightError(
            'amctl session missing/expired but stdin is not interactive. '
            'Use a terminal to run `amctl login --url http://api.amp.localhost:8080`, '
            'then rerun startup; CI requires separately provisioned credentials')
    print('AURORA_AMCTL_AUTH=renewal_required; opening official interactive OAuth login', flush=True)
    login = run(['amctl', 'login', '--url', AMP_URL], timeout=300, inherit=True)
    if login.returncode:
        raise PreflightError('amctl browser login was not completed; try `amctl login --url '
                             + AMP_URL + '` in this terminal')
    if local_context_status() != 'configured' or auth_probe() != 'valid':
        raise PreflightError('amctl login finished but authenticated local /orgs read is not verified')
    print('AURORA_AMCTL_AUTH=renewed_and_verified')
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('mode', choices=('dependencies', 'auth'))
    ap.add_argument('--action', choices=('up', 'agents', 'check'), default='up')
    args = ap.parse_args()
    try:
        return check_dependencies() if args.mode == 'dependencies' else check_authentication()
    except PreflightError as exc:
        print('AURORA_PREFLIGHT=failed: ' + str(exc), file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
