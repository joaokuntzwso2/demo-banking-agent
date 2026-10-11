#!/usr/bin/env python3
"""Diagnose Concierge -> Banking Core from the actual Concierge Pod.

Default is read-only. --probe adds one ephemeral BusyBox container to the
current Concierge Pod for a bounded, unauthenticated GET /health probe.
No deployment, build, credentials, network policy, or service is modified.
The ephemeral-container record remains on that Pod until its replacement.
"""
import argparse
import ipaddress
import json
import re
import subprocess
import sys
import time

CTX = 'k3d-amp-local'
DP_NS = 'dp-default-banco-aurora-default-990774f5'
CORE_NS = 'aurora-demo'
CORE_SVC = 'aurora-core'
CORE_HOST = 'aurora-core.aurora-demo.svc.cluster.local'
IMAGE = 'rancher/mirrored-library-busybox:1.36.1'
PROXY_NAMES = {'HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY', 'NO_PROXY'}


def kubectl(args, timeout=30):
    try:
        return subprocess.run(['kubectl', '--context', CTX, *args],
                              capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError('kubectl failed: ' + type(exc).__name__) from None


def get_json(namespace, kind, name=None, selector=None):
    args = ['-n', namespace, 'get', kind]
    if name:
        args.append(name)
    if selector:
        args.extend(['-l', selector])
    args.extend(['-o', 'json'])
    process = kubectl(args)
    if process.returncode:
        raise RuntimeError('Cannot read Kubernetes ' + kind + ' from namespace ' + namespace)
    try:
        value = json.loads(process.stdout)
        if not isinstance(value, dict):
            raise ValueError('not object')
        return value
    except ValueError:
        raise RuntimeError('Kubernetes ' + kind + ' returned invalid JSON') from None


def choose_concierge(pods):
    matches = []
    for pod in pods.get('items', []):
        meta, status = pod.get('metadata', {}), pod.get('status', {})
        name = meta.get('name', '')
        if not isinstance(name, str) or not name.startswith('aurora-concierge-'):
            continue
        if meta.get('deletionTimestamp') or status.get('phase') != 'Running':
            continue
        cs = [s for s in status.get('containerStatuses', [])
              if s.get('name') == 'main' and s.get('ready') is True]
        spec = [s for s in pod.get('spec', {}).get('containers', []) if s.get('name') == 'main']
        if len(cs) == 1 and len(spec) == 1:
            matches.append((name, pod, spec[0]))
    if len(matches) != 1:
        raise RuntimeError('Expected exactly one ready Concierge Pod (found %s); no probing during rollout' % len(matches))
    return matches[0]


def core_addresses(service, slices):
    svc = service.get('spec', {})
    if not any(p.get('port') == 8100 and str(p.get('targetPort')) == '8100'
               for p in svc.get('ports', [])):
        raise RuntimeError('Core Service does not have verified port/targetPort 8100')
    ip = svc.get('clusterIP')
    if not isinstance(ip, str) or ip == 'None':
        raise RuntimeError('Core Service has no ClusterIP')
    try:
        if ipaddress.ip_address(ip).version != 4:
            raise ValueError()
    except ValueError:
        raise RuntimeError('Core ClusterIP is not a supported IPv4 address') from None
    ips = []
    for sl in slices.get('items', []):
        if not isinstance(sl, dict):
            continue
        for ep in sl.get('endpoints', []):
            if ep.get('conditions', {}).get('ready') is False:
                continue
            for addr in ep.get('addresses', []):
                try:
                    if ipaddress.ip_address(addr).version == 4:
                        ips.append(addr)
                except ValueError:
                    raise RuntimeError('Invalid Core EndpointSlice address') from None
    ips = sorted(set(ips))
    if not ips:
        raise RuntimeError('Core has no ready IPv4 EndpointSlice targets')
    return ip, ips


def proxy_env_info(main):
    found = set()
    for env in main.get('env', []):
        name = env.get('name')
        if isinstance(name, str) and name.upper() in PROXY_NAMES:
            found.add(name.upper())
    has_env_from = bool(main.get('envFrom'))
    # envFrom could contain proxy variables. Never read or print their values.
    return sorted(found), has_env_from


def make_probe_shell(service_ip, endpoint_ip):
    # Addresses are validated as IPv4. Input is not user-supplied shell text.
    for ip in (service_ip, endpoint_ip):
        if ipaddress.ip_address(ip).version != 4:
            raise ValueError('Only IPv4 targets are supported')
    return '\n'.join([
        'if nslookup ' + CORE_HOST + ' >/dev/null 2>&1; then echo DNS=ok; else echo DNS=failed; fi',
        'if wget -q -T 6 -O /dev/null http://' + CORE_HOST + ':8100/health >/dev/null 2>&1; then echo FQDN_HTTP=ok; else echo FQDN_HTTP=failed; fi',
        'if wget -q -T 6 -O /dev/null http://' + service_ip + ':8100/health >/dev/null 2>&1; then echo SERVICE_IP_HTTP=ok; else echo SERVICE_IP_HTTP=failed; fi',
        'if wget -q -T 6 -O /dev/null http://' + endpoint_ip + ':8100/health >/dev/null 2>&1; then echo ENDPOINT_IP_HTTP=ok; else echo ENDPOINT_IP_HTTP=failed; fi',
    ])


def parse_probe_output(logs):
    names = ('DNS', 'FQDN_HTTP', 'SERVICE_IP_HTTP', 'ENDPOINT_IP_HTTP')
    results = {}
    for line in logs.splitlines():
        line = line.strip()
        m = re.fullmatch(r'(DNS|FQDN_HTTP|SERVICE_IP_HTTP|ENDPOINT_IP_HTTP)=(ok|failed)', line)
        if m:
            if m.group(1) in results:
                raise RuntimeError('Duplicate network probe result')
            results[m.group(1)] = m.group(2)
    if set(results) != set(names):
        raise RuntimeError('Debug container returned incomplete network probe results')
    return results


def interpret(results):
    if all(v == 'ok' for v in results.values()):
        return 'Core is reachable from Concierge Pod network. Investigate process configuration, HTTP_PROXY/NO_PROXY, or a different failed outbound request.'
    if results['ENDPOINT_IP_HTTP'] == 'ok' and results['SERVICE_IP_HTTP'] == 'failed':
        return 'Direct Core Pod works but Service IP fails: investigate kube-proxy/Service routing or policy on ClusterIP traffic.'
    if results['SERVICE_IP_HTTP'] == 'ok' and results['FQDN_HTTP'] == 'failed':
        return 'Service IP works but DNS URL fails: investigate Concierge Pod DNS resolution.'
    if results['SERVICE_IP_HTTP'] == 'failed' and results['ENDPOINT_IP_HTTP'] == 'failed':
        return 'Core traffic from Concierge Pod fails on both Service and endpoint IP: inspect ingress/egress NetworkPolicies and network routing.'
    return 'Mixed failure; inspect Pod DNS, endpoint health, and NetworkPolicies before changing any resources.'


def probe(pod_name, service_ip, endpoint_ip):
    # This modifies only the target Pod ephemeral-container subresource.
    name = 'aurora-core-path-%d' % int(time.time())
    shell = make_probe_shell(service_ip, endpoint_ip)
    print('Adding one ephemeral debug container to the existing Concierge Pod (no app restart or secret access).')
    r = kubectl(['-n', DP_NS, 'debug', 'pod/' + pod_name,
                 '--container=' + name, '--target=main', '--image=' + IMAGE,
                 '--attach=false', '--', '/bin/sh', '-c', shell], timeout=45)
    if r.returncode:
        raise RuntimeError('Debug-container creation failed (RBAC, admission policy, or unsupported runtime); no deployment changes performed')
    for _ in range(24):
        time.sleep(2)
        out = kubectl(['-n', DP_NS, 'logs', 'pod/' + pod_name, '-c', name], timeout=15)
        if out.returncode == 0:
            try:
                return parse_probe_output(out.stdout)
            except RuntimeError:
                pass
    raise RuntimeError('Debug container did not return complete results in 48 seconds; inspect its status. It remains recorded in the Pod.')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--probe', action='store_true', help='Add one ephemeral debug container and actively test network reachability')
    args = parser.parse_args(argv)
    try:
        pod_name, pod, main_container = choose_concierge(get_json(DP_NS, 'pods'))
        service_ip, endpoints = core_addresses(
            get_json(CORE_NS, 'svc', CORE_SVC),
            get_json(CORE_NS, 'endpointslices', selector='kubernetes.io/service-name=' + CORE_SVC))
        policies_dp = get_json(DP_NS, 'networkpolicies').get('items', [])
        policies_core = get_json(CORE_NS, 'networkpolicies').get('items', [])
        if not isinstance(policies_dp, list) or not isinstance(policies_core, list):
            raise RuntimeError('Invalid NetworkPolicy inventory')
        proxy_keys, env_from = proxy_env_info(main_container)
        print('Concierge: exactly one active/Ready Pod; pod DNS policy=%s.' % pod.get('spec', {}).get('dnsPolicy', 'unknown'))
        print('Banking Core: Service 8100 and %d ready IPv4 endpoints verified.' % len(endpoints))
        print('NetworkPolicies: Concierge namespace=%d, Core namespace=%d (presence does not prove traffic is denied).' % (len(policies_dp), len(policies_core)))
        print('Concierge declared proxy variable names: %s; envFrom source present=%s.' % (
            ', '.join(proxy_keys) or 'none explicit', 'yes (additional keys unknown)' if env_from else 'no'))
        if not args.probe:
            print('READ-ONLY COMPLETE. To test actual reachability from the same Pod network namespace, run with --probe.')
            print('NOTE: --probe adds an ephemeral debug container record, which remains until the Pod is replaced.')
            return 0
        result = probe(pod_name, service_ip, endpoints[0])
        for name in ('DNS', 'FQDN_HTTP', 'SERVICE_IP_HTTP', 'ENDPOINT_IP_HTTP'):
            print('  %s: %s' % (name, result[name]))
        print('DIAGNOSIS: ' + interpret(result))
        print('Probe is unauthenticated GET /health only. No banking records, keys, deployment or app container were changed.')
    except (RuntimeError, ValueError) as exc:
        print('NOT VERIFIED: ' + str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
