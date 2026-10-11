#!/usr/bin/env python3
"""Audits injected Concierge LLM route and optionally makes one governed gateway model call.

Default: read-only Kubernetes inventory, no model calls. --smoke: port-forward
verified runtime Service to loopback and send ONE small authenticated chat
completion via gateway. Secret bytes stay in Python memory, never stdout, argv,
request debug traces or disk. Does not alter workloads or rotate credentials.
"""
import argparse
import base64
import importlib.util
import json
import os
from pathlib import Path
import re
import select
import signal
import subprocess
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import build_opener, ProxyHandler, Request


class CheckError(RuntimeError):
    pass


def base_module():
    script = Path(__file__).with_name('diagnose-aurora-llm-path.py')
    if not script.is_file():
        raise CheckError('Install previous LLM network diagnostic alongside this script')
    spec = importlib.util.spec_from_file_location('aurora_llm_network_diag', script)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def service_identity(host, pod_namespace):
    """Accept K8s service DNS only; don't guess arbitrary external destinations."""
    suffix = '.svc.cluster.local'
    if host.endswith(suffix):
        labels = host[:-len(suffix)].split('.')
    else:
        labels = host.split('.')
    if len(labels) == 2:
        service, ns = labels
    elif len(labels) == 1:
        service, ns = labels[0], pod_namespace
    else:
        raise CheckError('Injected model host is not recognized as a Kubernetes Service DNS alias; no forwarding attempted')
    if not all(re.fullmatch(r'[a-z0-9]([-a-z0-9]*[a-z0-9])?', v) for v in (service, ns)):
        raise CheckError('Invalid Kubernetes Service DNS identifier; no forwarding attempted')
    return service, ns


def find_runtime_service(mod, host, port, ns):
    service, svc_ns = service_identity(host, ns)
    svc = mod.kget(['-n', svc_ns, 'get', 'service', service])
    if svc.get('metadata', {}).get('name') != service or svc.get('metadata', {}).get('namespace') != svc_ns:
        raise CheckError('Service identity mismatch; no forwarding attempted')
    if svc.get('spec', {}).get('type') == 'ExternalName':
        raise CheckError('ExternalName service is unsupported for this constrained smoke test')
    ports = [p for p in svc.get('spec', {}).get('ports', [])
             if p.get('port') == port and p.get('protocol', 'TCP') == 'TCP']
    if len(ports) != 1:
        raise CheckError('Injected model port is not a unique TCP port of the named Kubernetes Service')
    # Verify this is a Service with ready endpoints; do not test a dead or unowned route.
    eps = mod.kget(['-n', svc_ns, 'get', 'endpointslices', '-l', 'kubernetes.io/service-name=' + service])
    ready = sum(len(s.get('addresses', [])) for es in eps.get('items', [])
                for s in es.get('endpoints', []) if s.get('conditions', {}).get('ready') is not False)
    if ready < 1:
        raise CheckError('LLM runtime Service has no ready EndpointSlice address')
    return service, svc_ns, ready


def secret_string(obj, name):
    raw = obj.get('data', {}).get(name)
    if not isinstance(raw, str):
        return None
    try:
        result = base64.b64decode(raw, validate=True).decode('utf-8')
    except (ValueError, UnicodeError):
        raise CheckError('Referenced LLM gateway Secret value malformed') from None
    if not result:
        raise CheckError('Referenced LLM gateway Secret value empty')
    return result


def read_model_key(mod, namespace, container):
    """Use only existing Secret references. Never serialize the resulting credential."""
    target = 'LLM_PROVIDER_KEY'
    explicit = [i for i in container.get('env', []) if i.get('name') == target]
    if len(explicit) > 1:
        raise CheckError('Duplicate explicit managed LLM credential references')
    if explicit:
        ref = explicit[0].get('valueFrom', {}).get('secretKeyRef', {})
        if not ref.get('name') or not ref.get('key'):
            raise CheckError('Managed LLM credential is not a Kubernetes SecretKeyRef')
        obj = mod.kget(['-n', namespace, 'get', 'secret', ref['name']])
        key = secret_string(obj, ref['key'])
        if key is None:
            raise CheckError('LLM SecretKeyRef key is missing')
        return key
    matches = []
    for e in container.get('envFrom', []):
        ref = e.get('secretRef')
        if not isinstance(ref, dict) or not ref.get('name'):
            continue
        prefix = e.get('prefix', '')
        if not target.startswith(prefix):
            continue
        rawname = target[len(prefix):]
        if not rawname:
            continue
        obj = mod.kget(['-n', namespace, 'get', 'secret', ref['name']])
        key = secret_string(obj, rawname)
        if key is not None:
            matches.append(key)
    if len(matches) != 1:
        raise CheckError('Expected exactly one Secret-backed managed LLM credential')
    return matches[0]


def choose_model(mod, ns, container):
    try:
        model = mod.read_env(ns, container, 'OPENAI_MODEL')
    except mod.AuditError as e:
        # No explicit model means the app uses gpt-4.1-mini.
        if 'expected exactly one injected value, found 0' not in str(e):
            raise CheckError('Cannot safely resolve agent model setting') from None
        model = 'gpt-4.1-mini'
    if not isinstance(model, str) or not re.fullmatch(r'[A-Za-z0-9_.:\-]{1,100}', model):
        raise CheckError('Unexpected OPENAI_MODEL format')
    return model


def forward_failure_category(output):
    """Do not print kubectl's raw output: it may contain internal addresses."""
    message = output.lower()
    if 'forbidden' in message or 'permission denied' in message or 'unauthorized' in message:
        return 'Kubernetes authorization denied port-forward (RBAC or API permissions)'
    if 'does not have a service port' in message or 'not found' in message:
        return 'Service/pod target missing or mismatched port'
    if 'unable to listen on any of the requested ports' in message or 'address already in use' in message:
        return 'Local port-forward listener could not bind'
    if 'unable to forward port' in message or 'connection refused' in message or 'no endpoints' in message:
        return 'Service target pod or remote listener not accepting port-forward'
    return 'No forwarding confirmation received; inspect kubectl access and service target'


def start_forward(mod, svc, ns, port):
    """Read kubectl's stdout AND stderr; kubectl prints readiness to stdout.

    The local port is allocated by kubectl (:REMOTE). Never print the merged
    output, which may contain internal network/cluster identifiers.
    """
    args = ['kubectl', '--context', mod.CTX, '-n', ns, 'port-forward', '--address', '127.0.0.1',
            'service/' + svc, ':' + str(port)]
    try:
        proc = subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, bufsize=0)
    except OSError:
        raise CheckError('Cannot launch loopback-only port forward') from None
    deadline = time.monotonic() + 20
    output = bytearray()
    matched = None
    try:
        while time.monotonic() < deadline:
            remaining = max(0, deadline - time.monotonic())
            r, _, _ = select.select([proc.stdout], [], [], min(0.3, remaining))
            if r:
                chunk = os.read(proc.stdout.fileno(), 4096)
                if chunk:
                    # Keep a bounded buffer only to categorize errors and parse
                    # kubectl's one-line readiness marker; never print contents.
                    output.extend(chunk)
                    if len(output) > 16384:
                        del output[:-16384]
                    ready = re.search(rb'Forwarding from 127\.0\.0\.1:(\d+) -> ' +
                                      str(port).encode('ascii') + rb'(?=\s|$)', output)
                    if ready:
                        matched = int(ready.group(1))
                        break
                elif proc.poll() is not None:
                    break
            if proc.poll() is not None:
                # Drain any last buffered bytes on process exit.
                more = proc.stdout.read(4096)
                if more:
                    output.extend(more)
                break
        if matched is None:
            category = forward_failure_category(output.decode('utf-8', errors='replace'))
            raise CheckError('Gateway Service port-forward could not be established: ' + category)
        if proc.poll() is not None:
            raise CheckError('Gateway Service port-forward exited immediately after establishing')
        return proc, matched
    except BaseException:
        stop_forward(proc)
        raise


def stop_forward(proc):
    if proc.poll() is None:
        proc.terminate()
    try:
        proc.wait(timeout=4)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=4)
    if proc.stdout:
        proc.stdout.close()


def call_once(url, hostname, remote_port, local_port, key, model):
    """Never print response bytes. Never pass secrets as CLI args or subprocess env."""
    parsed = urlsplit(url)
    if parsed.query or parsed.fragment or parsed.username or parsed.password:
        raise CheckError('Injected gateway URL contains forbidden fields')
    path = (parsed.path or '').rstrip('/') + '/chat/completions'
    if not path.startswith('/') or '//' in path or any(ord(c) < 32 for c in path):
        raise CheckError('Model gateway path is unsupported')
    target = 'http://127.0.0.1:' + str(local_port) + path
    payload = json.dumps({'model': model, 'temperature': 0,
                          'messages': [{'role': 'system', 'content': 'Return exactly OK.'},
                                       {'role': 'user', 'content': 'Say OK.'}]}).encode('utf-8')
    host_header = hostname + ':' + str(remote_port)
    req = Request(target, data=payload, method='POST', headers={
        'Host': host_header, 'API-Key': key, 'Authorization': '',
        'Content-Type': 'application/json', 'Accept': 'application/json'})
    opener = build_opener(ProxyHandler({}))
    try:
        with opener.open(req, timeout=25) as res:
            body = res.read(65536)
            code = res.status
    except HTTPError as e:
        code = e.code
        e.close()  # response body deliberately not inspected or logged
        return code, False
    except (URLError, TimeoutError, OSError):
        raise CheckError('Could not complete HTTP request through loopback forward') from None
    if code == 200:
        try:
            result = json.loads(body)
            choices = result.get('choices', [])
            return code, isinstance(choices, list) and len(choices) > 0
        except (TypeError, ValueError, AttributeError):
            return code, False
    return code, False


def classify(code, valid):
    if code == 200 and valid:
        return 'SUCCESS: authenticated model completion accepted by managed gateway'
    if code == 200:
        return 'HTTP 200 but response is not an OpenAI-compatible chat completion'
    if code in (401, 403):
        return 'Gateway rejected authentication/authorization; check injected key, API-Key header and access policies'
    if code == 404:
        return 'Route not found; inspect injected provider base URL and gateway route publication'
    if code in (400, 422):
        return 'Gateway rejected request/model/payload; inspect configured model and provider contract'
    if code == 429:
        return 'Gateway/provider rate limit or quota reached'
    if code in (502, 503, 504):
        return 'Gateway/proxy or upstream provider unavailable; inspect model provider connection and gateway logs'
    if code >= 500:
        return 'Gateway or upstream returned an internal error; inspect runtime logs'
    return 'Unexpected provider HTTP status; inspect gateway policy and provider configuration'


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--smoke', action='store_true', help='Make exactly one billable model call through loopback forwarded runtime gateway')
    args = parser.parse_args(argv)
    try:
        mod = base_module()
        ns, pod_name, cont, _ = mod.select_pod(mod.kget(['get','pods','-A']))
        url = mod.read_env(ns, cont, 'LLM_PROVIDER_URL')
        host, port, scheme, family = mod.endpoint_info(url)
        if scheme != 'http':
            raise CheckError('Only HTTP in-cluster runtime URLs can be port-forwarded; no HTTPS gateway assumptions')
        svc, svc_ns, endpoints = find_runtime_service(mod, host, port, ns)
        # No secret values or full provider URL are printed.
        print('Concierge managed LLM URL: matched to Kubernetes runtime Service in %s, TCP/%d.' % (svc_ns, port))
        print('Gateway runtime Service: %d ready endpoint address(es).' % endpoints)
        print('MODEL_DNS failure in BusyBox nslookup is not proof of failed app DNS; MODEL_TCP succeeded.')
        print('HTTP status and provider auth are NOT verified by TCP-only diagnostics.')
        if not args.smoke:
            print('READ-ONLY COMPLETE: run --smoke for ONE small real model request (may incur provider charges).')
            return 0
        model = choose_model(mod, ns, cont)
        key = read_model_key(mod, ns, cont)
        proc, local_port = start_forward(mod, svc, svc_ns, port)
        try:
            code, valid = call_once(url, host, port, local_port, key, model)
        finally:
            stop_forward(proc)
        print('Authenticated model gateway request: HTTP %d; %s.' % (code, classify(code, valid)))
        print('No key, model response body, or provider URL was printed. This verifies the forwarded gateway path, not pod egress.')
        return 0 if code == 200 and valid else 2
    except (CheckError, Exception) as e:
        if isinstance(e, CheckError):
            print('NOT VERIFIED: ' + str(e), file=sys.stderr)
        else:
            print('NOT VERIFIED: unexpected diagnostic failure (%s), no secrets printed' % type(e).__name__, file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
