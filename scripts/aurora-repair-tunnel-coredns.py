#!/usr/bin/env python3
"""Reversible stub-zone repair for dedicated Banco Aurora k3d DNS ONLY.

Adds trycloudflare.com forwarding to Cloudflare recursive DNS; does not change
cluster.local resolution, alter an existing forwarding policy, or bypass TLS,
SSRF filters, or gateway auth. Caller must prove target NXDOMAIN discrepancy.
"""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import subprocess
import sys

ZONE = 'trycloudflare.com'
CONTEXT = 'k3d-amp-local'
BLOCK = """
# BEGIN AURORA DEMO QUICK TUNNEL DNS (managed, reversible)
trycloudflare.com:53 {
    errors
    cache 10
    forward . 1.1.1.1 1.0.0.1
}
# END AURORA DEMO QUICK TUNNEL DNS
"""


def validate_corefile(corefile):
    if not isinstance(corefile, str) or not corefile.strip():
        raise ValueError('CoreDNS Corefile is missing')
    if BLOCK.strip() in corefile:
        return corefile, False
    if re.search(r'(?m)^\s*trycloudflare\.com(?::53)?\s*\{', corefile):
        raise ValueError('Existing trycloudflare.com DNS zone is not Aurora-managed; refusing overwrite')
    if ('BEGIN AURORA DEMO QUICK TUNNEL DNS' in corefile
            or 'END AURORA DEMO QUICK TUNNEL DNS' in corefile):
        raise ValueError('Aurora-managed DNS block was customized; refusing overwrite')
    if not re.search(r'(?m)^\s*\.\s*:\s*53\s*\{', corefile):
        raise ValueError('Unexpected CoreDNS default-zone configuration; refusing change')
    if not re.search(r'(?m)^\s*reload(?:\s|$)', corefile):
        raise ValueError('No CoreDNS reload plugin; refusing live change')
    return corefile.rstrip() + '\n' + BLOCK, True


def k(*args, timeout=30):
    try:
        p = subprocess.run(['kubectl', '--context', CONTEXT, '-n', 'kube-system',
                            *args], capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError('Unable to access dedicated Kubernetes cluster: '+str(exc)) from exc
    if p.returncode:
        raise RuntimeError('kubectl '+args[0]+' failed: '+(p.stderr or p.stdout).strip()[-800:])
    return p.stdout


def read_current():
    item=json.loads(k('get','configmap','coredns','-o','json'))
    if item.get('metadata',{}).get('name') != 'coredns' or item.get('metadata',{}).get('namespace') != 'kube-system':
        raise RuntimeError('Unexpected CoreDNS ConfigMap identity')
    corefile=item.get('data',{}).get('Corefile')
    if not isinstance(corefile,str):
        raise RuntimeError('CoreDNS ConfigMap lacks data.Corefile')
    return corefile


def patch_corefile(old, new):
    # JSON Patch 'test' guards against overwriting an administrator's edits.
    payload=[{'op':'test','path':'/data/Corefile','value':old},
             {'op':'replace','path':'/data/Corefile','value':new}]
    k('patch','configmap','coredns','--type=json', '-p',json.dumps(payload))


def backup(original):
    base=Path.home()/'Downloads'/'aurora-startup-backups'
    base.mkdir(mode=0o700,parents=True,exist_ok=True)
    name='coredns-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-'+str(os.getpid())+'.corefile'
    backup_path=base/name
    fd=os.open(backup_path, os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    with os.fdopen(fd,'w') as f:
        f.write(original)
    return backup_path


def main():
    p=argparse.ArgumentParser()
    p.add_argument('--context',required=True)
    choice=p.add_mutually_exclusive_group(required=True)
    choice.add_argument('--host')
    choice.add_argument('--restore-backup',type=Path)
    a=p.parse_args()
    if a.context!=CONTEXT:
        raise RuntimeError('DNS auto-repair is restricted to k3d-amp-local; no changes made')
    if a.restore_backup is not None:
        saved=a.restore_backup.resolve(strict=True)
        root=(Path.home()/'Downloads'/'aurora-startup-backups').resolve()
        if root not in saved.parents or not saved.name.startswith('coredns-') or saved.suffix!='.corefile':
            raise RuntimeError('Restore requires Aurora CoreDNS backup in ~/Downloads/aurora-startup-backups')
        original=saved.read_text()
        expected,changed=validate_corefile(original)
        if not changed:
            raise RuntimeError('Backup already contains the repair or uses an unsupported Corefile')
        live=read_current()
        if live!=expected:
            raise RuntimeError('Live CoreDNS differs from the exact patched backup; refusing restore')
        patch_corefile(live,original)
        print('AURORA_DNS_ZONE=restored_original')
        return
    if not re.fullmatch(r'[a-z0-9-]{1,62}\.trycloudflare\.com',a.host):
        raise RuntimeError('Not a valid single-label Cloudflare Quick Tunnel hostname')
    original=read_current()
    patched,changed=validate_corefile(original)
    if not changed:
        print('AURORA_DNS_ZONE=already_configured; no CoreDNS change')
        return
    saved=backup(original)
    try:
        patch_corefile(original,patched)
    except Exception as exc:
        raise RuntimeError('CoreDNS patch did not succeed; original backup: '+str(saved)+'. '+str(exc)) from exc
    print('AURORA_DNS_ZONE=patched_only_trycloudflare.com')
    print('AURORA_DNS_BACKUP='+str(saved))
    print('CoreDNS ConfigMap reloads automatically; publisher will verify normal cluster DNS.')

if __name__=='__main__':
    try:
        main()
    except (ValueError,RuntimeError,KeyError,json.JSONDecodeError) as e:
        print('ERROR: '+str(e),file=sys.stderr)
        sys.exit(1)
