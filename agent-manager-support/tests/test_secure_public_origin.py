import importlib.util
import json
import os
from pathlib import Path
import sys
import threading
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.error import HTTPError
from urllib.request import Request, urlopen
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
def importfile(name,path):
    spec=importlib.util.spec_from_file_location(name,path)
    mod=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod
publisher=importfile('aurora_publisher',ROOT/'publish_proxy.py')
bridge=importfile('aurora_bridge',ROOT/'secure_origin_bridge.py')

class Upstream(BaseHTTPRequestHandler):
    def log_message(self,*args): pass
    def do_POST(self):
        self.server.last_host=self.headers.get('Host')
        n=int(self.headers.get('Content-Length','0'))
        data=self.rfile.read(n)
        body=json.dumps({'body':data.decode(),'path':self.path}).encode()
        self.send_response(200)
        self.send_header('Content-Type','application/json')
        self.send_header('Content-Length',str(len(body)))
        self.end_headers();self.wfile.write(body)

class SecureOriginTests(unittest.TestCase):
    def test_public_payload_includes_explicit_origin_auth(self):
        tool=[{'name':'get_account_balance','inputSchema':{'type':'object','properties':{}}}]
        with patch.dict(os.environ,{'AURORA_MCP_ORIGIN_KEY':'a'*48}):
            result=publisher.build_proxy_payload('aurora-banking','https://demo.trycloudflare.com/mcp','3ca08496-7c42-4ef5-969f-eeb658bbef91',tool)
            ep=result['endpoints'][0]['upstream']['main']
            self.assertEqual(ep['auth'],{'type':'api-key','header':'X-Aurora-Origin-Key','value':'a'*48})
            self.assertEqual(ep['url'],'https://demo.trycloudflare.com/mcp')
    def test_insecure_authenticated_origin_rejected(self):
        tool=[{'name':'get_account_balance','inputSchema':{'type':'object'}}]
        with patch.dict(os.environ,{'AURORA_MCP_ORIGIN_KEY':'a'*48}):
            with self.assertRaises(RuntimeError):
                publisher.build_proxy_payload('aurora-banking','http://localhost/mcp','abc',tool)
    def test_relay_denies_missing_key_and_forwards_valid_mcp_only(self):
        upstream=ThreadingHTTPServer(('127.0.0.1',0),Upstream)
        t1=threading.Thread(target=upstream.serve_forever,daemon=True);t1.start()
        class Protected(bridge.Handler):
            origin_key='1234567890'*5
            port=upstream.server_address[1]
            internal_host='aurora-mcp.aurora-demo.svc.cluster.local'
        server=ThreadingHTTPServer(('127.0.0.1',0),Protected)
        t2=threading.Thread(target=server.serve_forever,daemon=True);t2.start()
        url='http://127.0.0.1:%s' % server.server_address[1]
        try:
            for path in ('/mcp','/mcp?foo=bar'):
                with self.assertRaises(HTTPError) as e:
                    urlopen(Request(url+path,method='POST',data=b'{}'),timeout=3)
                self.assertEqual(e.exception.code,401)
            with self.assertRaises(HTTPError) as e:
                urlopen(Request(url+'/admin',method='POST',data=b'{}',headers={'X-Aurora-Origin-Key':Protected.origin_key}),timeout=3)
            self.assertEqual(e.exception.code,404)
            req=Request(url+'/mcp',method='POST',data=b'{}',headers={'X-Aurora-Origin-Key':Protected.origin_key})
            with urlopen(req,timeout=3) as r:
                self.assertEqual(r.status,200)
                out=json.load(r)
            self.assertEqual(out['body'],'{}')
            self.assertEqual(out['path'],'/mcp')
            self.assertEqual(upstream.last_host,'aurora-mcp.aurora-demo.svc.cluster.local:8101')
        finally:
            server.shutdown();server.server_close()
            upstream.shutdown();upstream.server_close()

if __name__=='__main__':unittest.main()
