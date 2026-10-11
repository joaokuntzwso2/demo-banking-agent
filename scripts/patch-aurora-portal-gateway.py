#!/usr/bin/env python3
"""One-time, idempotent source patch for Banco Aurora's private Portal BFF.

Never inject OpenAI provider credentials into browser code. This patch adds an
in-cluster route for AMP's *published* .localhost gateway URL; the original
Host header is preserved, and the BFF continues sending its existing X-API-Key.
"""
import argparse
import ast
import pathlib
import re
import sys

ROOT=pathlib.Path(__file__).resolve().parent.parent
FILE=ROOT/'demo_portal'/'app.py'
MARKER='def concierge_transport(): '
TARGET='''def concierge_transport():
    """Use AMP's Kubernetes gateway with the published virtual host.

    Applies only to the known Banco Aurora local k3d AMP environment. The
    browser never sees these addresses or any upstream API keys.
    """
    from urllib.parse import urlsplit
    url = CONCIERGE.rstrip('/')
    parsed = urlsplit(url)
    if not (os.getenv('WSO2_MODE', '').lower() == 'true'
            and os.getenv('KUBERNETES_SERVICE_HOST')):
        return url, {}
    if parsed.hostname == 'default-default.am-gateway.localhost':
        if (parsed.scheme != 'http' or parsed.port != 19080
                or parsed.path.rstrip('/') != '/aurora-concierge'
                or parsed.username or parsed.password or parsed.query or parsed.fragment):
            raise RuntimeError('Unrecognized local AMP Concierge gateway endpoint')
        return ('http://gateway-default.openchoreo-data-plane.svc.cluster.local:19080'
                + parsed.path.rstrip('/'), {'Host': parsed.netloc})
    return url, {}

'''
OLD_RE=re.compile(r"(?m)^    async with httpx\.AsyncClient\(timeout=90\) as client:\n"
                  r"        res=await client\.request\(method,CONCIERGE\.rstrip\('/'\)\+path,json=payload,\n"
                  r"[ \t]+headers=gateway_headers\(approval\)\)")
NEW='''    base_url, extra_headers = concierge_transport()
    async with httpx.AsyncClient(timeout=90) as client:
        res=await client.request(method,base_url+path,json=payload,
                                 headers={**gateway_headers(approval), **extra_headers})'''

def transform(src):
    if 'def concierge_transport():' in src:
        if TARGET.strip() not in src or NEW not in src:
            raise ValueError('Portal BFF has an unknown gateway-transport implementation; refusing overwrite')
        return src, False
    if len(OLD_RE.findall(src))!=1 or src.count('async def agent(method,path,payload=None,approval=False):')!=1:
        raise ValueError('Portal BFF differs from reviewed implementation; no source changes made')
    result=src.replace('async def agent(method,path,payload=None,approval=False):',
                       TARGET+'async def agent(method,path,payload=None,approval=False):',1)
    result=OLD_RE.sub(NEW,result,count=1)
    ast.parse(result)
    return result,True


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--apply',action='store_true',help='Apply reviewed BFF-only source patch')
    args=p.parse_args()
    try:
        src=FILE.read_text()
        after, changed=transform(src)
        if changed and args.apply:
            # Never rewrite the file unless the reviewed anchors match.
            FILE.write_text(after)
            print('Applied in-cluster Concierge gateway transport to demo_portal/app.py.')
        elif changed:
            print('Portal gateway transport patch available (read-only). Run with --apply.')
        else:
            print('Portal gateway transport already installed; no changes.')
    except (OSError, ValueError, SyntaxError) as exc:
        print(f'Portal patch refused: {exc}', file=sys.stderr)
        return 1
    return 0

if __name__=='__main__':
    sys.exit(main())
