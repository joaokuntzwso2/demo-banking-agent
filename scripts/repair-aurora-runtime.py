#!/usr/bin/env python3
"""Safe AMP v1.0.0 Banco Aurora six-agent port/readiness repair.

Default action is read-only audit. Mutations require --apply. No Git write,
no build, no Kubernetes mutation, no cluster reset, no secret manipulation.
"""
import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

from aurora_runtime_contract import (AGENTS, PROJECT, ENV, EXPECTED_PORT, ACTIVE,
    ContractError, deployment_entry, invoke_url, service_port, pod_readiness, select_build)

ROOT = Path(__file__).resolve().parent.parent
CTX = 'k3d-amp-local'
ORG = 'default'


def call(args, *, timeout=45):
    try:
        p = subprocess.run(args, cwd=ROOT, capture_output=True, text=True,
                           timeout=timeout, check=False)
    except subprocess.TimeoutExpired as e:
        raise ContractError(f'{args[0]} read/action timed out ({timeout}s)') from e
    if p.returncode:
        # Do not expose command arguments or unfiltered API response bodies.
        msg = (p.stderr or p.stdout).strip()
        raise ContractError(f'{args[0]} operation failed (code {p.returncode}): {msg[:350]}')
    return p.stdout


def json_cmd(args):
    try:
        obj = json.loads(call(args))
    except ValueError as e:
        raise ContractError(f'{args[0]} returned non-JSON response') from e
    if not isinstance(obj, dict):
        raise ContractError(f'{args[0]} expected JSON object')
    return obj


def api_deployments(agent):
    return json_cmd(['amctl', 'api', '-X', 'GET',
        f'/orgs/{ORG}/projects/{PROJECT}/agents/{agent}/deployments'])


def agent_detail(agent):
    obj = json_cmd(['amctl', 'agent', 'get', agent, '--project', PROJECT,
                    '--org', ORG, '--json'])
    data = obj.get('data') or {}
    if (data.get('name') != agent or data.get('projectName') != PROJECT or
        (data.get('inputInterface') or {}).get('port') != EXPECTED_PORT or
        not (data.get('configurations') or {}).get('enableApiKeySecurity')):
        raise ContractError(f'{agent}: Agent Manager identity, app port, or API-key security differs; no auto-repair')
    return data


def namespace_and_service(agent):
    svcs = json_cmd(['kubectl', '--context', CTX, 'get', 'services', '-A', '-o', 'json']).get('items', [])
    matches = [s for s in svcs if s.get('metadata', {}).get('name') == agent and
               s.get('metadata', {}).get('namespace', '').startswith('dp-default-banco-aurora-default-')]
    if len(matches) != 1:
        raise ContractError(f'{agent}: expected one managed Service; got {len(matches)}')
    service_port(matches[0], agent)
    return matches[0]['metadata']['namespace']


def kube_readiness(agent, ns):
    obj = json_cmd(['kubectl', '--context', CTX, '-n', ns, 'get', 'pods', '-o', 'json'])
    return pod_readiness(obj.get('items', []), agent)


def inventory(agent):
    obj = json_cmd(['amctl', 'agent', 'build', 'list', agent, '--project', PROJECT,
                    '--org', ORG, '--json'])
    branch = call(['git', 'branch', '--show-current']).strip()
    if branch != 'main':
        raise ContractError(f'Expected checked-out main branch, got {branch!r}')
    sha = call(['git', 'rev-parse', 'main']).strip().lower()
    build = select_build(obj, agent, sha)
    commit = build['buildParameters']['commitId']
    # Git resolves an abbreviation to a unique object, or exits nonzero.
    resolved = call(['git', 'rev-parse', '--verify', commit + '^{commit}']).strip().lower()
    if resolved != sha:
        raise ContractError(f'{agent}: build commit abbreviation does not resolve to local main')
    return build


def snapshot(agent, *, check_build=False):
    agent_detail(agent)
    ns = namespace_and_service(agent)
    entry = deployment_entry(api_deployments(agent), agent)
    ready, detail, port = kube_readiness(agent, ns)
    result = {'agent': agent, 'namespace': ns, 'status': entry['status'],
              'ready': ready, 'pod_detail': detail, 'port_env': port,
              'image': entry.get('imageId')}
    if check_build:
        result['build'] = inventory(agent)
    return result


def healthy(s):
    return s['status'] == ACTIVE and s['ready'] is True and s['port_env'] in (None, str(EXPECTED_PORT))


def concise(s):
    return (f'{s["agent"]}: status={s["status"]}, ready={s["ready"]}, '
            f'PORT={s["port_env"] or "not-a-literal"}; {s["pod_detail"]}')


def wait_health(agent, ns, *, seconds=300, interval=10):
    until = time.monotonic() + seconds
    last = ''
    while True:
        entry = deployment_entry(api_deployments(agent), agent)
        ready, detail, port = kube_readiness(agent, ns)
        s = {'agent': agent, 'namespace': ns, 'status': entry['status'],
             'ready': ready, 'port_env': port, 'pod_detail': detail,
             'image': entry.get('imageId')}
        summary = concise(s)
        if summary != last:
            print('  ' + summary, flush=True)
            last = summary
        if healthy(s):
            return s
        if time.monotonic() >= until:
            raise ContractError(f'{agent}: did not reach active + Ready in {seconds}s. '
                                f'Last observation: {summary}. Inspect pod startupProbe and logs.')
        time.sleep(min(interval, max(0, until - time.monotonic())))


def deploy(agent, build, *, concierge_urls=None):
    args = ['amctl', 'agent', 'deploy', agent, '--project', PROJECT, '--org', ORG,
            '--build-name', build['buildName'], '--env', f'PORT={EXPECTED_PORT}',
            '--env', 'USE_LLM_PROVIDER=true', '--env', 'REQUIRE_GOVERNED_LLM=true']
    if concierge_urls:
        for role, url in concierge_urls.items():
            args.extend(['--env', f'AGENT_{role.upper()}_URL={url}'])
    args.append('--yes')
    print(f'  Sending one deployment update for {agent}; existing build={build["buildName"]}.', flush=True)
    call(args, timeout=240)


def read_concierge_urls():
    urls = {}
    for role in ('account', 'card', 'dispute', 'pix', 'wellness'):
        agent = 'aurora-' + role
        s = snapshot(agent)
        if not healthy(s):
            raise ContractError(f'{agent} not active/Ready; cannot configure Concierge')
        urls[role] = invoke_url(api_deployments(agent), agent)
    details = agent_detail('aurora-concierge')
    serialized = json.dumps(details)
    missing = [f'AGENT_{role.upper()}_API_KEY' for role in urls if f'AGENT_{role.upper()}_API_KEY' not in serialized]
    if missing:
        raise ContractError('Concierge agent definition does not expose gateway key secret references; '
                            'refusing to inject secrets via plain --env. Inspect Agent Manager console.')
    return urls


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--apply', action='store_true', help='Explicitly deploy PORT=8000 to unhealthy agents')
    p.add_argument('--include-concierge', action='store_true',
                   help='Also reconcile Concierge after all specialists are active')
    p.add_argument('--agent', action='append', choices=AGENTS,
                   help='Limit to one or more agents (defaults to five specialists)')
    p.add_argument('--timeout', type=int, default=300, help='Readiness wait per deployment (60..1200s)')
    args = p.parse_args()
    if args.timeout < 60 or args.timeout > 1200:
        p.error('--timeout must be 60..1200')
    if 'aurora-concierge' in (args.agent or []) and not args.include_concierge:
        p.error('Concierge requires --include-concierge and healthy specialists')
    agents = list(dict.fromkeys(args.agent or AGENTS[:5]))
    if args.include_concierge and 'aurora-concierge' not in agents:
        agents.append('aurora-concierge')
    version = call(['amctl', 'version']).strip()
    if '1.0.0' not in version:
        raise ContractError('Expected Agent Manager CLI 1.0.0; got ' + version[:120])
    kube_context = call(['kubectl', 'config', 'current-context']).strip()
    # Always use explicit --context for Kubernetes queries; no global kubectl changes.
    print(f'Using amctl {version}; kubectl calls explicitly target {CTX} (current={kube_context}).')
    print('Preflight: checking all selected agent configuration, service ports, and deployment states.')
    initial = [snapshot(agent) for agent in agents]
    for s in initial:
        print('  ' + concise(s))
    if not args.apply:
        print('READ-ONLY audit complete. Run with --apply to modify deployment PORT only.')
        return 0
    # Preflight every build before making any changes; never create a managed build.
    builds = {}
    for agent in agents:
        builds[agent] = inventory(agent)
        current = next(s for s in initial if s['agent'] == agent)
        if current['image'] and current['image'] != builds[agent].get('imageId'):
            raise ContractError(f'{agent}: deployment points to an unexpected build image; inspect before repair')
    for agent in agents:
        current = snapshot(agent)
        if agent == 'aurora-concierge':
            urls = read_concierge_urls()
        else:
            urls = None
        if healthy(current) and agent != 'aurora-concierge':
            print(f'{agent}: already active/Ready; no deployment update.')
            continue
        if current['status'] == 'suspended':
            raise ContractError(f'{agent}: suspended intentionally; refusing automatic resume')
        if current['status'] == 'in-progress':
            print(f'{agent}: current rollout in-progress; allowing it to settle before deciding on a write.')
            try:
                wait_health(agent, current['namespace'], seconds=min(args.timeout, 360))
                if agent != 'aurora-concierge':
                    continue
            except ContractError:
                print(f'{agent}: prior rollout did not become healthy within grace period; inspecting status.')
            current = snapshot(agent)
            if current['status'] == 'in-progress':
                raise ContractError(f'{agent}: deployment remains in-progress; do not submit concurrent deploy')
        if agent == 'aurora-concierge' and healthy(current):
            print(f'{agent}: active already; no blind deployment. Run full reconciler to verify specialist URL mappings.')
            continue
        if current['status'] not in ('failed', 'not-deployed'):
            raise ContractError(f'{agent}: unexpected repair state {current["status"]}')
        deploy(agent, builds[agent], concierge_urls=urls)
        wait_health(agent, current['namespace'], seconds=args.timeout)
    print('All selected deployments checked. Run the full reconciler to validate AgentID/MCP/Portal/E2E.')
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (ContractError, OSError) as exc:
        print('SAFE STOP:', exc, file=sys.stderr)
        sys.exit(2)
