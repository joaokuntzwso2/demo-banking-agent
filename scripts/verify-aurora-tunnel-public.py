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


def _curl_probe_https(url, *, dns_over_https=False):
    """Probe without credentials or disabled TLS checks, honoring the host proxy.

    Curl matches the publisher's transport, including its Cloudflare DoH fallback.
    Only diagnostic Aurora headers are returned; intermediary headers are ignored.
    """
    import re
    import subprocess
    import tempfile

    with tempfile.TemporaryDirectory(prefix='aurora-https-probe-') as tmp:
        headers_file = pathlib.Path(tmp) / 'headers'
        command = [
            'curl', '--silent', '--show-error',
            '--output', '/dev/null', '--dump-header', str(headers_file),
            '--write-out', '%{http_code}',
            '--connect-timeout', '7', '--max-time', '12',
        ]
        if dns_over_https:
            command += ['--doh-url', 'https://1.1.1.1/dns-query']
        command.append(url)
        try:
            result = subprocess.run(
                command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, timeout=17, check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise TunnelError('Public HTTPS curl transport unavailable') from exc
        if result.returncode != 0:
            raise TunnelError(
                'Public HTTPS curl probe failed (exit {})'.format(result.returncode)
            )
        status = result.stdout.strip()
        if not re.fullmatch(r'[0-9]{3}', status):
            raise TunnelError('Public HTTPS curl did not return an HTTP status')
        try:
            raw = headers_file.read_text(errors='replace').replace('\r\n', '\n')
        except OSError as exc:
            raise TunnelError('Public HTTPS headers unavailable') from exc

        # Proxies may prepend an HTTP CONNECT response. Use the LAST HTTP block.
        blocks = [
            part for part in raw.split('\n\n')
            if part.lstrip().startswith('HTTP/')
        ]
        if not blocks:
            raise TunnelError('Public HTTPS response headers were missing')
        values = {}
        for line in blocks[-1].splitlines()[1:]:
            if ':' in line:
                key, value = line.split(':', 1)
                values[key.strip()] = value.strip()
        return int(status), marked_headers(values)


def stable_public_probe(url, expected_marker):
    """Keep a freshly created hostname while DNS/edge routing settles.

    Never accept arbitrary 401s: a matching local-origin marker is mandatory.
    An exhausted retry budget is an error (fail closed), not implicit success.
    """
    import time

    raw_wait = os.environ.get('AURORA_PUBLIC_VERIFY_WAIT_SECONDS', '300')
    try:
        wait_seconds = int(raw_wait)
    except ValueError as exc:
        raise TunnelError('AURORA_PUBLIC_VERIFY_WAIT_SECONDS must be 0..600') from exc
    if not 0 <= wait_seconds <= 600:
        raise TunnelError('AURORA_PUBLIC_VERIFY_WAIT_SECONDS must be 0..600')

    deadline = time.monotonic() + wait_seconds
    attempt = 0
    while True:
        attempt += 1
        for method in ('urllib', 'curl', 'doh'):
            try:
                if method == 'urllib':
                    status, marker = http_probe(url)
                else:
                    status, marker = _curl_probe_https(
                        url, dns_over_https=(method == 'doh')
                    )
                if status == 401 and marker == expected_marker:
                    if attempt > 1 or method != 'urllib':
                        print(
                            'MCP public origin verified (attempt {}, transport {}).'.format(
                                attempt, method
                            ), file=sys.stderr,
                        )
                    return status, marker
            except (TunnelError, OSError, TimeoutError, urllib.error.URLError):
                # A transient DNS, transport, or Cloudflare edge error; no
                # unverified response is ever treated as successful.
                pass

        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        if attempt == 1 or attempt % 6 == 0:
            print(
                'MCP public HTTPS not yet verified; preserving tunnel hostname '
                '(attempt {}, up to {}s total).'.format(attempt, wait_seconds),
                file=sys.stderr,
            )
        time.sleep(min(5, remaining))

    raise TunnelError(
        'Public HTTPS does not deliver the same authenticated origin 401 marker '
        '(after {}s stabilization; refusing unverified endpoint)'.format(wait_seconds)
    )



def check():
    if not STATE.is_file():
        raise TunnelError('No recorded HTTPS origin; cold-start tunnel reconciliation required')
    public = origin_url(STATE.read_text())
    local_status, local_marker = http_probe(LOCAL)
    if local_status != 401 or not local_marker:
        raise TunnelError('Private origin bridge did not return its authenticated 401 marker')
    public_status, public_marker = stable_public_probe(public, local_marker)
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
