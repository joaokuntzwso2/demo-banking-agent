#!/usr/bin/env python3
"""Register a governed Banco Aurora MCP proxy through WSO2 Agent Manager amctl.

Uses documented AMP v1.0.0 API shapes. Does not assign roles to identities,
provision agents or publish unsecured MCP tools. Fails closed on mismatch.
"""
import argparse
import ast
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
from urllib.parse import urlsplit

# One action scope per tool. This allows the exact Python role matrix to be
# mirrored in AgentID without making 'read' universally readable by all roles.
ACTIONS = {
    'get_account_balance': 'balance-read',
    'list_account_transactions': 'account-tx-read',
    'get_card_status': 'card-read',
    'list_card_transactions': 'card-tx-read',
    'get_dispute_status': 'dispute-read',
    'list_beneficiaries': 'beneficiary-read',
    'get_upcoming_obligations': 'obligation-read',
    'get_human_cases': 'case-read',
    'request_human_escalation': 'case-create',
    'request_card_block': 'card-block-ask',
    'execute_approved_card_block': 'card-block-exec',
    'request_dispute': 'dispute-ask',
    'execute_approved_dispute': 'dispute-exec',
    'request_pix': 'pix-ask',
    'execute_approved_pix': 'pix-exec',
}
ROLE_TOOLS = {
    'account': ['get_account_balance','list_account_transactions','get_dispute_status'],
    'card': ['get_card_status','request_card_block','execute_approved_card_block'],
    'dispute': ['list_card_transactions','get_dispute_status','get_human_cases','request_dispute','execute_approved_dispute','request_human_escalation'],
    'pix': ['get_account_balance','list_beneficiaries','request_pix','execute_approved_pix'],
    'wellness': ['get_account_balance','get_upcoming_obligations'],
    'risk': ['list_beneficiaries'],
}


def fail(msg):
    raise RuntimeError(msg)


def apicall(path, method='GET', payload=None, allow_missing=False):
    cmd = ['amctl', 'api', path]
    if method != 'GET':
        cmd += ['-X', method, '--input', '-']
    result = subprocess.run(cmd, input=json.dumps(payload) if payload is not None else None,
                            text=True, capture_output=True)
    output = result.stdout.strip()
    if result.returncode:
        err = (result.stderr + '\n' + output).strip()
        if allow_missing and re.search(r'\b404\b|not found|NOT_FOUND', err, re.I):
            return None
        fail(f'amctl {method} {path} failed: {err[:700]}')
    if not output:
        return {}
    try:
        data = json.loads(output)
    except ValueError:
        fail(f'Expected JSON from amctl {path}; got non-JSON output (first 200 chars): {output[:200]!r}')
    if isinstance(data, dict) and data.get('status') == 'error':
        fail(f'amctl response error: {data.get("error")}')
    return data.get('data', data) if isinstance(data, dict) else data


def item_list(response):
    if isinstance(response,list):
        return response
    if isinstance(response,dict):
        for name in ('environments','items','results','roles','scopes','data','list'):
            if name in response:
                return item_list(response[name])
    return []



# AMP 1.0 environment listing can return {"environments":[...]}, an amctl
# envelope {"data":{"environments":[...]}}, or paginated {"items":[...]}.
# The previous item_list() omitted "environments" entirely. Never guess a
# UUID from the environment handle or silently select the first environment.
UUID_PATTERN = re.compile(r'^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$')


def environment_identity(record):
    if not isinstance(record, dict):
        return None, None
    nested = record.get('metadata') if isinstance(record.get('metadata'), dict) else {}
    names = [record.get(k) for k in ('name', 'handle', 'slug', 'environmentName')]
    names += [nested.get(k) for k in ('name', 'handle')]
    uuid_values = [record.get(k) for k in (
        'uuid', 'environmentUuid', 'environmentUUID', 'environmentId', 'id')]
    uuid_values += [nested.get(k) for k in ('uuid', 'environmentUuid', 'id')]
    # Some APIs use 'id' as the environment handle; some use it for the UUID.
    # Only consider it an identifier when it is a real UUID.
    uid = next((str(v) for v in uuid_values if isinstance(v, str) and UUID_PATTERN.fullmatch(v)), None)
    names = [str(x) for x in names if isinstance(x, str) and x]
    if isinstance(record.get('id'), str) and not UUID_PATTERN.fullmatch(record['id']):
        names.append(record['id'])
    return names, uid


def resolve_environment_uuid(response, handle):
    records = item_list(response)
    matches = []
    seen_names = []
    for record in records:
        names, uid = environment_identity(record)
        if names is None:
            continue
        seen_names.extend(names)
        if handle in names:
            if not uid:
                fail(f'Environment {handle!r} exists but its response has no UUID. Inspect: amctl api /orgs/<org>/environments')
            matches.append(uid)
    matches = sorted(set(matches))
    if len(matches) != 1:
        available = ', '.join(sorted(set(seen_names))) or '(none readable)'
        fail(f'Unable to identify a unique UUID for environment {handle!r}; available names: {available}. Run: amctl api /orgs/<org>/environments')
    return matches[0]

def actual_tool_names(repo_root: Path):
    path=repo_root/'mcp_server'/'app.py'
    tree=ast.parse(path.read_text())
    found=set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for deco in node.decorator_list:
            if isinstance(deco, ast.Call) and isinstance(deco.func, ast.Attribute) and deco.func.attr == 'tool':
                found.add(node.name)
    return found


def discovered_tool_names(obj):
    """Extract discovered names for validation/logging, including legacy responses."""
    names = set()
    def walk(value, is_tools=False):
        if isinstance(value, dict):
            if is_tools and isinstance(value.get('name'), str):
                names.add(value['name'])
            for key, sub in value.items():
                walk(sub, is_tools or key in ('tools','toolInfos','toolDefinitions'))
        elif isinstance(value, list):
            for sub in value:
                if is_tools and isinstance(sub, str):
                    names.add(sub)
                else:
                    walk(sub, is_tools)
    walk(obj)
    return names


def validate_tool_descriptors(tools):
    """Normalize *full* MCP Tool objects. AMP's capabilities.tools is []map,
    not []string. Never synthesize a schema from only a tool name.

    See Agent Manager Go models.MCPProxyCapabilities.Tools, json:'tools'.
    A tool's inputSchema must be the real one returned by MCP tools/list.
    """
    if not isinstance(tools, list) or not tools:
        fail('Expected a nonempty MCP tool descriptor array')
    by_name = {}
    for t in tools:
        if not isinstance(t, dict):
            fail('Tool capability was a string/non-object; Agent Manager requires full MCP Tool JSON objects (name, inputSchema, etc.)')
        name = t.get('name')
        schema = t.get('inputSchema')
        if not isinstance(name, str) or not re.fullmatch(r'[a-zA-Z][a-zA-Z0-9_-]*', name):
            fail(f'Invalid MCP Tool name {name!r}')
        if name in by_name:
            fail(f'Duplicate MCP Tool name {name!r}')
        if not isinstance(schema, dict) or schema.get('type') != 'object':
            fail(f'MCP Tool {name!r} has no valid inputSchema object; do not invent a schema')
        if any(not isinstance(k, str) for k in t):
            fail(f'MCP Tool {name!r} has a non-string JSON property')
        # Preserve genuine metadata from MCP tools/list, including its input
        # schema, descriptions and annotations; all values must be JSON safe.
        try:
            json.dumps(t, allow_nan=False)
        except (TypeError, ValueError) as exc:
            fail(f'Unserializable MCP Tool descriptor for {name}: {exc}')
        by_name[name] = t
    return [by_name[k] for k in sorted(by_name)]


def discovered_tool_descriptors(obj):
    """Find a full tool list in a nested control-plane discovery response.
    Refuse name-only lists; those decode as HTTP 400 in AMP create request.
    """
    matches = []
    def walk(value):
        if isinstance(value, dict):
            for k, v in value.items():
                if k in ('tools', 'toolInfos', 'toolDefinitions') and isinstance(v, list):
                    matches.append(v)
                walk(v)
        elif isinstance(value, list):
            for item in value:
                walk(item)
    walk(obj)
    for tools in matches:
        if tools:
            return validate_tool_descriptors(tools)
    fail('Control-plane discovery has no full MCP tool descriptors. For the private same-cluster backend, use AMP_DISCOVERY_MODE=cluster.')


def build_proxy_payload(proxy_id, url, environment_uuid, tool_descriptors):
    """Create the documented v1.0.0 org-level MCP proxy JSON body."""
    tools = validate_tool_descriptors(tool_descriptors)
    upstream = {'url': url}
    origin_key = os.getenv('AURORA_MCP_ORIGIN_KEY', '').strip()
    if origin_key:
        parts = urlsplit(url)
        if parts.scheme != 'https' or not parts.hostname or parts.username or parts.password:
            fail('AURORA_MCP_ORIGIN_KEY requires a public HTTPS upstream with no embedded URL credentials')
        if not re.fullmatch(r'[A-Za-z0-9_-]{32,128}', origin_key):
            fail('AURORA_MCP_ORIGIN_KEY must be a 32-128 character URL-safe random secret')
        upstream['auth'] = {'type': 'api-key', 'header': 'X-Aurora-Origin-Key', 'value': origin_key}
    return {
        'id': proxy_id,
        'name': 'Banco Aurora Banking MCP',
        'description': 'Private synthetic Banco Aurora banking tools, OAuth-governed',
        'version': 'v1.0',
        'context': '/' + proxy_id,
        'mcpSpecVersion': '2025-06-18',
        'endpoints': [{
            'id': 'primary', 'name': 'primary',
            'upstream': {'main': upstream},
            'capabilities': {'tools': tools},
            'security': {'enabled': True, 'identity': {'enabled': True}},
            'environments': [{'environmentUuid': environment_uuid}],
        }],
    }



# WSO2 Agent Manager 1.0.0 intentionally refuses non-public IP addresses
# in the control-plane Fetch Server Info API. For this known k3d-only MCP
# deployment, an operator may explicitly opt in to discovering its tools
# from inside the *running MCP container* instead. The normal AMP Create
# Proxy API is still used, including OAuth, scopes and roles. No Agent
# Manager setting, host DNS rule or SSRF protection is disabled.
CLUSTER_DISCOVERY_PROGRAM = r'''
import asyncio, json, sys
import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

async def run():
    timeout = httpx.Timeout(20.0, read=30.0)
    async with httpx.AsyncClient(timeout=timeout) as http_client:
        async with streamable_http_client(sys.argv[1], http_client=http_client) as (read, write, _):
            async with ClientSession(read, write) as session:
                await session.initialize()
                result = await session.list_tools()
                if getattr(result, "nextCursor", None):
                    raise RuntimeError("MCP tool pagination requires explicit review")
                # AMP's MCPProxyCapabilities.Tools is []map[string]any.
                # Sending name-only strings caused HTTP 400 Invalid request body.
                tools = [tool.model_dump(mode="json", by_alias=True, exclude_none=True)
                         for tool in result.tools]
                print("AURORA_MCP_TOOLS_JSON=" + json.dumps(tools, allow_nan=False))

asyncio.run(run())
'''

def discover_cluster_tools(url, namespace, context):
    """Discover live MCP tools via kubectl exec, never via the SSRF-restricted AMP API.

    Only the exact private service DNS, port, and path from this repo-local
    installation are permitted. A different URL is rejected, not probed.
    """
    if not re.fullmatch(r'[a-z0-9](?:[a-z0-9-]*[a-z0-9])?', namespace):
        fail('Invalid KUBE_NAMESPACE for cluster discovery')
    if not context or not re.fullmatch(r'[A-Za-z0-9_.-]+', context):
        fail('Invalid KUBE_CONTEXT for cluster discovery')
    parts = urlsplit(url)
    expected_host = f'aurora-mcp.{namespace}.svc.cluster.local'
    if (parts.scheme != 'http' or parts.hostname != expected_host
            or parts.port != 8101 or parts.path != '/mcp'
            or parts.query or parts.fragment or parts.username or parts.password):
        fail('Cluster discovery is restricted to the exact private Aurora MCP service: '
             f'http://{expected_host}:8101/mcp')
    command = ['kubectl','--context',context,'-n',namespace,
               'exec','deployment/aurora-mcp','--','python','-c',
               CLUSTER_DISCOVERY_PROGRAM,url]
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=90)
    except (OSError, subprocess.TimeoutExpired) as exc:
        fail(f'Unable to run in-cluster MCP discovery: {exc}')
    if result.returncode != 0:
        fail('In-cluster MCP discovery failed; check aurora-mcp pod health, '
             'MCP_ALLOWED_HOSTS and SDK logs. Diagnostic: '
             f'{result.stderr[-900:]}')
    markers = [line.split('=',1)[1] for line in result.stdout.splitlines()
               if line.startswith('AURORA_MCP_TOOLS_JSON=')]
    if len(markers) != 1:
        fail('In-cluster MCP discovery did not produce exactly one tool inventory')
    try:
        names = json.loads(markers[0])
    except json.JSONDecodeError as exc:
        fail(f'Invalid in-cluster MCP tool inventory: {exc}')
    return validate_tool_descriptors(names)

def is_quick_tunnel_upstream(value):
    """Only an HTTPS Cloudflare Quick Tunnel /mcp address can be rotated."""
    if not isinstance(value, str):
        return False
    parts = urlsplit(value)
    return (parts.scheme == 'https' and parts.port is None
            and not parts.username and not parts.password
            and not parts.query and not parts.fragment and parts.path == '/mcp'
            and bool(re.fullmatch(r'[a-z0-9-]+\.trycloudflare\.com', parts.hostname or '')))


def matching_gateway(org, env_handle, env_uuid, *, attempts=30, interval=3):
    """Wait until an ACTIVE EGRESS/BOTH gateway maps to the exact env UUID."""
    for n in range(attempts):
        result = subprocess.run(
            ['amctl', 'gateway', 'list', '--org', org, '--env', env_handle, '--json'],
            capture_output=True, text=True, timeout=40)
        if result.returncode:
            fail('Cannot verify gateway environment mapping via amctl gateway list; '
                 'no MCP proxy mutation attempted. ' + result.stderr[:300])
        try:
            response = json.loads(result.stdout)
        except ValueError:
            fail('Gateway list did not return JSON; refusing to guess gateway mapping')
        gateways = response.get('data', response).get('gateways') if isinstance(response, dict) else None
        if not isinstance(gateways, list):
            fail('Gateway inventory lacks data.gateways[]; no proxy mutation attempted')
        names = []
        for gateway in gateways:
            if not isinstance(gateway, dict):
                continue
            associated = gateway.get('environments') or []
            if not isinstance(associated, list):
                continue
            uuids = {x.get('id') or x.get('uuid') for x in associated if isinstance(x, dict)}
            if (env_uuid in uuids and gateway.get('status') == 'ACTIVE'
                    and gateway.get('gatewayType') in ('BOTH', 'EGRESS')):
                names.append(gateway.get('name'))
        if len(names) == 1 and isinstance(names[0], str):
            return names[0]
        if len(names) > 1:
            fail('Multiple ACTIVE egress gateways match; explicit endpoint gateway selection is required')
        if n + 1 < attempts:
            if n == 0 or (n + 1) % 5 == 0:
                print(f'No ACTIVE egress gateway mapped to live {env_handle!r} UUID yet; waiting ({n+1}/{attempts}).', flush=True)
            time.sleep(interval)
    fail(f'No ACTIVE EGRESS/BOTH gateway associated with {env_handle!r} '
         f'UUID {env_uuid} after {attempts} checks. Check gateway environment registration.')


def wait_proxy_deployed(path, live_uuid, expected_url, attempts=30, interval=3):
    """The role endpoint rejects grants until the per-environment gateway is deployed."""
    for n in range(attempts):
        current = apicall(path)
        eps = current.get('endpoints') if isinstance(current, dict) else None
        if not isinstance(eps, list) or len(eps) != 1:
            fail('MCP proxy endpoint shape changed while awaiting deployment')
        ep = eps[0]
        entries = ep.get('environments') or []
        if len(entries) != 1 or entries[0].get('environmentUuid') != live_uuid:
            fail('MCP proxy deployment environment UUID changed or is stale')
        current_url = ((ep.get('upstream') or {}).get('main') or {}).get('url')
        if current_url != expected_url:
            fail('MCP proxy HTTPS upstream changed during deployment')
        status = entries[0].get('deploymentStatus')
        if status == 'Deployed':
            print(f'Confirmed MCP proxy deployed to live environment UUID {live_uuid}.')
            return
        if n == 0 or (n + 1) % 5 == 0:
            print(f'MCP proxy deployment pending (status={status!r}; check {n+1}/{attempts}).', flush=True)
        if n + 1 < attempts:
            time.sleep(interval)
    fail('MCP proxy did not reach Deployed; inspect amp-api logs for '
         '"no gateway mapped to environment" or gateway deployment errors. '
         'No roles have been granted.')


def verify_dns_from_cluster(url, context, *, namespace='wso2-amp'):
    """Wait for actual Kubernetes DNS readiness for a new HTTPS tunnel hostname.

    The check uses ONE disposable BusyBox pod, avoiding a new image pull and
    scheduling race on each DNS retry. Actual resolution always goes through
    the pod's configured nameserver. In the dedicated local demo only, a
    proven CoreDNS/public-DNS discrepancy triggers a narrowly scoped, backed-
    up CoreDNS forwarding-zone repair. It never substitutes an IP for the
    target URL or bypasses Agent Manager URL/SSRF checks.
    """
    host = urlsplit(url).hostname
    if not host or not is_quick_tunnel_upstream(url):
        return
    if not context or not re.fullmatch(r'[A-Za-z0-9_.-]+', context):
        fail('Missing trusted KUBE_CONTEXT for in-cluster DNS verification')
    try:
        wait_seconds = int(os.getenv('AURORA_MCP_DNS_WAIT_SECONDS', '180'))
    except ValueError:
        fail('AURORA_MCP_DNS_WAIT_SECONDS must be an integer')
    if not 30 <= wait_seconds <= 360:
        fail('AURORA_MCP_DNS_WAIT_SECONDS must be between 30 and 360')
    pod = f'aurora-mcp-dns-{os.getpid()}'
    prefix = ['kubectl', '--context', context, '-n', namespace]
    last_result = ''
    def run_kubectl(args, *, timeout=25):
        try:
            return subprocess.run(prefix + args, capture_output=True,
                                  text=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            return None
    def compact(result):
        if result is None:
            return 'command timed out'
        return ((result.stdout or '') + '\n' + (result.stderr or '')).strip()[-1200:]
    try:
        create = run_kubectl(['run', pod, '--restart=Never',
                              '--image=busybox:1.36.1', '--command',
                              '--', 'sleep', '450'], timeout=30)
        if create is None or create.returncode != 0:
            fail('Failed to create Kubernetes DNS diagnostic Pod: ' + compact(create))
        ready = run_kubectl(['wait', '--for=condition=Ready', 'pod/' + pod,
                             '--timeout=75s'], timeout=85)
        if ready is None or ready.returncode != 0:
            state = run_kubectl(['get', 'pod', pod, '-o', 'wide'], timeout=15)
            describe = run_kubectl(['describe', 'pod', pod], timeout=15)
            fail('Kubernetes DNS diagnostic Pod did not become Ready. '
                 'This may be image pull, scheduling, or container startup—not DNS. '
                 'Pod: ' + compact(state) + ' Details: ' + compact(describe))
        print(f'Kubernetes DNS diagnostic Pod ready; waiting up to {wait_seconds}s '
              f'for {host} through its configured DNS.', flush=True)
        stop_at = time.monotonic() + wait_seconds
        attempt = 0
        repaired_dns = False
        while True:
            attempt += 1
            check = run_kubectl(['exec', pod, '--', 'nslookup', host], timeout=20)
            if check is not None and check.returncode == 0:
                print(f'In-cluster DNS verified for {host} (attempt {attempt}).', flush=True)
                return
            last_result = compact(check)
            # Dedicated local demo only. Repair a reproducible DNS discrepancy
            # after two failed checks, not a transport/scheduling fault. The
            # verification Pod still must resolve via its normal CoreDNS.
            if (attempt == 2 and not repaired_dns and 'NXDOMAIN' in last_result
                    and os.getenv('AURORA_MCP_AUTO_REPAIR_DNS', '1') == '1'
                    and context == 'k3d-amp-local'):
                normal_dns = run_kubectl(['exec', pod, '--', 'nslookup',
                                          'cloudflare.com'], timeout=20)
                public_dns = run_kubectl(['exec', pod, '--', 'nslookup',
                                          host, '1.1.1.1'], timeout=20)
                # We need a proven response for the exact target hostname,
                # not merely success connecting to the upstream resolver.
                resolved = (public_dns is not None
                            and public_dns.returncode == 0
                            and re.search(r'(?m)^Name:\s*' + re.escape(host)
                                          + r'\s*$', public_dns.stdout or ''))
                if (normal_dns is not None and normal_dns.returncode == 0
                        and resolved):
                    helper = (Path(__file__).resolve().parents[1]
                              / 'scripts/aurora-repair-tunnel-coredns.py')
                    if not helper.is_file():
                        fail('Dedicated DNS recovery helper missing; cannot repair CoreDNS')
                    print('Confirmed: CoreDNS NXDOMAIN while public DNS resolves '
                          'the exact Quick Tunnel hostname. Repairing only the '
                          'trycloudflare.com forwarding zone in dedicated amp-local.',
                          flush=True)
                    fix = subprocess.run(
                        [sys.executable, str(helper), '--context', context,
                         '--host', host], capture_output=True, text=True,
                        timeout=90)
                    if fix.stdout.strip():
                        print(fix.stdout.strip(), flush=True)
                    if fix.returncode != 0:
                        fail('Dedicated CoreDNS recovery failed: '
                             + (fix.stderr or fix.stdout).strip()[-1200:])
                    repaired_dns = True
                    # ConfigMap projection + CoreDNS reload are asynchronous.
                    stop_at = max(stop_at, time.monotonic() + 120)
            remaining = stop_at - time.monotonic()
            if remaining <= 0:
                break
            if attempt == 1 or attempt % 3 == 0:
                print(f'Kubernetes DNS not ready for {host}; retrying '
                      f'(attempt {attempt}, {int(remaining)}s remaining).', flush=True)
            time.sleep(min(10, remaining))
        # Collect comparison lookups only for *diagnosis*. They do not make
        # proxy creation proceed and never change the nameserver in use.
        normal = run_kubectl(['exec', pod, '--', 'nslookup', 'cloudflare.com'], timeout=20)
        public = run_kubectl(['exec', pod, '--', 'nslookup', host, '1.1.1.1'], timeout=20)
        fail('Kubernetes DNS never resolved tunnel hostname during bounded wait. '
             f'Target result: {last_result}; '
             f'Known public domain via cluster DNS: {compact(normal)}; '
             f'Target via diagnostic-only public resolver 1.1.1.1: {compact(public)}. '
             'Check CoreDNS upstream forwarding, DNS egress, and live tunnel lifecycle. '
             'No DNS settings or SSRF protections were altered.')
    finally:
        # Avoid retaining diagnostic resources even after Pod/pull failure.
        run_kubectl(['delete', 'pod', pod, '--ignore-not-found=true',
                     '--wait=false'], timeout=20)


def ensure_known_proxy(path, expected_url, expected_id, desired_payload=None,
                       *, valid_environment_uuids=None):
    """Repair only a positively identified Aurora proxy with an orphan env UUID.

    Fail closed if the old environment is still live, or any security/tool/scope
    invariants differ. Never replay a redacted GET payload as a PUT: use the
    freshly built payload containing the validated current origin key.
    """
    existing = apicall(path, allow_missing=True)
    if existing is None:
        return False
    if desired_payload is None:
        fail('Existing proxy reconciliation requires the complete verified payload')
    if (not isinstance(existing, dict) or existing.get('id') != expected_id or
            existing.get('name') != 'Banco Aurora Banking MCP' or
            existing.get('context') != '/aurora-banking'):
        fail('Existing proxy identity/name/context not exclusively owned by Banco Aurora')
    endpoints = existing.get('endpoints')
    if not isinstance(endpoints, list) or len(endpoints) != 1:
        fail('Existing MCP proxy has unexpected endpoint count')
    ep = endpoints[0]
    desired = desired_payload['endpoints'][0]
    if ep.get('id') != 'primary' or ep.get('name') != 'primary':
        fail('Existing proxy does not have the expected primary endpoint')
    upstream = ((ep.get('upstream') or {}).get('main') or {})
    old_url = upstream.get('url')
    if not old_url:
        fail('Existing proxy has no upstream URL; refusing modification')
    auth = upstream.get('auth') or {}
    if auth.get('type') != 'api-key' or auth.get('header') != 'X-Aurora-Origin-Key':
        fail('Existing proxy origin authentication is not Aurora configuration')
    if not (ep.get('security') or {}).get('enabled') or not (ep.get('security') or {}).get('identity', {}).get('enabled'):
        fail('Existing proxy lacks expected OAuth/AgentID security')
    old_tools = (ep.get('capabilities') or {}).get('tools') or []
    if (len(old_tools) != len(ACTIONS)
            or {x.get('name') for x in old_tools if isinstance(x, dict)} != set(ACTIONS)):
        fail('Existing proxy tools differ; refusing automatic update')
    old_envs = ep.get('environments') or []
    if len(old_envs) != 1 or not isinstance(old_envs[0], dict):
        fail('Existing proxy has multiple or malformed environment mappings')
    old_env = old_envs[0].get('environmentUuid')
    new_env = desired['environments'][0]['environmentUuid']
    if not UUID_PATTERN.fullmatch(str(old_env)):
        fail('Existing proxy environment UUID malformed')
    if old_env != new_env:
        if valid_environment_uuids is None or old_env in valid_environment_uuids:
            fail('Existing proxy environment differs but is still live or unverified; refusing automatic remapping')
        print(f'Reconciling orphaned proxy environment UUID {old_env} to live UUID {new_env}.')
    requires_url_rotation = old_url != expected_url
    if requires_url_rotation:
        if os.getenv('AURORA_ALLOW_EPHEMERAL_PROXY_UPDATE', '').lower() != 'true':
            fail('An existing MCP proxy uses a different upstream; run the full startup for safe Quick Tunnel rotation')
        if not (is_quick_tunnel_upstream(old_url) and is_quick_tunnel_upstream(expected_url)):
            fail('Refusing automatic rotation of a non-Quick-Tunnel upstream')
    scopes = item_list(apicall(path + '/scopes'))
    actual = {x.get('action'): x.get('tools') for x in scopes if isinstance(x, dict)}
    if (len(actual) != len(scopes) or
            any(action not in ACTIONS.values() or actual[action] != [tool]
                for tool, action in ACTIONS.items() if action in actual)):
        fail('Existing MCP tool scopes have unexpected or duplicate mappings; refusing proxy overwrite')
    # Known partial scope creation is safe to complete via ensure_scopes below.
    if old_env != new_env or requires_url_rotation or old_envs[0].get('deploymentStatus') != 'Deployed':
        apicall(path, 'PUT', desired_payload)
        updated = apicall(path)
        uep = (updated.get('endpoints') or [{}])[0]
        if (uep.get('environments') or [{}])[0].get('environmentUuid') != new_env:
            fail('Proxy PUT returned, but live environment UUID was not persisted')
        if (((uep.get('upstream') or {}).get('main') or {}).get('url') != expected_url):
            fail('Proxy PUT returned, but verified HTTPS URL was not persisted')
        print('Reconciled existing Aurora MCP proxy without deleting identity or scopes.')
    else:
        print('Existing proxy uses the live environment and verified HTTPS upstream; no update needed.')
    return True


def ensure_scopes(org, proxy_id, entries):
    path=f'/orgs/{org}/mcp-proxies/{proxy_id}/scopes'
    existing=item_list(apicall(path))
    by_action={item.get('action'):item for item in existing if isinstance(item,dict)}
    for tool,action in entries.items():
        if action in by_action:
            old=set(by_action[action].get('tools') or [])
            if old != {tool}:
                fail(f'Existing scope {proxy_id}:{action} differs from expected mapping. Review manually.')
            continue
        apicall(path,'POST',{'action':action,'description':f'Banco Aurora permission: {tool}','tools':[tool]})
        print(f'  scope: {proxy_id}:{action} → {tool}')
    scopes=item_list(apicall(path))
    found={item.get('action'):set(item.get('tools',[])) for item in scopes if isinstance(item,dict)}
    for t,a in entries.items():
        if found.get(a)!={t}:
            fail(f'Scope {a} was not returned with expected tool {t}')
    print(f'Verified {len(entries)} tool-specific OAuth scopes.')


def ensure_roles(org, env, proxy_id):
    path=f'/orgs/{org}/environments/{env}/agent-identities/roles'
    existing=item_list(apicall(path))
    by_name={item.get('name'):item for item in existing if isinstance(item,dict)}
    for role,tools in ROLE_TOOLS.items():
        name=f'aurora-{role}'
        scopes=[f'{proxy_id}:{ACTIONS[t]}' for t in tools]
        if name in by_name:
            old=set(by_name[name].get('scopes') or [])
            if old and old!=set(scopes):
                fail(f'Existing {name} has different scopes; manual reconciliation required')
            print(f'  role exists: {name} (no identity assignment changed)')
            continue
        apicall(path,'POST',{'name':name,'description':f'Least privilege for Banco Aurora {role} agent', 'scopes':scopes})
        print(f'  role created: {name}')
    print('Role creation done. Assign roles to deployed AgentID identities manually.')


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--repo', required=True)
    parser.add_argument('--dry-run', action='store_true')
    args=parser.parse_args()
    repo=Path(args.repo)
    source_tools=actual_tool_names(repo)
    if source_tools!=set(ACTIONS):
        fail(f'Source MCP tool inventory differs! Missing: {sorted(set(ACTIONS)-source_tools)}; new: {sorted(source_tools-set(ACTIONS))}. Do NOT publish unscoped tools.')
    for role,tools in ROLE_TOOLS.items():
        if not set(tools).issubset(ACTIONS):
            fail(f'Unknown tool in {role} role')
    url=os.getenv('AURORA_MCP_UPSTREAM_URL','').strip()
    org=os.getenv('AMP_ORG','default').strip()
    env_uuid=os.getenv('AMP_ENVIRONMENT_UUID','').strip()
    env_handle=os.getenv('AMP_ENVIRONMENT_HANDLE','default').strip()
    proxy_id=os.getenv('AMP_PROXY_ID','aurora-banking').strip()
    if not re.fullmatch('[a-z][a-z0-9-]{1,62}',proxy_id):
        fail('AMP_PROXY_ID must be lowercase kebab-case, at least 2 characters')
    if not re.fullmatch('[a-zA-Z0-9_-]+',org) or not re.fullmatch('[a-zA-Z0-9_-]+',env_handle):
        fail('Invalid AMP_ORG or AMP_ENVIRONMENT_HANDLE')
    if not url.startswith(('https://','http://')) or not url.endswith('/mcp'):
        fail('AURORA_MCP_UPSTREAM_URL must be an actual reachable http(s) URL ending /mcp')
    print(f'Validated {len(source_tools)} source MCP tools and six least-privilege role mappings.')
    if args.dry_run:
        print('DRY RUN: no Agent Manager API calls made.')
        return
    # The local env file survives Colima/k3d reinstallations; its UUID does not.
    # Never use a persisted override as the source of truth for proxy deployment.
    env_records = apicall(f'/orgs/{org}/environments')
    live_uuid = resolve_environment_uuid(env_records, env_handle)
    if env_uuid and env_uuid != live_uuid:
        print(f'Ignored stale AMP_ENVIRONMENT_UUID for {env_handle!r}; using live Agent Manager UUID {live_uuid}.')
    env_uuid = live_uuid
    gateway = matching_gateway(org, env_handle, env_uuid)
    print(f'Resolved live environment {env_handle!r}: {env_uuid}; gateway: {gateway}.')
    # Verify that DNS works inside the cluster, not merely from macOS via DoH.
    if is_quick_tunnel_upstream(url):
        verify_dns_from_cluster(url, os.getenv('KUBE_CONTEXT', ''))
    pbase=f'/orgs/{org}/mcp-proxies'
    mode = os.getenv('AMP_DISCOVERY_MODE', 'control-plane').strip().lower()
    if mode == 'cluster':
        print('Discovering live MCP tools in trusted Aurora Kubernetes pod ...')
        discovery_url = os.getenv('AURORA_MCP_DISCOVERY_URL', url).strip()
        tool_descriptors=discover_cluster_tools(
            discovery_url, os.getenv('KUBE_NAMESPACE', 'aurora-demo'),
            os.getenv('KUBE_CONTEXT', ''))
    elif mode == 'control-plane':
        print('Discovering tools from Agent Manager control plane ...')
        discovered=apicall(f'{pbase}/fetch-server-info','POST',{'url':url})
        tool_descriptors=discovered_tool_descriptors(discovered)
    else:
        fail('AMP_DISCOVERY_MODE must be control-plane or cluster')
    upstream_tools={t['name'] for t in tool_descriptors}
    if upstream_tools != source_tools:
        fail(f'Live MCP discovery does not match source! Found={sorted(upstream_tools)}; expected={sorted(source_tools)}. Check connectivity and SDK response.')
    print('Live MCP discovery verified exactly the expected 15 tools with real input schemas.')
    proxy_path=f'{pbase}/{proxy_id}'
    payload=build_proxy_payload(proxy_id, url, env_uuid, tool_descriptors)
    live_records = item_list(env_records)
    if isinstance(env_records, dict) and type(env_records.get('total')) is int:
        if env_records['total'] != len(live_records):
            fail('Environment inventory is paginated; cannot prove an old UUID is orphaned')
    live_ids = {environment_identity(rec)[1] for rec in live_records if isinstance(rec, dict)}
    if not ensure_known_proxy(proxy_path,url,proxy_id,payload,
                              valid_environment_uuids=live_ids):
        try:
            for attempt in range(1, 4):
                try:
                    apicall(pbase, 'POST', payload)
                    break
                except RuntimeError as exc:
                    # AMP 1.0 may transiently have a negative Kubernetes DNS
                    # cache even after a diagnostic pod resolves the new URL.
                    # An explicit 400 DNS-rejection is retryable ONLY if a
                    # fresh GET proves the proxy was not persisted.
                    if not re.search(r'url host could not be resolved|no such host', str(exc), re.I) or attempt == 3:
                        raise
                    existing_after_error = apicall(proxy_path, allow_missing=True)
                    if existing_after_error is not None:
                        fail('MCP proxy exists following a rejected POST; refuse blind duplicate creation')
                    print(f'AMP DNS cache rejected new Quick Tunnel hostname; retrying ({attempt}/2).', flush=True)
                    time.sleep(10)
        except RuntimeError as exc:
            if 'Invalid request body' in str(exc) or 'invalid request body' in str(exc):
                fail('Agent Manager still rejected the structured MCP Tool payload as Invalid request body. '
                     'Read the live API validation logs: kubectl -n wso2-amp logs '
                     'deployment/amp-api --since=5m --tail=150. No tools or scopes were created. '
                     f'Original error: {exc}')
            if 'non-public IP' in str(exc) or 'invalid URL' in str(exc):
                fail('Agent Manager also rejected the private upstream during proxy creation. '
                     'This is a separate server-side policy; the supported next step is a '
                     'properly secured HTTPS endpoint or a vendor-supported private-endpoint '
                     'allowlist. Do not change DNS or disable SSRF protections. '
                     f'Original error: {exc}')
            raise
        print(f'Created identity-secured MCP proxy {proxy_id}.')
    ensure_scopes(org,proxy_id,ACTIONS)
    # Scope creation may trigger gateway reconciliation; role POST must wait.
    wait_proxy_deployed(proxy_path, env_uuid, url)
    if os.getenv('AMP_CREATE_ROLES','true').lower()=='true':
        ensure_roles(org,env_handle,proxy_id)
    print('\nPROXY SETUP COMPLETE. Confirm gateway artifact is Active in Agent Manager Console.')
    print(f'Use MCP Proxy "{proxy_id}" in the five specialist Tool Configurations, URL variable MCP_SERVER_URL.')
    print('Next: deploy agents, attach this proxy, assign aurora-* roles to their AgentID identities.')

if __name__=='__main__':
    try:
        main()
    except (RuntimeError, OSError) as exc:
        print('ERROR:',exc,file=sys.stderr)
        sys.exit(1)
