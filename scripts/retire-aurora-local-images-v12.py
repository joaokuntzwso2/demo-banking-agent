#!/usr/bin/env python3
"""Read-only by default: refuse deletion of in-use Aurora support image tags."""
from __future__ import annotations
import argparse
import datetime
import os
from pathlib import Path
import json
import re
import subprocess
import sys

SUPPORT_RE=re.compile(r'^(?:[a-z0-9._-]+(?::[0-9]+)?/)*aurora-banking-support:[A-Za-z0-9_.-]+$')

def cmd(args,required=False):
    r=subprocess.run(args,text=True,capture_output=True,check=False)
    if required and r.returncode:
        raise RuntimeError('Command failed (output withheld): '+' '.join(args[:2]))
    return r

def inspect(tag):
    result=cmd(['docker','image','inspect',tag,'--format','{{.Id}}'],True)
    digest=result.stdout.strip()
    if not digest.startswith('sha256:'):
        raise RuntimeError('Docker did not report a valid image ID')
    return digest

def candidates():
    out=cmd(['docker','image','ls','--format','{{.Repository}}:{{.Tag}}'],True)
    return sorted({s for s in out.stdout.splitlines() if SUPPORT_RE.fullmatch(s)})

def users(tag,digest):
    docker=cmd(['docker','ps','-aq','--filter','ancestor='+tag],True)
    docker_uses=[s for s in docker.stdout.splitlines() if s.strip()]
    # Any Kubernetes query failure is a hard stop for destructive mode.
    ctx=cmd(['kubectl','config','current-context'],True).stdout.strip()
    if ctx!='k3d-amp-local':
        raise RuntimeError('Not in expected k3d-amp-local Kubernetes context; refuse to infer image safety')
    pods=cmd(['kubectl','get','pods','-A','-o','json'],True)
    data=json.loads(pods.stdout)
    k8s=[]
    for pod in data.get('items',[]):
        spec=pod.get('spec',{})
        containers=spec.get('containers',[])+spec.get('initContainers',[])+spec.get('ephemeralContainers',[])
        statuses=pod.get('status',{}).get('containerStatuses',[])
        for c in containers:
            if c.get('image')==tag:
                k8s.append((pod.get('metadata',{}).get('namespace'),pod.get('metadata',{}).get('name')))
        for c in statuses:
            image_id=c.get('imageID','')
            if digest in image_id:
                k8s.append((pod.get('metadata',{}).get('namespace'),pod.get('metadata',{}).get('name')))
    return docker_uses, sorted(set(k8s))

def record_baseline():
    entries=[{'tag':tag,'id':inspect(tag)} for tag in candidates()]
    if not entries:
        print('No local Aurora support images found; nothing to baseline.')
        return
    folder=Path.home()/'Downloads'
    folder.mkdir(parents=True,exist_ok=True)
    dest=folder/('aurora-image-baseline-'+datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'.json')
    fd=os.open(dest,os.O_CREAT | os.O_EXCL | os.O_WRONLY,0o600)
    with os.fdopen(fd,'w') as out:
        json.dump({'version':1,'images':entries},out,indent=2)
        out.write('\n')
    print('BASELINE_RECORDED:',dest,'(private local file; never commit it)')
    print('Record is evidence of OLD local image IDs; do not remove them until safe replacement is verified.')


def retire_baseline(filename, execute, confirmed):
    file=Path(filename).expanduser()
    if not file.is_file() or file.is_symlink():
        raise RuntimeError('Missing baseline file or unsafe symlink')
    data=json.loads(file.read_text())
    if data.get('version')!=1 or not isinstance(data.get('images'),list):
        raise RuntimeError('Unknown baseline format')
    for entry in data['images']:
        if not isinstance(entry,dict):
            raise RuntimeError('Malformed baseline entry')
        tag=entry.get('tag','')
        digest=entry.get('id','')
        if not SUPPORT_RE.fullmatch(tag) or not re.fullmatch(r'sha256:[0-9a-f]{64}',digest):
            raise RuntimeError('Baseline includes an ineligible tag or image ID')
        old=cmd(['docker','image','inspect',digest,'--format','{{.Id}}'])
        if old.returncode:
            print('OLD_IMAGE_ALREADY_ABSENT:',tag,digest[:19]+'…')
            continue
        current=cmd(['docker','image','inspect',tag,'--format','{{.Id}}'])
        if current.returncode==0 and current.stdout.strip()==digest:
            raise RuntimeError('Original vulnerable image is still the current tag: '+tag)
        dc=cmd(['docker','ps','-aq','--filter','ancestor='+digest],True)
        if dc.stdout.strip():
            raise RuntimeError('Original image still used by one or more Docker containers: '+tag)
        # Kubernetes imageID may refer to containerd's content digest, not Docker's
        # local image ID. If any workload references the tag, refuse to assume safety.
        ctx=cmd(['kubectl','config','current-context'],True).stdout.strip()
        if ctx!='k3d-amp-local':
            raise RuntimeError('Unexpected Kubernetes context during old-image check')
        pods=json.loads(cmd(['kubectl','get','pods','-A','-o','json'],True).stdout)
        for pod in pods.get('items',[]):
            spec=pod.get('spec',{})
            references=[c.get('image') for k in ('containers','initContainers','ephemeralContainers') for c in spec.get(k,[])]
            ids=[c.get('imageID','') for c in pod.get('status',{}).get('containerStatuses',[])]
            if digest in ' '.join(ids):
                raise RuntimeError('A Kubernetes Pod still reports the old image digest: '+tag)
            if tag in references:
                # Fail closed: k3d containerd image digests are not always equal
                # to Docker engine IDs and cannot be inferred solely from a tag.
                raise RuntimeError('Kubernetes still references this tag; verify per-Pod imageID and retire manually after rollout: '+tag)
        if not execute:
            print('DRY_RUN_OLD_IMAGE_UNUSED:',tag,digest[:19]+'…')
            continue
        if not confirmed:
            raise RuntimeError('--execute requires --confirm-retire')
        removed=cmd(['docker','image','rm',digest])
        if removed.returncode:
            raise RuntimeError('Docker refused to remove the exact old digest without force; inspect local references')
        print('RETIRED_OLD_DOCKER_IMAGE:',tag,digest[:19]+'…')
    print('Note: Docker retirement does not purge k3d containerd caches, registries, or backups.')


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--image',help='Exact Aurora support Docker image tag to inspect')
    ap.add_argument('--record-baseline',action='store_true',help='Record current local Aurora support image IDs to private Downloads file')
    ap.add_argument('--baseline',help='Inspect/retire recorded OLD IDs after a clean rebuild and rollout')
    ap.add_argument('--execute',action='store_true',help='Actually run docker image rm after safety checks')
    ap.add_argument('--confirm-retire',action='store_true',help='Explicit acknowledgment image should be retired')
    a=ap.parse_args()
    try:
        if a.record_baseline:
            if a.image or a.baseline or a.execute: raise RuntimeError('Use --record-baseline alone')
            record_baseline()
            return
        if a.baseline:
            if a.image: raise RuntimeError('Use --baseline independently of --image')
            retire_baseline(a.baseline,a.execute,a.confirm_retire)
            return
        if not a.image:
            print('AURORA_SUPPORT_IMAGES (read-only):')
            for tag in candidates(): print(' ',tag)
            print('Specify --image EXACT_TAG for safe user/POD checks; no changes performed.')
            return
        if not SUPPORT_RE.fullmatch(a.image):
            raise RuntimeError('Only explicit aurora-banking-support:<tag> images are eligible; no wildcards or global pruning')
        digest=inspect(a.image)
        dc,k8s=users(a.image,digest)
        print('Candidate:',a.image,'digest:',digest[:19]+'…')
        print('Docker container users:',len(dc),'Kubernetes Pod users:',len(k8s))
        for namespace,pod in k8s:print(' IN_USE_K8S:',namespace+'/'+pod)
        if dc or k8s:
            print('REFUSED: image still referenced; rebuild/redeploy and verify new image digests before retiring it')
            sys.exit(3)
        if not a.execute:
            print('DRY RUN: image unused in Docker containers and active k3d Pods; re-run with --execute --confirm-retire to untag/delete locally.')
            return
        if not a.confirm_retire:
            raise RuntimeError('--execute requires --confirm-retire')
        r=cmd(['docker','image','rm',a.image])
        if r.returncode:
            raise RuntimeError('Docker refused safe image removal; no force used. Resolve references before retrying.')
        print('LOCAL_IMAGE_RETIRE=successful; no guarantee of removal from registries/k3d containerd layers/backups')
    except (RuntimeError,ValueError,json.JSONDecodeError,OSError) as exc:
        print('SAFE REFUSAL:',exc,file=sys.stderr)
        sys.exit(2)
if __name__=='__main__':main()
