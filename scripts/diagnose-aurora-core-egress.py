#!/usr/bin/env python3
"""Audit, and optionally add, a narrowly scoped Concierge -> Banking Core egress NetworkPolicy.

The default path is READ ONLY. --apply requires an unambiguous denial by standard
Kubernetes NetworkPolicy and creates one new namespaced allow policy; it does
not edit existing policies or workloads. It does not read Secrets or ConfigMaps.
"""
import argparse
import ipaddress
import json
import subprocess
import sys

CTX = 'k3d-amp-local'
CORE_NAMESPACE = 'aurora-demo'
CORE_SERVICE = 'aurora-core'
CORE_PORT = 8100
POLICY_NAME = 'aurora-concierge-egress-core-8100'
OWNER = 'banco-aurora-egress-audit'
SELECTOR_KEYS = (
    'app.kubernetes.io/name',
    'openchoreo.dev/component-name',
    'openchoreo.dev/component',
    'app.kubernetes.io/component',
    'app.kubernetes.io/instance',
    'component',
    'app',
    'service',
)


def run(args, payload=None, timeout=25):
    try:
        return subprocess.run(['kubectl', '--context', CTX, *args], input=payload,
                              capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError('kubectl ' + type(exc).__name__) from None


def read_json(args):
    r = run(args + ['-o', 'json'])
    if r.returncode:
        raise RuntimeError('Cannot read Kubernetes inventory (' + ' '.join(args[:4]) + ')')
    try:
        obj = json.loads(r.stdout)
    except (ValueError, TypeError):
        raise RuntimeError('Invalid Kubernetes JSON inventory') from None
    if not isinstance(obj, dict):
        raise RuntimeError('Unexpected Kubernetes inventory structure')
    return obj


def items(obj):
    result = obj.get('items')
    if not isinstance(result, list):
        raise RuntimeError('Kubernetes list missing items')
    return result


def match_selector(selector, labels):
    """Implement Kubernetes LabelSelector for matchLabels + matchExpressions."""
    if not isinstance(selector, dict) or not isinstance(labels, dict):
        raise ValueError('Invalid selector or labels')
    if any(labels.get(k) != v for k, v in selector.get('matchLabels', {}).items()):
        return False
    for req in selector.get('matchExpressions', []):
        key, op, values = req.get('key'), req.get('operator'), req.get('values', [])
        if not isinstance(key, str):
            raise ValueError('Malformed matchExpression')
        present = key in labels
        if op == 'In':
            ok = present and labels[key] in values
        elif op == 'NotIn':
            ok = not present or labels[key] not in values
        elif op == 'Exists':
            ok = present
        elif op == 'DoesNotExist':
            ok = not present
        else:
            raise ValueError('Unsupported label selector operator')
        if not ok:
            return False
    return True


def choose_concierge(pods):
    matches = []
    for pod in pods:
        meta, status = pod.get('metadata', {}), pod.get('status', {})
        if not meta.get('name', '').startswith('aurora-concierge-'):
            continue
        if meta.get('deletionTimestamp') or status.get('phase') != 'Running':
            continue
        if not any(s.get('name') == 'main' and s.get('ready') is True
                   for s in status.get('containerStatuses', [])):
            continue
        matches.append(pod)
    if len(matches) != 1:
        raise RuntimeError('Expected exactly one active and Ready Concierge pod; avoid policy changes during rollout')
    return matches[0]


def choose_concierge_selector(pod, namespace_pods):
    labels = pod.get('metadata', {}).get('labels', {})
    podname = pod['metadata']['name']
    for key in SELECTOR_KEYS:
        value = labels.get(key)
        if not isinstance(value, str) or 'aurora-concierge' not in value:
            continue
        matched = [p for p in namespace_pods if p.get('metadata', {}).get('labels', {}).get(key) == value]
        if len(matched) == 1 and matched[0].get('metadata', {}).get('name') == podname:
            return {key: value}
    raise RuntimeError('No unique stable Concierge identity label found; refuse broad policy selector')


def egress_policy(pol):
    spec = pol.get('spec', {})
    types = spec.get('policyTypes')
    return 'Egress' in types if types is not None else 'egress' in spec


def peer_match(peer, core_ns_labels, core_pod_labels, endpoint_ip):
    """Return True, False, or None for uncertain IPBlock semantics."""
    if not peer:
        return True
    if 'ipBlock' in peer:
        try:
            block = peer['ipBlock']
            addr = ipaddress.ip_address(endpoint_ip)
            permitted = addr in ipaddress.ip_network(block['cidr'], strict=False)
            permitted = permitted and not any(addr in ipaddress.ip_network(e, strict=False) for e in block.get('except', []))
        except (KeyError, ValueError):
            return None
        # NetworkPolicy IPBlock semantics for in-cluster pod traffic vary with CNI.
        return None if permitted else False
    if 'namespaceSelector' not in peer and 'podSelector' not in peer:
        return None
    if 'namespaceSelector' not in peer:
        return False  # podSelector alone means same namespace, not aurora-demo.
    if not match_selector(peer['namespaceSelector'], core_ns_labels):
        return False
    if 'podSelector' in peer and not match_selector(peer['podSelector'], core_pod_labels):
        return False
    return True


def port_match(ports):
    if not ports:
        return True
    uncertain = False
    for p in ports:
        if p.get('protocol', 'TCP') != 'TCP':
            continue
        port = p.get('port')
        if port is None:
            return True
        if isinstance(port, int):
            if port <= CORE_PORT <= p.get('endPort', port):
                return True
        else:
            uncertain = True  # named ports must be evaluated against the container spec
    return None if uncertain else False


def egress_allow(pol, core_ns_labels, core_pod_labels, endpoint_ip):
    uncertain = False
    for rule in pol.get('spec', {}).get('egress', []):
        port_ok = port_match(rule.get('ports'))
        if port_ok is False:
            continue
        destinations = rule.get('to')
        matches = [peer_match(p, core_ns_labels, core_pod_labels, endpoint_ip)
                   for p in destinations] if destinations else [True]
        if port_ok is True and True in matches:
            return True
        if port_ok is None or None in matches:
            uncertain = True
    return None if uncertain else False


def classify(policies, concierge_labels, core_ns_labels, core_pod_labels, endpoint_ip):
    selected = [p for p in policies if egress_policy(p) and
                match_selector(p.get('spec', {}).get('podSelector', {}), concierge_labels)]
    if not selected:
        return 'unrestricted', []
    findings = [(p['metadata']['name'], egress_allow(p, core_ns_labels, core_pod_labels, endpoint_ip))
                for p in selected]
    if any(allowed is True for _, allowed in findings):
        return 'allowed', findings
    if any(allowed is None for _, allowed in findings):
        return 'uncertain', findings
    return 'denied', findings


def plan_policy(namespace, selector, core_service_selector):
    return {
        'apiVersion': 'networking.k8s.io/v1',
        'kind': 'NetworkPolicy',
        'metadata': {'name': POLICY_NAME, 'namespace': namespace,
                     'labels': {'app.kubernetes.io/managed-by': OWNER}},
        'spec': {
            'podSelector': {'matchLabels': selector},
            'policyTypes': ['Egress'],
            'egress': [{
                'to': [{'namespaceSelector': {'matchLabels': {'kubernetes.io/metadata.name': CORE_NAMESPACE}},
                        'podSelector': {'matchLabels': core_service_selector}}],
                'ports': [{'protocol': 'TCP', 'port': CORE_PORT}],
            }],
        },
    }


def audit():
    allpods = items(read_json(['get', 'pods', '-A']))
    concierge = choose_concierge(allpods)
    meta = concierge['metadata']
    dp_ns = meta['namespace']
    ns_pods = [p for p in allpods if p.get('metadata', {}).get('namespace') == dp_ns]
    selector = choose_concierge_selector(concierge, ns_pods)
    core_svc = read_json(['-n', CORE_NAMESPACE, 'get', 'service', CORE_SERVICE])
    svc = core_svc.get('spec', {})
    core_selector = svc.get('selector', {})
    if not isinstance(core_selector, dict) or not core_selector:
        raise RuntimeError('Core Service has no explicit pod selector; refuse policy generation')
    if not any(p.get('port') == CORE_PORT and str(p.get('targetPort')) == str(CORE_PORT)
               for p in svc.get('ports', [])):
        raise RuntimeError('Core Service/target port 8100 mismatch')
    core_pods = [p for p in allpods if p.get('metadata', {}).get('namespace') == CORE_NAMESPACE
                 and all(p.get('metadata', {}).get('labels', {}).get(k) == v for k, v in core_selector.items())
                 and p.get('status', {}).get('phase') == 'Running'
                 and p.get('status', {}).get('podIP')]
    if len(core_pods) != 1:
        raise RuntimeError('Expected one running Core Pod matched by Core Service selector; refuse ambiguous policy plan')
    core_pod = core_pods[0]
    slices = items(read_json(['-n', CORE_NAMESPACE, 'get', 'endpointslices', '-l',
                              'kubernetes.io/service-name=' + CORE_SERVICE]))
    ready_ips = set()
    for sl in slices:
        for ep in sl.get('endpoints', []):
            if ep.get('conditions', {}).get('ready') is not False:
                ready_ips.update(ep.get('addresses', []))
    ip = core_pod['status']['podIP']
    if ip not in ready_ips:
        raise RuntimeError('Core Pod is not in ready EndpointSlice; refuse policy generation')
    core_ns = read_json(['get', 'namespace', CORE_NAMESPACE]).get('metadata', {}).get('labels', {})
    if core_ns.get('kubernetes.io/metadata.name') != CORE_NAMESPACE:
        raise RuntimeError('Core namespace identity label missing or mismatched')
    policies = items(read_json(['-n', dp_ns, 'get', 'networkpolicies']))
    status, findings = classify(policies, meta.get('labels', {}), core_ns,
                                core_pod.get('metadata', {}).get('labels', {}), ip)
    policy = plan_policy(dp_ns, selector, core_selector)
    if not match_selector(policy['spec']['podSelector'], meta.get('labels', {})):
        raise RuntimeError('Generated selector does not match Concierge')
    if peer_match(policy['spec']['egress'][0]['to'][0], core_ns,
                  core_pod['metadata'].get('labels', {}), ip) is not True:
        raise RuntimeError('Generated Core destination selector does not match Core Pod')
    return status, findings, policy, policies


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--apply', action='store_true', help='Create a new narrow allow rule ONLY if proven denied by standard Kubernetes policies')
    args = ap.parse_args(argv)
    try:
        status, findings, policy, inventory = audit()
        print('Concierge -> Core TCP/8100: Kubernetes NetworkPolicy audit=%s' % status.upper())
        print('Selecting Egress NetworkPolicies: %d' % len(findings))
        for name, allow in findings:
            print('  %s: %s' % (name, 'permits' if allow is True else 'uncertain' if allow is None else 'no matching allow'))
        print('Candidate: %s in namespace %s; pod selector=%s; destination=namespace %s + Core Service selector, TCP/8100.' % (
            POLICY_NAME, policy['metadata']['namespace'], ','.join(policy['spec']['podSelector']['matchLabels']), CORE_NAMESPACE))
        if status == 'allowed':
            print('Standard NetworkPolicy already allows the path. Diagnose non-NetworkPolicy controls/routing instead; no changes.')
            return 0
        if status == 'unrestricted':
            print('Concierge is not isolated for egress by standard NetworkPolicy. Investigate CNI/global policies or routing; no changes.')
            return 0
        if status == 'uncertain':
            print('Existing IPBlock or named-port policy prevents conclusive evaluation. No automatic changes permitted.')
            return 2
        if not args.apply:
            print('READ-ONLY: selected standard NetworkPolicies do not allow Concierge -> Core TCP/8100.')
            print('To add only this flow, inspect this report and explicitly rerun with --apply.')
            return 0
        if any(p.get('metadata', {}).get('name') == POLICY_NAME for p in inventory):
            raise RuntimeError('Candidate policy name already exists; refuse to edit existing policy')
        data = json.dumps(policy)
        if run(['create', '--dry-run=server', '-f', '-'], payload=data).returncode:
            raise RuntimeError('Kubernetes server-side validation rejected narrow policy; nothing created')
        if run(['create', '-f', '-'], payload=data).returncode:
            raise RuntimeError('Could not create narrow policy (RBAC/conflict/admission); inspect cluster; no existing policies edited')
        print('CREATED: narrow, additive Concierge -> Banking Core TCP/8100 egress NetworkPolicy.')
        print('Run the existing core-path --probe again, then rerun chat diagnostic. Remove ONLY this policy if required.')
        return 0
    except (RuntimeError, ValueError, KeyError, TypeError) as exc:
        print('NOT VERIFIED: ' + str(exc), file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
