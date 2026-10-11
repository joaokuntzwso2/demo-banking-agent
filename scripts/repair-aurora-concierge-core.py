#!/usr/bin/env python3
"""Conservative diagnosis / repair of Concierge's Banking Core URL.

Read-only by default. --apply uses the existing reconciler to select a verified
completed build, merge only the non-secret Core URL and runtime PORT, and await
Agent Manager active + Kubernetes Ready. Never accesses private keys or rotates
credentials. Refuses to override a configuration it cannot understand.
"""
import argparse
import base64
import binascii
import importlib.util
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parent.parent
CTX = 'k3d-amp-local'
CORE_NS = 'aurora-demo'
CORE_SVC = 'aurora-core'
CONCIERGE_NS = 'dp-default-banco-aurora-default-990774f5'
CORE_URL = 'http://aurora-core.aurora-demo.svc.cluster.local:8100'


def query(namespace, kind, name=None, *, selector=None):
    args=['kubectl','--context',CTX,'-n',namespace,'get',kind]
    if name:
        args.append(name)
    if selector:
        args.extend(['-l',selector])
    args.extend(['-o','json'])
    result=subprocess.run(args, text=True, capture_output=True, timeout=30, cwd=ROOT)
    if result.returncode:
        raise RuntimeError('Cannot read Kubernetes '+kind+' in '+namespace+' (no mutations performed)')
    try:
        out=json.loads(result.stdout)
    except ValueError:
        raise RuntimeError('Unexpected Kubernetes JSON for '+kind)
    if not isinstance(out,dict):
        raise RuntimeError('Expected a Kubernetes JSON object')
    return out


def core_service_ready(service, slices):
    ports=service.get('spec',{}).get('ports',[])
    good=[p for p in ports if p.get('port') == 8100 and p.get('targetPort') in (8100,'8100')]
    if len(good) != 1 or not service.get('spec',{}).get('selector'):
        raise RuntimeError('Banking Core Service port/targetPort/selector is not verified; no deployment changes')
    items=slices.get('items',[])
    if not isinstance(items,list):
        raise RuntimeError('Invalid Core EndpointSlice inventory')
    ready=sum(1 for obj in items
              for ep in obj.get('endpoints',[])
              if ep.get('addresses') and ep.get('conditions',{}).get('ready') is not False)
    if ready < 1:
        raise RuntimeError('Banking Core Service has no ready EndpointSlice address; fix Core before deploying Concierge')
    return ready


def select_concierge(pods):
    items=pods.get('items',[])
    if not isinstance(items,list):
        raise RuntimeError('Unexpected Concierge pod inventory')
    matches=[]
    for pod in items:
        meta=pod.get('metadata',{})
        if not meta.get('name','').startswith('aurora-concierge-') or meta.get('deletionTimestamp'):
            continue
        if pod.get('status',{}).get('phase') != 'Running':
            continue
        containers=pod.get('spec',{}).get('containers',[])
        main=[c for c in containers if c.get('name')=='main']
        statuses=pod.get('status',{}).get('containerStatuses',[])
        if len(main)==1 and any(c.get('name')=='main' and c.get('ready') for c in statuses):
            matches.append((meta['name'],main[0]))
    if len(matches)!=1:
        raise RuntimeError(f'Expected one ready Concierge main pod, observed {len(matches)}; do not mutate during rollout')
    return matches[0]


def config_value(namespace, ref, kind, key):
    """Read only one selected non-secret environment value, never print it.

    Kubernetes may project even plaintext AMP deployment --env through a
    Secret/ConfigMap. This read is needed to distinguish correct from missing
    configuration; all unrelated keys are discarded without inspection.
    """
    if not isinstance(ref, str) or not ref or not isinstance(key,str) or not key:
        raise RuntimeError('Malformed Kubernetes environment reference')
    obj=query(namespace,kind,ref)
    mapping=obj.get('data',{})
    if not isinstance(mapping,dict):
        raise RuntimeError('Kubernetes environment source has no data object')
    value=mapping.get(key)
    if value is None:
        return None
    if not isinstance(value,str):
        raise RuntimeError('Unexpected non-string Kubernetes environment value')
    if kind=='secret':
        try:
            return base64.b64decode(value,validate=True).decode('utf-8')
        except (binascii.Error,UnicodeDecodeError):
            raise RuntimeError('Invalid Kubernetes Secret encoding (no values printed)')
    return value


def referenced_value(namespace, entry):
    ref=entry.get('valueFrom')
    if not isinstance(ref,dict):
        return None,False
    if set(ref)=={'secretKeyRef'}:
        source=ref['secretKeyRef']
        return config_value(namespace, source.get('name'), 'secret', source.get('key')),True
    if set(ref)=={'configMapKeyRef'}:
        source=ref['configMapKeyRef']
        return config_value(namespace, source.get('name'), 'configmap', source.get('key')),True
    return None,False


def core_env_state(container, namespace=CONCIERGE_NS):
    matches=[e for e in container.get('env',[]) if e.get('name') == 'BANK_CORE_URL']
    if len(matches)>1:
        raise RuntimeError('Duplicate BANK_CORE_URL variable; no changes made')
    if matches:
        entry=matches[0]
        if 'valueFrom' in entry:
            value,resolved=referenced_value(namespace,entry)
            if not resolved:
                return 'unknown-valueFrom'
            if value is None:
                return 'missing-referenced-key'
            return 'correct' if value==CORE_URL else 'mismatched-reference'
        if not isinstance(entry.get('value'), str):
            return 'unknown-format'
        return 'correct' if entry['value']==CORE_URL else 'mismatched-literal'
    env_from=container.get('envFrom',[])
    if not env_from:
        return 'missing'
    found=[]
    for ent in env_from:
        if not isinstance(ent,dict) or set(ent) not in ({'secretRef'},{'configMapRef'}):
            return 'unknown-envFrom'
        kind='secret' if 'secretRef' in ent else 'configmap'
        ref=ent.get('secretRef') if kind=='secret' else ent.get('configMapRef')
        value=config_value(namespace,ref.get('name'),kind,'BANK_CORE_URL')
        if value is not None:
            found.append(value)
    if len(found)>1:
        return 'ambiguous-envFrom'
    if not found:
        return 'missing'
    return 'correct' if found[0]==CORE_URL else 'mismatched-reference'


def audit():
    ready=core_service_ready(
        query(CORE_NS,'svc',CORE_SVC),
        query(CORE_NS,'endpointslices',selector='kubernetes.io/service-name='+CORE_SVC))
    name,main=select_concierge(query(CONCIERGE_NS,'pods'))
    state=core_env_state(main)
    print('Banking Core: Service 8100 and ready EndpointSlice addresses verified ('+str(ready)+').')
    print('Concierge: Ready main pod found; BANK_CORE_URL status='+state+'.')
    # Do not print arbitrary runtime environment or sensitive pod metadata.
    return state


def apply():
    status=audit()
    if status=='correct':
        print('No change: Concierge already declares the expected Core URL. '
              'A continuing ConnectError needs DNS/egress/service investigation, not another deploy.')
        return
    if status not in ('missing','mismatched-literal','mismatched-reference','missing-referenced-key'):
        raise RuntimeError('Cannot prove effective BANK_CORE_URL from a literal pod env setting; '
                           'refusing an override because the effective env source is unknown or ambiguous')
    path=ROOT/'scripts'/'aurora-full-reconcile.py'
    sys.path.insert(0,str(path.parent))
    spec=importlib.util.spec_from_file_location('aurora_reconcile_core_repair',path)
    module=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    if getattr(module,'CORE_SERVICE_URL',None)!=CORE_URL:
        raise RuntimeError('Reconciler does not contain the reviewed Core URL contract')
    if (module.PROJECT,module.ORG,module.ENV)!=('banco-aurora','default','default'):
        raise RuntimeError('Unexpected Agent Manager coordinates; no changes made')
    print('Reusing verified completed Concierge build. Merging non-secret BANK_CORE_URL and PORT only.')
    module.deploy_agent('aurora-concierge',{'BANK_CORE_URL':CORE_URL})
    print('Concierge deployment reported active/Ready. Confirm /api/chat and Concierge logs before the full scenario run.')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply',action='store_true',help='Apply a targeted Concierge runtime-only change')
    args=parser.parse_args()
    try:
        if args.apply:
            apply()
        else:
            audit()
            print('READ-ONLY: no deployments, keys, builds, or roles changed. Use --apply only if URL is missing/mismatched.')
    except (RuntimeError,subprocess.TimeoutExpired,OSError,ValueError) as exc:
        print('CORE CONNECTIVITY NOT VERIFIED: '+str(exc),file=sys.stderr)
        return 1
    return 0


if __name__=='__main__':
    sys.exit(main())
