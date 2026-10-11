#!/usr/bin/env python3
"""Read-only, sanitized traceback classifier for the Banco Aurora chat HTTP 500.

Reads only Kubernetes pod inventory and recent logs. Never prints log contents,
credentials, prompts, URLs, customer identifiers, request bodies, or environment.
Requires kubectl access to the existing k3d-amp-local cluster.
"""
import argparse
from collections import Counter
import json
import re
import subprocess
import sys

CTX = 'k3d-amp-local'
WORKLOAD_NS = 'dp-default-banco-aurora-default-990774f5'
CORE_NS = 'aurora-demo'
ALLOWED_SERVICES = {
    'concierge': (WORKLOAD_NS, 'aurora-concierge-'),
    'portal': (CORE_NS, 'aurora-portal-'),
    'core': (CORE_NS, 'aurora-core-'),
}
# Only recognize exception CLASS names, not potentially sensitive messages.
EXCEPTION = re.compile(
    r'(?m)^\s*(?:(?:[A-Za-z_][\w]*\.)*)([A-Za-z_][\w]*(?:Error|Exception|Timeout|ExceptionGroup))\s*:',
)
FRAME = re.compile(r'(?m)^\s*File "([^"]+)", line (\d+), in ([A-Za-z_][\w]*)')
SAFE_MODULES = (
    'agents/concierge.py', 'agents/common.py', 'agents/specialists.py',
    'banking_common/config.py', 'banking_common/security.py',
    'mock_core/app.py', 'mock_core/store.py', 'demo_portal/app.py',
)
CHAT_FAILURE = re.compile(r'(?i)(?:POST /chat|POST /api/chat).*?\b(?:500|503)\b')


def command(args, timeout=20):
    try:
        return subprocess.run(args, capture_output=True, text=True,
                              timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        # Do not stringify shell/transport errors (may contain endpoint details).
        raise RuntimeError('kubectl transport failed: ' + type(exc).__name__) from None


def choose_pods(items, prefix):
    """Return up to two recent active pods; avoids stale completed workflow pods."""
    selected = []
    for pod in items:
        if not isinstance(pod, dict):
            continue
        meta = pod.get('metadata') or {}
        spec = pod.get('spec') or {}
        stat = pod.get('status') or {}
        name = meta.get('name', '')
        if (not isinstance(name, str) or not name.startswith(prefix)
                or meta.get('deletionTimestamp') or stat.get('phase') != 'Running'):
            continue
        containers = spec.get('containers') or []
        names = [x.get('name') for x in containers if isinstance(x, dict)]
        name_of_container = 'main' if 'main' in names else (
            'portal' if 'portal' in names else ('core' if 'core' in names else None))
        if name_of_container is None:
            if len(names) != 1 or not isinstance(names[0], str):
                continue
            name_of_container = names[0]
        statuses = stat.get('containerStatuses') or []
        record = next((x for x in statuses if isinstance(x, dict)
                       and x.get('name') == name_of_container), {})
        selected.append((meta.get('creationTimestamp') or '', name,
                         name_of_container, int(record.get('restartCount') or 0)))
    selected.sort(reverse=True)
    return [(name, container, restarts) for _, name, container, restarts in selected[:2]]


def sanitize_traceback(logs):
    """Return counts and allowlisted source locations; never retain messages."""
    classes = Counter(EXCEPTION.findall(logs))
    frames = []
    for path, lineno, _ in FRAME.findall(logs):
        normalized = path.replace('\\', '/')
        matching = next((m for m in SAFE_MODULES
                         if normalized.endswith('/' + m) or normalized == m), None)
        if matching and (matching, lineno) not in frames:
            frames.append((matching, lineno))
    return {
        'exception_types': classes,
        'source_locations': frames[-10:],
        'chat_http_failures': len(CHAT_FAILURE.findall(logs)),
        'tracebacks': logs.count('Traceback (most recent call last):'),
    }


def inventory(namespace):
    result = command(['kubectl', '--context', CTX, '-n', namespace,
                      'get', 'pods', '-o', 'json'])
    if result.returncode:
        raise RuntimeError('Cannot read Kubernetes pod inventory for namespace ' + namespace)
    try:
        data = json.loads(result.stdout)
        items = data['items']
        if not isinstance(items, list):
            raise ValueError()
        return items
    except (ValueError, TypeError, KeyError):
        raise RuntimeError('Unexpected Kubernetes pod inventory response') from None


def logs_for(namespace, pod, container, since, previous=False):
    args = ['kubectl', '--context', CTX, '-n', namespace,
            'logs', 'pod/' + pod, '-c', container,
            '--since=' + since, '--tail=650']
    if previous:
        args.append('--previous')
    result = command(args, timeout=30)
    return result.stdout if result.returncode == 0 else None


def hints(classes):
    c = set(classes)
    if c & {'ConnectError', 'ConnectTimeout', 'ReadTimeout', 'RemoteProtocolError',
            'NetworkError', 'RequestError'}:
        print('  Next area: Concierge outbound connectivity (Core, gateway, or specialist).')
    if c & {'HTTPStatusError', 'APIStatusError', 'AuthenticationError',
            'PermissionDeniedError', 'RateLimitError', 'BadRequestError'}:
        print('  Next area: upstream HTTP status/authentication; identify failing service from stack location.')
    if c & {'KeyError', 'TypeError', 'ValueError', 'ValidationError', 'AttributeError'}:
        print('  Next area: response payload or application logic, based on source location.')
    if c & {'RuntimeError', 'ConfigurationError'}:
        print('  Next area: missing/mismatched runtime configuration; do not print env or secrets.')
    if not c:
        print('  No Python exception signature found. Inspect gateway routing and the request timestamp.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--since', default='20m',
                        choices=['5m', '10m', '20m', '30m', '1h'])
    parser.add_argument('--include-portal', action='store_true')
    parser.add_argument('--include-core', action='store_true')
    args = parser.parse_args()
    services = ['concierge']
    if args.include_portal:
        services.append('portal')
    if args.include_core:
        services.append('core')
    for service in services:
        ns, prefix = ALLOWED_SERVICES[service]
        print('\n' + service.upper() + ': sanitized exceptions from last ' + args.since)
        pods = choose_pods(inventory(ns), prefix)
        if not pods:
            print('  No active matching pod found; no Kubernetes changes made.')
            continue
        for index, (pod, container, restarts) in enumerate(pods, 1):
            print('  Running pod #%d: container=%s, restarts=%d' % (
                index, container if container in ('main', 'portal', 'core') else 'other', restarts))
            for previous in ([False, True] if restarts > 0 else [False]):
                content = logs_for(ns, pod, container, args.since, previous=previous)
                if content is None:
                    print('    %s log stream unavailable' % ('previous' if previous else 'current'))
                    continue
                data = sanitize_traceback(content)
                title = 'previous' if previous else 'current'
                print('    %s: tracebacks=%d chat_5xx_lines=%d' % (
                    title, data['tracebacks'], data['chat_http_failures']))
                classes = data['exception_types']
                if classes:
                    print('    Exception types: ' + ', '.join(
                        '%s (%d)' % (n, cnt) for n, cnt in classes.most_common(8)))
                if data['source_locations']:
                    print('    Repo traceback locations: ' + ', '.join(
                        '%s:%s' % (n, line) for n, line in data['source_locations']))
                hints(classes)
    print('\nREAD-ONLY: no resource mutations, credentials, raw logs, URLs or customer data printed.')
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except RuntimeError as exc:
        print('DIAGNOSTIC ERROR: ' + str(exc), file=sys.stderr)
        sys.exit(2)
