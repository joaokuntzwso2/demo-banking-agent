import ast
import importlib.util
from pathlib import Path
import io
import contextlib
import unittest

MODULE = Path(__file__).resolve().parent.parent/'scripts'/'diagnose-aurora-s01.py'
spec = importlib.util.spec_from_file_location('aurora_s01', MODULE)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

class S01DiagnosticsTests(unittest.TestCase):
    def test_python311_syntax(self):
        ast.parse(MODULE.read_text(), feature_version=(3, 11))
    def test_detects_unavailable(self):
        p={'data':{'invoked_agents':['card','dispute'], 'specialists':[
            {'agent':'card','data':{'error_type':'ConnectError'},'next_actions':[]},
            {'agent':'dispute','data':{'error_type':'HTTPStatusError'},'next_actions':[]}]},'next_actions':[]}
        r=m.classify(p)
        out=io.StringIO()
        with contextlib.redirect_stdout(out):m.render(r)
        self.assertIn('specialist call failed',out.getvalue())
        self.assertIn('ConnectError',out.getvalue())
    def test_approvals(self):
        p={'data':{'invoked_agents':['card','dispute'],'specialists':[]},'next_actions':[{'action':'block_card'}, {'action':'open_dispute'}]}
        r=m.classify(p)
        self.assertEqual(r['actions'], ['block_card','open_dispute'])
    def test_routing_missing(self):
        out=io.StringIO()
        with contextlib.redirect_stdout(out): m.render(m.classify({'data':{'invoked_agents':['account']}}))
        self.assertIn('not selected',out.getvalue())
    def test_sanitizes_values(self):
        p={'data':{'invoked_agents':['Bearer secret','card'],'specialists':[{'agent':'card','data':{'error_type':'http 500 Authorization secret'}}]},'next_actions':[{'action':'SECRET_API_KEY=value'}]}
        s=repr(m.classify(p)); self.assertNotIn('secret',s);self.assertIn('other',s)
    def test_rejects_malformed(self):
        self.assertFalse(m.classify({'data':{'specialists':'wrong'}})['valid'])
    def test_never_calls_mutating_core_api(self):
        src=MODULE.read_text()
        self.assertNotIn("'/api/reset'",src)
        self.assertNotIn("'/api/approvals/",src)
        self.assertNotIn('kubectl apply',src)
    def test_dependency_is_existing_diagnostic(self):
        self.assertIn('diagnose-aurora-chat.py', MODULE.read_text())

if __name__=='__main__':unittest.main()
