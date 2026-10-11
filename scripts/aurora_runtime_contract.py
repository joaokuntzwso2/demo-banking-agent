"""Read-only v1.0.0 AMP deployment and Kubernetes readiness contracts.

No secrets, no Kubernetes writes. Use this module as the single source of
truth for deployment identity, port probes, and external gateway URLs.
"""
from urllib.parse import urlsplit

ROLES = ('account', 'card', 'dispute', 'pix', 'wellness', 'concierge')
AGENTS = tuple('aurora-' + x for x in ROLES)
ENV = 'default'
PROJECT = 'banco-aurora'
EXPECTED_PORT = 8000
ACTIVE = 'active'
KNOWN_STATES = {'active', 'in-progress', 'failed', 'not-deployed', 'suspended'}

class ContractError(RuntimeError):
    pass


def require(cond, message):
    if not cond:
        raise ContractError(message)


def deployment_entry(payload, agent, env=ENV):
    require(agent in AGENTS, f'Unexpected agent name: {agent}')
    require(isinstance(payload, dict), f'{agent}: deployment payload must be object')
    require(env in payload and isinstance(payload[env], dict),
            f'{agent}: missing environment {env!r} in deployment response')
    entry = payload[env]
    state = entry.get('status')
    require(state in KNOWN_STATES, f'{agent}: unknown deployment status {state!r}; fail closed')
    return entry


def invoke_url(payload, agent, env=ENV, *, require_active=True):
    entry = deployment_entry(payload, agent, env)
    if require_active:
        require(entry['status'] == ACTIVE,
                f'{agent}: deployment is {entry["status"]!r}, not active; cannot supply invocation URL')
    endpoints = entry.get('endpoints')
    require(isinstance(endpoints, list), f'{agent}: no deployment endpoints list')
    candidates = []
    for ep in endpoints:
        require(isinstance(ep, dict), f'{agent}: malformed endpoint')
        if ep.get('visibility') != 'external':
            continue
        require(ep.get('name') == agent + '-endpoint',
                f'{agent}: unexpected external endpoint name')
        raw = ep.get('url')
        require(isinstance(raw, str) and raw, f'{agent}: missing endpoint URL')
        parsed = urlsplit(raw)
        require(parsed.scheme in ('http', 'https') and parsed.hostname and
                parsed.username is None and parsed.password is None and
                not parsed.query and not parsed.fragment and parsed.path.rstrip('/') == '/' + agent,
                f'{agent}: invalid gateway URL structure')
        # Fixed local k3d gateway identity confirmed from the customer's
        # live Agent Manager response. No invented routes or arbitrary hosts.
        require(parsed.hostname == 'default-default.am-gateway.localhost' and
                parsed.port == 19080 and parsed.scheme == 'http',
                f'{agent}: unexpected local gateway origin; inspect deployment metadata')
        candidates.append(raw.rstrip('/'))
    require(len(candidates) == 1,
            f'{agent}: expected exactly one external gateway endpoint; got {len(candidates)}')
    return candidates[0]


def service_port(service, agent):
    require(service.get('metadata', {}).get('name') == agent, f'{agent}: service identity mismatch')
    ports = service.get('spec', {}).get('ports') or []
    require(len(ports) == 1, f'{agent}: expected single service port')
    item = ports[0]
    require(item.get('port') == EXPECTED_PORT and item.get('targetPort') == EXPECTED_PORT,
            f'{agent}: Service port/targetPort not {EXPECTED_PORT}; do not use PORT-only repair')


def pod_readiness(pods, agent):
    """Return (bool, diagnosis, declared PORT value).

    Ignore completed/terminating pods; select ready running main containers.
    We do not read or print Kubernetes Secrets.
    """
    require(isinstance(pods, list), f'{agent}: pod inventory not a list')
    matches = [p for p in pods if p.get('metadata', {}).get('name', '').startswith(agent + '-')
               and not p.get('metadata', {}).get('deletionTimestamp')
               and p.get('status', {}).get('phase') not in ('Succeeded', 'Failed')]
    if not matches:
        return False, 'no running workload pod', None
    ready = []
    diag = []
    values = set()
    for p in matches:
        name = p['metadata']['name']
        containers = [c for c in p.get('spec', {}).get('containers', []) if c.get('name') == 'main']
        require(len(containers) == 1, f'{agent}: pod {name} has no unique main container')
        main = containers[0]
        probe = (main.get('startupProbe') or {}).get('tcpSocket') or {}
        require(probe.get('port') == EXPECTED_PORT,
                f'{agent}: startup probe in {name} does not target {EXPECTED_PORT}')
        port_env = [e for e in main.get('env', []) if e.get('name') == 'PORT']
        require(len(port_env) <= 1, f'{agent}: duplicate PORT declaration in {name}')
        literal = port_env[0].get('value') if port_env else None
        if literal is not None:
            values.add(literal)
        stat = [s for s in p.get('status', {}).get('containerStatuses', []) if s.get('name') == 'main']
        isready = (len(stat) == 1 and stat[0].get('ready') is True and
                   p.get('status', {}).get('phase') == 'Running' and
                   any(c.get('type') == 'Ready' and c.get('status') == 'True'
                       for c in p.get('status', {}).get('conditions', [])))
        diag.append(f'{name}: {"ready" if isready else "not-ready"}')
        if isready:
            ready.append(p)
    require(len(values) <= 1, f'{agent}: contradictory PORT values across pods: {sorted(values)}')
    value = next(iter(values)) if values else None
    if ready and value not in (None, str(EXPECTED_PORT)):
        return False, f'{agent}: Ready pod but PORT={value} differs from {EXPECTED_PORT}', value
    return bool(ready), '; '.join(diag), value


def select_build(payload, agent, sha, repo='https://github.com/joaokuntzwso2/demo-banking-agent', branch='main'):
    """Select already completed, source-matching build, never invent one.

    The eight-character commitId from real AMP responses is accepted only as
    a prefix of the known local 40-character Git commit. The caller must first
    verify the prefix against the Git object database to reject ambiguity.
    """
    require(isinstance(payload, dict), f'{agent}: build response not JSON object')
    records = payload.get('data', {}).get('builds')
    require(isinstance(records, list), f'{agent}: missing build list')
    require(len(sha) == 40 and all(c in '0123456789abcdef' for c in sha.lower()),
            'Expected full 40-digit Git commit SHA')
    candidates = []
    for b in records:
        require(isinstance(b, dict), f'{agent}: malformed build list item')
        bp = b.get('buildParameters') or {}
        commit = bp.get('commitId')
        if not commit:
            raise ContractError(f'{agent}: build lacks commitId; cannot safely choose build')
        require(isinstance(commit, str) and 8 <= len(commit) <= 40 and
                all(c in '0123456789abcdefABCDEF' for c in commit),
                f'{agent}: invalid commitId')
        if (b.get('agentName') == agent and b.get('projectName') == PROJECT and
            bp.get('repoUrl', '').rstrip('/').removesuffix('.git').lower() == repo.lower() and
            bp.get('branch') == branch and
            bp.get('appPath') == '/deployment/agent-manager/apps/' + agent):
            if b.get('status', '').lower() in ('queued', 'pending', 'running', 'building', 'in-progress'):
                raise ContractError(f'{agent}: managed build still in progress; no deployment requested')
            if sha.startswith(commit.lower()):
                require(b.get('status', '').lower() in ('completed', 'success', 'succeeded'),
                        f'{agent}: matching build is not completed successfully')
                require(isinstance(b.get('buildName'), str) and b['buildName'],
                        f'{agent}: matching build has no buildName')
                candidates.append(b)
    require(candidates, f'{agent}: no completed build for desired Git revision')
    candidates.sort(key=lambda b: b.get('startedAt', ''), reverse=True)
    require(not (len(candidates) > 1 and
                 candidates[0].get('startedAt') == candidates[1].get('startedAt')),
            f'{agent}: ambiguous equally recent builds; refuse selection')
    return candidates[0]
