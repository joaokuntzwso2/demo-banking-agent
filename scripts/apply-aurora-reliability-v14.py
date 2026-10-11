#!/usr/bin/env python3
"""Guarded source-only Banco Aurora startup MCP self-heal installer.

Runs a read-only plan by default; --apply updates the existing startup script,
adds the monitor and tests, and appends one section to the existing root README.
Does NOT invoke Kubernetes, Git, Docker, Agent Manager or Cloudflare.
"""
import argparse
import datetime as dt
from pathlib import Path
import re
import shutil
import sys

BUNDLE = Path(__file__).resolve().parents[1]
MARK = '# AURORA_V14_MCP_FORWARD_GUARD'


def transform_start(original):
    if original.count(MARK) == 3:
        return original
    if MARK in original:
        raise RuntimeError('Partial v14 patch present; refusing automatic modification')
    # Inject preflight in check before runtime scenario checks. Preserve existing
    # v11 secure public probe and any later validated reconciler behavior.
    lines = original.splitlines(keepends=True)
    check_start = next((i for i, line in enumerate(lines)
                        if line.strip() == 'if [[ "$ACTION" == check ]]; then'), None)
    if check_start is None:
        raise RuntimeError('Cannot identify startup check branch')
    check_end = next((j for j in range(check_start + 1, len(lines))
                      if lines[j].strip() == 'fi'), None)
    if check_end is None or check_end - check_start > 22:
        raise RuntimeError('Unfamiliar startup check branch')
    body = ''.join(lines[check_start:check_end])
    if 'scripts/aurora-full-reconcile.py check' not in body:
        raise RuntimeError('Check branch does not invoke audited reconciler')
    insert_at = next(j for j in range(check_start + 1, check_end)
                     if 'python3 scripts/aurora-full-reconcile.py check' in lines[j])
    lines.insert(insert_at, f'  {MARK}\n'
                           '  python3 scripts/aurora-mcp-forward-guard-v14.py ensure --gateway-check \\\n'
                           "    || die 'MCP forward/gateway proof failed; no S01-S06 writes attempted'\n")
    text = ''.join(lines)
    # Guard up/agents after managed deployments and before stateful acceptance.
    gate = "log 'Stage 6/6: Check role assignments, endpoints, and portal integration.'"
    if text.count(gate) != 1:
        raise RuntimeError('Cannot identify stage-6 entry point')
    text = text.replace(gate,
        f'{MARK}\n'
        'python3 scripts/aurora-mcp-forward-guard-v14.py ensure --gateway-check \\\n'
        "  || die 'Native gateway not healthy; refusing S01-S06 stateful checks'\n"
        + gate, 1)
    # Start the continuous supervisor immediately after the secure tunnel up
    # and before MCP Proxy/control-plane validation. Do not rotate the tunnel.
    stage4 = "log 'Stage 4/6: Verify identity-secured MCP registration and tool scopes.'"
    if text.count(stage4) != 1:
        raise RuntimeError('Cannot identify stage-4 entry point')
    text = text.replace(stage4,
        f'{MARK}\n'
        '  python3 scripts/aurora-mcp-forward-guard-v14.py ensure \\\n'
        "    || die 'Authenticated MCP bridge lost its Kubernetes forward'\n"
        + '  ' + stage4, 1)
    if text.count(MARK) != 3:
        raise RuntimeError('Internal installer assertion: exactly three guards')
    return text


README_NOTE = '''

### v14 — self-healing MCP forwarding on the startup path

`./scripts/start-everything.sh up` now confirms a **real MCP `initialize`**
through the private Kubernetes Service forward at `127.0.0.1:18101` and starts
an owned background monitor. If the selected `aurora-mcp` pod is replaced and
its `kubectl port-forward` exits, the monitor reconnects the local-only
forward to the current Service backend. It does **not** rotate Cloudflare
URLs, create proxies, change MCP scopes, rotate credentials, reset data, or
rebuild agents. `up`, `agents` and `check` require the real Account AgentID
positive tool read and a **gateway HTTP 403** for forbidden tool access
*before* the stateful S01–S06 suite is allowed to run. A failed verification
fails closed and must never be counted as a passing demo.

Inspect the supervisor without printing secrets:

```bash
python3 scripts/aurora-mcp-forward-guard-v14.py status
```

`stop-monitor` stops only the owned supervisor, never the tunnel or database.
The existing `publish-mcp-secure-tunnel.sh stop` remains the supported tunnel
shutdown path. This monitor cannot heal a Cloudflare provider outage or a
misconfigured external WSO2 gateway; in either case the mandatory positive/403
proof blocks scenario execution and reports failure.

**Shared-data limitation:** The banking Portal and S01–S06 acceptance operate on
one synthetic customer in a shared Core database. The automatic gateway check
prevents MCP false readiness, but it **cannot prevent a presenter from placing
an additional Pix payment during the acceptance test**. Run `check` only when
no human is submitting UI mutations, or provide a separate test Core/database
and agent wiring. Do not suppress balance assertion failures or reset the
shared database unexpectedly to force a pass.
'''


def run(repo, apply):
    start = repo / 'scripts' / 'start-everything.sh'
    readme = repo / 'README.md'
    if not start.is_file() or not readme.is_file() or not (repo/'scripts'/'verify-aurora-agentid-denial.py').is_file():
        raise RuntimeError('Expected Banco Aurora startup, README and native authz verifier missing')
    before = start.read_text()
    after = transform_start(before)
    old_readme = readme.read_text()
    new_readme = old_readme if '### v14 — self-healing MCP forwarding' in old_readme else old_readme.rstrip() + '\n' + README_NOTE
    source_guard = BUNDLE/'scripts'/'aurora-mcp-forward-guard-v14.py'
    source_tests = BUNDLE/'tests'/'test_aurora_mcp_forward_v14.py'
    for src in (source_guard, source_tests):
        if not src.is_file():
            raise RuntimeError(f'Bundle incomplete: {src.name}')
    targets = [
        (start, after), (readme, new_readme),
        (repo/'scripts'/'aurora-mcp-forward-guard-v14.py', source_guard.read_text()),
        (repo/'tests'/'test_aurora_mcp_forward_v14.py', source_tests.read_text())]
    for path, content in targets:
        mode = 'UNCHANGED' if path.is_file() and path.read_text() == content else ('MODIFY' if path.exists() else 'CREATE')
        print(f'{path.relative_to(repo)}: {mode}')
    if not apply:
        print('READ-ONLY PLAN. No Git/Kubernetes/agent/tunnel/data mutation.')
        return
    backup = Path.home() / 'Downloads' / ('aurora-v14-source-backup-' + dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%SZ'))
    backup.mkdir(parents=True, mode=0o700)
    for path, content in targets:
        if path.is_file() and path.read_text() == content:
            continue
        if path.exists():
            shutil.copy2(path, backup / path.name)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    print(f'Source installed; backup copies in {backup}')
    print('No runtime changes. Run focused + full tests; then startup up/check.')


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--repo', type=Path, required=True)
    p.add_argument('--apply', action='store_true')
    a = p.parse_args()
    try:
        run(a.repo.resolve(), a.apply)
    except (RuntimeError, OSError) as exc:
        print(f'INSTALL REFUSED: {exc}', file=sys.stderr)
        sys.exit(1)
