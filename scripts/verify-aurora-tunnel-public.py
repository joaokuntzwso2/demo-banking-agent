#!/usr/bin/env python3
"""Verify live authenticated Aurora MCP bridge THROUGH the current HTTPS Quick Tunnel.

Never equate process liveness or an arbitrary public HTTP 401 to bridge health.
No credentials sent; only the dedicated origin bridge's unauthenticated marker
is matched between loopback and Cloudflare. Safe for repeated preflight checks.
"""
import argparse
import pathlib
import os
import sys
import urllib.request
import urllib.error
from urllib.parse import urlsplit

ROOT = pathlib.Path(__file__).resolve().parent.parent
STATE = ROOT / 'agent-manager-support' / '.mcp-public-tunnel' / 'url'
LOCAL = 'http://127.0.0.1:18102/mcp'

class TunnelError(RuntimeError): pass


def origin_url(value):
    u = urlsplit(value.strip())
    allowed = u.hostname and (u.hostname.endswith('.trycloudflare.com') or
               u.hostname == os.environ.get('AURORA_NAMED_MCP_HOST', ''))
    if not (u.scheme == 'https' and allowed and not u.username and not u.password and
            not u.query and not u.fragment and u.port in (None, 443) and
            u.path.rstrip('/') in ('', '/mcp')):
        raise TunnelError('Unsafe or unrecognized stored tunnel origin; no request attempted')
    return 'https://' + u.hostname + '/mcp'


def marked_headers(headers):
    # Marker belongs to the *authenticated local origin bridge*, not an arbitrary
    # WAF/proxy 401. Compare only named diagnostic headers, never auth headers.
    return {k.lower(): v for k, v in headers.items()
            if k.lower().startswith('x-') and
            ('aurora' in k.lower() or 'origin-bridge' in k.lower())}


def http_probe(url):
    req = urllib.request.Request(url, headers={'Accept':'application/json',
                                                'Cache-Control':'no-store',
                                                'User-Agent':'AuroraTunnelHealth/1.0'}, method='GET')
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({})) if url.startswith('http://127.0.0.1:') else urllib.request.build_opener()
    try:
        with opener.open(req, timeout=12) as response:
            return response.status, marked_headers(response.headers)
    except urllib.error.HTTPError as response:
        try:
            return response.code, marked_headers(response.headers)
        finally:
            response.close()
    except (OSError, urllib.error.URLError, TimeoutError):
        return 0, {}


def check():
    if not STATE.is_file():
        raise TunnelError('No recorded HTTPS origin; cold-start tunnel reconciliation required')
    public = origin_url(STATE.read_text())
    local_status, local_marker = http_probe(LOCAL)
    if local_status != 401 or not local_marker:
        raise TunnelError('Private origin bridge did not return its authenticated 401 marker')
    public_status, public_marker = http_probe(public)
    if public_status != 401 or public_marker != local_marker:
        raise TunnelError('Public HTTPS does not deliver the same authenticated origin 401 marker')
    print('MCP_TUNNEL_PUBLIC=verified; HTTPS reaches the authenticated Aurora origin bridge.')


def main():
    try:
        check()
    except TunnelError as exc:
        print('MCP_TUNNEL_PUBLIC=not_verified: '+str(exc),file=sys.stderr)
        return 1
    return 0

if __name__=='__main__':
    sys.exit(main())
