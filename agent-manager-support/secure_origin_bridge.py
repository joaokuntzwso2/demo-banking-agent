#!/usr/bin/env python3
"""Loopback-only authenticated streaming MCP reverse bridge for local AMP demo.

Exposes only /mcp to locally running cloudflared; requires an independent
X-Aurora-Origin-Key supplied by WSO2 MCP Proxy upstream authentication.
Do not expose this listener directly to the internet or bind 0.0.0.0.
"""
import argparse
import hashlib
import hmac
import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import sys
from urllib.parse import urlsplit

HOP = {'connection','proxy-connection','keep-alive','proxy-authenticate','proxy-authorization',
       'te','trailer','transfer-encoding','upgrade','host','content-length',
       'x-aurora-origin-key','x-forwarded-for','x-forwarded-host','x-forwarded-proto','forwarded'}


def safe_path(path):
    parsed = urlsplit(path)
    return not (parsed.scheme or parsed.netloc or parsed.fragment) and parsed.path == '/mcp'


class Handler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'
    origin_key = ''
    port = 18101
    internal_host = 'aurora-mcp.aurora-demo.svc.cluster.local'

    def log_message(self, fmt, *args):
        # Prevent logging bearer tokens or request bodies.
        if args and str(args[1] if len(args) > 1 else '').startswith('5'):
            super().log_message('upstream gateway error %s', args[1])

    def error_response(self, code, content):
        body = (content+'\n').encode()
        self.send_response(code)
        self.send_header('Content-Type','text/plain')
        self.send_header('Content-Length',str(len(body)))
        self.send_header('Cache-Control','no-store')
        if code == 401:
            # Marker proves an unauthenticated request reached this exact bridge.
            # A 401 from Cloudflare Access or another proxy is not sufficient.
            self.send_header('X-Aurora-Origin-Auth', 'required')
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self): self.proxy()
    def do_POST(self): self.proxy()
    def do_DELETE(self): self.proxy()
    def do_OPTIONS(self): self.error_response(405,'Method not allowed')
    def do_PUT(self): self.error_response(405,'Method not allowed')

    def proxy(self):
        supplied=self.headers.get('X-Aurora-Origin-Key','')
        # Compare digests to avoid variable length input timing behaviour.
        if not hmac.compare_digest(hashlib.sha256(supplied.encode()).digest(),
                                   hashlib.sha256(self.origin_key.encode()).digest()):
            self.error_response(401,'Unauthorized')
            return
        if not safe_path(self.path):
            self.error_response(404,'Not found')
            return
        if self.headers.get('Transfer-Encoding'):
            self.error_response(411,'Transfer-encoding request bodies not supported')
            return
        try:
            n=int(self.headers.get('Content-Length','0'))
            if n<0 or n>1024*1024:
                self.error_response(413,'Request too large');return
        except ValueError:
            self.error_response(400,'Invalid Content-Length');return
        body=self.rfile.read(n) if n else None
        conn=http.client.HTTPConnection('127.0.0.1',self.port,timeout=180)
        try:
            headers={k:v for k,v in self.headers.items() if k.lower() not in HOP}
            headers['Host']=f'{self.internal_host}:8101'
            conn.request(self.command, self.path, body=body, headers=headers)
            response=conn.getresponse()
            self.send_response(response.status)
            for k,v in response.getheaders():
                if k.lower() not in HOP:
                    self.send_header(k,v)
            self.send_header('Transfer-Encoding','chunked')
            self.end_headers()
            while True:
                chunk=response.read1(8192)
                if not chunk:break
                self.wfile.write(f'{len(chunk):X}\r\n'.encode())
                self.wfile.write(chunk)
                self.wfile.write(b'\r\n')
                self.wfile.flush()
            self.wfile.write(b'0\r\n\r\n')
            self.wfile.flush()
        except (OSError,http.client.HTTPException,BrokenPipeError) as exc:
            # If response headers were already sent, end the connection, never
            # fabricate a success response after an upstream failure.
            if not getattr(self,'_headers_buffer',None) and not self.wfile.closed:
                self.close_connection=True
            else:
                try:self.error_response(502,'Upstream unavailable')
                except (OSError,BrokenPipeError):pass
        finally:
            conn.close()


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--port',type=int,default=18102)
    parser.add_argument('--upstream-port',type=int,default=18101)
    parser.add_argument('--host',required=True)
    parser.add_argument('--key-file',required=True)
    args=parser.parse_args()
    if args.port == args.upstream_port: parser.error('Upstream and listener ports must differ')
    Handler.origin_key=open(args.key_file).read().strip()
    if len(Handler.origin_key)<32:parser.error('Weak or missing origin key')
    Handler.port=args.upstream_port
    Handler.internal_host=args.host
    srv=ThreadingHTTPServer(('127.0.0.1',args.port),Handler)
    print(f'Aurora authenticated MCP bridge on localhost:{args.port} (secret not logged)',flush=True)
    try:srv.serve_forever()
    finally:srv.server_close()

if __name__=='__main__':main()
