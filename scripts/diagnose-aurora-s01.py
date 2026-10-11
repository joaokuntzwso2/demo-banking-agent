#!/usr/bin/env python3
"""Non-destructive S01 specialist diagnostics using existing Portal login and forwarding.

One chat POST may create pending synthetic approvals; never approves, executes or
resets simulator. Prints only allowlisted roles/actions/exception CLASS names.
"""
import importlib.util
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parent.parent
CHAT = ROOT / 'scripts' / 'diagnose-aurora-chat.py'
S01 = "I don't recognize the R$1,280 Orion Digital charge. Block my card and open a dispute."
ROLES = {'account', 'card', 'dispute', 'pix', 'wellness', 'risk'}
ACTIONS = {'block_card', 'open_dispute', 'execute_pix'}


def safe_symbol(value, allowed=None):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]{0,63}', value):
        return 'other'
    return value if allowed is None or value in allowed else 'other'


def classify(response):
    """Convert an untrusted chat payload to a privacy-safe structural report."""
    if not isinstance(response, dict):
        return {'valid': False, 'reason': 'non_object'}
    data = response.get('data') or {}
    if not isinstance(data, dict):
        return {'valid': False, 'reason': 'malformed_data'}
    roles = data.get('invoked_agents') or []
    specialists = data.get('specialists') or []
    actions = response.get('next_actions') or []
    if not all(isinstance(x, list) for x in (roles, specialists, actions)):
        return {'valid': False, 'reason': 'malformed_lists'}
    report = {
        'valid': True,
        'invoked': [safe_symbol(x, ROLES) for x in roles][:8],
        'specialists': [],
        'actions': [safe_symbol(x.get('action'), ACTIONS) for x in actions[:12] if isinstance(x, dict)],
        'clarification': data.get('awaiting_clarification') is True,
    }
    for item in specialists[:8]:
        if not isinstance(item, dict):
            report['specialists'].append(('other', 'malformed', []))
            continue
        info = item.get('data') or {}
        info = info if isinstance(info, dict) else {}
        kind = safe_symbol(info.get('error_type')) if info.get('error_type') else 'none'
        own_actions = item.get('next_actions') or []
        if not isinstance(own_actions, list):
            own_actions = []
        report['specialists'].append((
            safe_symbol(item.get('agent'), ROLES),
            kind,
            [safe_symbol(x.get('action'), ACTIONS) for x in own_actions[:12] if isinstance(x, dict)]))
    return report


def render(report):
    if not report['valid']:
        print('S01 response invalid; classification=' + report['reason'])
        return
    print('S01 invoked specialists: ' + ', '.join(report['invoked']))
    for role, err, actions in report['specialists']:
        print('  specialist=' + role + ' error_type=' + err + ' pending=' + ','.join(actions))
    print('S01 aggregate pending action types: ' + ','.join(report['actions']))
    print('S01 awaiting clarification: ' + str(report['clarification']).lower())
    expected = {'card', 'dispute'}
    seen = set(report['invoked'])
    if not expected.issubset(seen):
        print('DIAGNOSIS: Card/Dispute not selected. Inspect Concierge planning, source version or intent parsing.')
    elif any(role in expected and err != 'none' for role, err, _ in report['specialists']):
        print('DIAGNOSIS: Card/Dispute selected but specialist call failed. Check published gateway URL routing, policy, or MCP.')
    elif not {'block_card', 'open_dispute'}.issubset(report['actions']):
        print('DIAGNOSIS: Card/Dispute selected but approvals absent. Inspect returned card/transaction matching and clarification.')
    else:
        print('DIAGNOSIS: Both S01 approval types present. Do not authorize as part of this diagnostic.')


def load_chat():
    if not CHAT.is_file():
        raise RuntimeError('Missing existing scripts/diagnose-aurora-chat.py; install chat diagnostic v1 first')
    spec = importlib.util.spec_from_file_location('aurora_chat_diagnostic_module', CHAT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main():
    d = load_chat()
    private = d.private_values(d.SUPPORT / 'secrets.env')
    code = private.get('DEMO_ACCESS_CODE') or d.os.getenv('DEMO_ACCESS_CODE')
    if not code:
        raise RuntimeError('Missing private Portal access code')
    proc, log, port = d.start_port_forward()
    try:
        import http.cookiejar
        import urllib.request
        origin = 'http://127.0.0.1:' + str(port)
        opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
        status, login, detail = d.request(opener, 'POST', origin+'/api/login', private, payload={'access_code': code})
        if status != 200 or not isinstance(login, dict) or not login.get('csrf_token'):
            print('STOP: Portal login unavailable, HTTP=' + str(status))
            return 2
        status, state, detail = d.request(opener, 'GET', origin+'/api/state', private)
        if status != 200 or not isinstance(state, dict):
            print('STOP: Portal banking state unavailable, HTTP=' + str(status))
            return 2
        cards = state.get('cards') or []
        disputes = state.get('disputes') or []
        print('Synthetic baseline: card_active=' + str(any(x.get('status') == 'active' for x in cards if isinstance(x, dict))).lower()
              + '; dispute_count=' + str(len(disputes) if isinstance(disputes, list) else '?'))
        print('Sending S01 request without approving or resetting simulator.')
        status, response, detail = d.request(opener, 'POST', origin+'/api/chat', private,
            payload={'message': S01, 'language': 'en'},
            headers={'X-CSRF-Token': login['csrf_token']}, timeout=100)
        print('Portal S01 HTTP=' + str(status))
        if status != 200:
            print('STOP: Chat did not succeed; sanitized detail=' + (detail or 'none'))
            return 1
        report = classify(response)
        render(report)
        return 0 if report['valid'] else 1
    finally:
        d.terminate_forward(proc, log)


if __name__ == '__main__':
    try:
        sys.exit(main())
    except Exception as exc:
        print('DIAGNOSTIC FAILED: '+type(exc).__name__+' (no raw exception message printed)', file=sys.stderr)
        sys.exit(2)
