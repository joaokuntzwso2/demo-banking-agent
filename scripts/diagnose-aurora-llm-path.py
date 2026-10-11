#!/usr/bin/env python3
"""Read-only/optional ephemeral network probe for Concierge's managed LLM endpoint.

Default: read Kubernetes Pod, LLM env reference and gateway service. Never logs
LLM URLs, API keys, Secret contents, raw kubectl stderr, or request bodies.
--probe: add one ephemeral BusyBox to existing pod, test TCP only (no HTTP,
API calls or keys). No agents, model configs, keys, policies are modified.
"""
import argparse
import base64
import ipaddress
import json
import re
import shlex
import subprocess
import sys
import time
from urllib.parse import urlsplit

CTX = 'k3d-amp-local'
GATEWAY_NAMESPACE = 'openchoreo-data-plane'
GATEWAY_SERVICE = 'gateway-default'
GATEWAY_FQDN = GATEWAY_SERVICE + '.' + GATEWAY_NAMESPACE + '.svc.cluster.local'
IMAGE = 'rancher/mirrored-library-busybox:1.36.1'
ENV_URL = 'LLM_PROVIDER_URL'
ENV_KEY = 'LLM_PROVIDER_KEY'


class AuditError(RuntimeError):
    pass


def run(args, timeout=25):
    try:
        return subprocess.run(['kubectl', '--context', CTX, *args], capture_output=True,
                              text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise AuditError('kubectl ' + type(exc).__name__) from None


def kget(args):
    p = run(args + ['-o', 'json'])
    if p.returncode:
        raise AuditError('Cannot read Kubernetes resource: ' + ' '.join(args[:4]))
    try:
        obj = json.loads(p.stdout)
    except (ValueError, TypeError):
        raise AuditError('Kubernetes returned malformed JSON') from None
    if not isinstance(obj, dict):
        raise AuditError('Kubernetes returned unexpected resource shape')
    return obj


def select_pod(pods):
    found = []
    for pod in pods.get('items', []):
        meta, status = pod.get('metadata', {}), pod.get('status', {})
        name = meta.get('name', '')
        if not isinstance(name, str) or not name.startswith('aurora-concierge-'):
            continue
        if meta.get('deletionTimestamp') or status.get('phase') != 'Running':
            continue
        ready = [x for x in status.get('containerStatuses', []) if x.get('name') == 'main' and x.get('ready') is True]
        containers = [x for x in pod.get('spec', {}).get('containers', []) if x.get('name') == 'main']
        if len(ready) == 1 and len(containers) == 1:
            found.append((meta.get('namespace'), name, containers[0], pod))
    if len(found) != 1 or not found[0][0]:
        raise AuditError('Expected exactly one active/Ready Concierge main container; no changes made')
    return found[0]


def decode_k8s_value(namespace, source, name, is_key=False):
    if not isinstance(source, dict):
        raise AuditError('Malformed Kubernetes environment reference')
    if 'secretKeyRef' in source:
        ref = source['secretKeyRef']
        if not ref.get('name') or not ref.get('key'):
            raise AuditError('Malformed Secret environment reference')
        obj = kget(['-n', namespace, 'get', 'secret', ref['name']])
        data = obj.get('data', {})
        raw = data.get(ref['key'])
        if not isinstance(raw, str):
            raise AuditError(name + ': referenced Secret key absent')
        if is_key:
            return True   # Never decode or print the model gateway API key.
        try:
            return base64.b64decode(raw, validate=True).decode('utf-8')
        except (UnicodeError, ValueError):
            raise AuditError(name + ': referenced Secret value malformed') from None
    if 'configMapKeyRef' in source:
        ref = source['configMapKeyRef']
        if not ref.get('name') or not ref.get('key'):
            raise AuditError('Malformed ConfigMap reference')
        obj = kget(['-n', namespace, 'get', 'configmap', ref['name']])
        raw = obj.get('data', {}).get(ref['key'])
        if not isinstance(raw, str):
            raise AuditError(name + ': referenced ConfigMap key absent')
        if is_key:
            raise AuditError('LLM key in ConfigMap; refusing to treat as safely injected')
        return raw
    raise AuditError(name + ': unsupported environment reference')


def read_env(namespace, container, target, is_key=False):
    """Resolve a single env name without printing values; explicit env overrides envFrom."""
    explicit = [x for x in container.get('env', []) if x.get('name') == target]
    if len(explicit) > 1:
        raise AuditError(target + ': duplicate explicit declarations')
    if explicit:
        item = explicit[0]
        if 'valueFrom' in item:
            return decode_k8s_value(namespace, item['valueFrom'], target, is_key)
        if 'value' in item:
            if is_key:
                raise AuditError('Plaintext LLM key in Pod spec; stop and review configuration')
            return item['value']
        raise AuditError(target + ': empty explicit environment entry')
    results = []
    for from_entry in container.get('envFrom', []):
        prefix = from_entry.get('prefix', '')
        for kind, kubekind in [('secretRef','secret'), ('configMapRef','configmap')]:
            ref = from_entry.get(kind)
            if not isinstance(ref, dict) or not ref.get('name'):
                continue
            obj = kget(['-n', namespace, 'get', kubekind, ref['name']])
            raw_name = target[len(prefix):] if target.startswith(prefix) else None
            if not raw_name:
                continue
            if kind == 'secretRef':
                raw = obj.get('data', {}).get(raw_name)
                if raw is None:
                    continue
                if is_key:
                    results.append(True)
                else:
                    try:
                        results.append(base64.b64decode(raw, validate=True).decode('utf-8'))
                    except (UnicodeError, ValueError):
                        raise AuditError(target + ': malformed Secret envFrom value') from None
            else:
                value = obj.get('data', {}).get(raw_name)
                if value is not None:
                    if is_key:
                        raise AuditError('LLM key is supplied through ConfigMap; review secret storage')
                    results.append(value)
    if len(results) != 1:
        raise AuditError(target + ': expected exactly one injected value, found ' + str(len(results)))
    return results[0]


def endpoint_info(url):
    if not isinstance(url, str) or not url.strip():
        raise AuditError('Managed LLM injected endpoint URL is empty')
    if any(ord(c) < 32 or ord(c) == 127 for c in url):
        raise AuditError('Managed LLM endpoint contains forbidden control characters')
    try:
        u = urlsplit(url.strip())
        if u.scheme not in ('http', 'https') or not u.hostname or u.username or u.password or u.query or u.fragment:
            raise ValueError()
        port = u.port or (443 if u.scheme == 'https' else 80)
        host = u.hostname
        if not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9.\-]*', host) or not 1 <= port <= 65535:
            raise ValueError()
    except ValueError:
        raise AuditError('Injected LLM endpoint URL is malformed or contains forbidden credentials/query') from None
    if host == 'localhost' or host.endswith('.localhost'):
        family = 'localhost-domain (check in-cluster gateway DNS/routing)'
    elif host.endswith('.svc.cluster.local'):
        family = 'Kubernetes Service FQDN'
    elif host.endswith('.cluster.local'):
        family = 'cluster-local host'
    else:
        family = 'external/custom hostname'
    # Deliberately not returning path/query or any credentials.
    return host, port, u.scheme, family


def selector_matches(selector, labels):
    if not isinstance(selector, dict) or not isinstance(labels, dict):
        raise AuditError('Malformed NetworkPolicy selector')
    if any(labels.get(k) != v for k, v in selector.get('matchLabels', {}).items()):
        return False
    for exp in selector.get('matchExpressions', []):
        k, op, vals = exp.get('key'), exp.get('operator'), exp.get('values', [])
        if op == 'In': match = k in labels and labels[k] in vals
        elif op == 'NotIn': match = k not in labels or labels[k] not in vals
        elif op == 'Exists': match = k in labels
        elif op == 'DoesNotExist': match = k not in labels
        else: raise AuditError('Unsupported NetworkPolicy selector operator')
        if not match:
            return False
    return True


def selecting_egress_count(policies, pod):
    labels = pod.get('metadata', {}).get('labels', {})
    n = 0
    for pol in policies.get('items', []):
        spec = pol.get('spec', {})
        types = spec.get('policyTypes')
        egress = ('Egress' in types) if types is not None else 'egress' in spec
        if egress and selector_matches(spec.get('podSelector', {}), labels):
            n += 1
    return n


def gateway_port(svc):
    ports = svc.get('spec', {}).get('ports', [])
    valid = [p.get('port') for p in ports if p.get('protocol', 'TCP') == 'TCP' and isinstance(p.get('port'), int)]
    if 19080 in valid:
        return 19080
    if len(valid) == 1:
        return valid[0]
    raise AuditError('Gateway Service port is ambiguous; cannot choose probe destination')


def probe_code(host, port, gateway_port_num):
    # validated DNS host and bounded integer port, quoted nonetheless.
    h = shlex.quote(host)
    gw = shlex.quote(GATEWAY_FQDN)
    return '\n'.join([
        'if nslookup ' + h + ' >/dev/null 2>&1; then echo MODEL_DNS=ok; else echo MODEL_DNS=failed; fi',
        'if nc -z -w 6 ' + h + ' ' + str(port) + ' >/dev/null 2>&1; then echo MODEL_TCP=ok; else echo MODEL_TCP=failed; fi',
        'if nslookup ' + gw + ' >/dev/null 2>&1; then echo GATEWAY_DNS=ok; else echo GATEWAY_DNS=failed; fi',
        'if nc -z -w 6 ' + gw + ' ' + str(gateway_port_num) + ' >/dev/null 2>&1; then echo GATEWAY_TCP=ok; else echo GATEWAY_TCP=failed; fi',
    ])


def parse_probe(text):
    found = {}
    for line in text.splitlines():
        match = re.fullmatch(r'(MODEL_DNS|MODEL_TCP|GATEWAY_DNS|GATEWAY_TCP)=(ok|failed)', line.strip())
        if match:
            if match.group(1) in found:
                raise AuditError('Duplicate diagnostic line')
            found[match.group(1)] = match.group(2)
    if len(found) != 4:
        raise AuditError('Ephemeral diagnostic did not produce complete network results')
    return found


def interpret(results, selected_count):
    if results['GATEWAY_TCP'] == 'failed':
        return ('In-cluster LLM gateway is unreachable from Concierge; ' +
                ('selected Egress NetworkPolicy may deny this hop; audit scoped gateway allow rule' if selected_count else
                 'inspect gateway Service endpoints, listener and cluster routing'))
    if results['MODEL_TCP'] == 'failed':
        return 'Gateway Service TCP works, but injected LLM hostname/port fails: inspect advertised URL, DNS and gateway vhost routing'
    return 'Both TCP paths are reachable: inspect provider gateway response, API-key header, proxy policies, upstream OpenAI credentials/model; TCP alone does not prove LLM success'


def probe(namespace, pod_name, shell):
    name = 'aurora-llm-path-' + str(int(time.time()))
    print('Creating one temporary ephemeral debug container; no agent, key, workflow or network policy changes.')
    proc = run(['-n', namespace, 'debug', 'pod/' + pod_name, '--container=' + name,
                '--target=main', '--image=' + IMAGE, '--attach=false', '--', '/bin/sh', '-c', shell], 45)
    if proc.returncode:
        raise AuditError('Ephemeral debug creation failed (RBAC/admission/runtime); no configuration changes')
    for _ in range(24):
        time.sleep(2)
        out = run(['-n', namespace, 'logs', 'pod/' + pod_name, '-c', name], 15)
        if out.returncode == 0:
            try:
                return parse_probe(out.stdout)
            except AuditError:
                pass
    raise AuditError('Debug probe did not finish within 48s; its record stays with this pod')


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--probe', action='store_true', help='Add a single ephemeral container to test DNS and TCP only')
    args = p.parse_args(argv)
    try:
        ns, name, main_container, pod = select_pod(kget(['get', 'pods', '-A']))
        url = read_env(ns, main_container, ENV_URL)
        assert read_env(ns, main_container, ENV_KEY, is_key=True)
        host, port, scheme, family = endpoint_info(url)
        pol_count = selecting_egress_count(kget(['-n', ns, 'get', 'networkpolicies']), pod)
        svc = kget(['-n', GATEWAY_NAMESPACE, 'get', 'service', GATEWAY_SERVICE])
        gw_port = gateway_port(svc)
        print('Concierge: one Ready main container, managed LLM URL and secret-backed API key present.')
        print('Injected LLM URL: scheme=%s, endpoint class=%s, TCP port=%d (full URL and key concealed).' % (scheme, family, port))
        print('Concierge selected Egress NetworkPolicies: %d (presence does not establish destination allowance).' % pol_count)
        print('Internal LLM gateway: Service exists, TCP Service port=%d.' % gw_port)
        if not args.probe:
            print('READ-ONLY COMPLETE. Use --probe for DNS/TCP tests from Concierge Pod network.')
            print('NOTE: --probe records one ephemeral container until Concierge pod replacement.')
            return 0
        result = probe(ns, name, probe_code(host, port, gw_port))
        for k in ('MODEL_DNS','MODEL_TCP','GATEWAY_DNS','GATEWAY_TCP'):
            print('  %s: %s' % (k, result[k]))
        print('DIAGNOSIS: ' + interpret(result, pol_count))
        print('TCP-only; no model request sent, credentials inspected beyond presence, or gateway/agent configuration changed.')
        return 0
    except AuditError as exc:
        print('NOT VERIFIED: ' + str(exc), file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
