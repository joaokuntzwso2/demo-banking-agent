"""End-to-end Git safety tests against a temporary bare remote (never GitHub)."""
from pathlib import Path
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT=Path(__file__).resolve().parent.parent
SCRIPT=ROOT/'scripts/sync-managed-agent-source.py'
GENERATOR='''from pathlib import Path
root=Path(__file__).resolve().parents[3]
apps=root/'deployment/agent-manager/apps'
for role in ('account','card','dispute','pix','wellness','concierge'):
    a=apps/('aurora-'+role); a.mkdir(parents=True,exist_ok=True)
    (a/'main.py').write_text((root/'agents'/'main.py').read_text())
    (a/'requirements.txt').write_text((root/'requirements.txt').read_text())
    import json
    paths={'/health':{},'/chat':{}}
    if role=='concierge':paths.update({'/approve':{},'/workflow/{workflow_id}':{}})
    else:paths['/execute']={}
    (a/'openapi.json').write_text(json.dumps({'openapi':'3.0.3','info':{'title':'Sample','version':'1'},'paths':paths}))
'''


def call(args, cwd, check=True, env=None):
    p=subprocess.run(args,cwd=cwd,capture_output=True,text=True,env=env)
    if check and p.returncode:
        raise AssertionError(f'{args} => {p.returncode}: {p.stderr} {p.stdout}')
    return p


class SourceTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        p=Path(self.tmp.name)
        self.bare=p/'remote.git';self.repo=p/'checkout'
        call(['git','init','--bare',str(self.bare)],p)
        call(['git','clone',str(self.bare),str(self.repo)],p)
        call(['git','checkout','-b','main'],self.repo)
        call(['git','config','user.name','Local test'],self.repo)
        call(['git','config','user.email','local@example.invalid'],self.repo)
        for directory in ('agents','banking_common','knowledge','mcp_server','scripts','deployment/agent-manager/scripts'):
            (self.repo/directory).mkdir(parents=True,exist_ok=True)
        (self.repo/'agents/main.py').write_text('print("v1")\n')
        (self.repo/'banking_common/core.py').write_text('# synthetic\n')
        (self.repo/'knowledge/policies.json').write_text('{}\n')
        (self.repo/'mcp_server/app.py').write_text('app=None\n')
        (self.repo/'requirements.txt').write_text('fastapi\n')
        (self.repo/'deployment/agent-manager/scripts/prepare-apps.py').write_text(GENERATOR)
        shutil.copy2(SCRIPT,self.repo/'scripts/sync-managed-agent-source.py')
        (self.repo/'agent-manager-support').mkdir(exist_ok=True)
        (self.repo/'.gitignore').write_text('agent-manager-support/.source-sync-state.json\nagent-manager-support/.source-sync-backups/\n')
        call(['python3','deployment/agent-manager/scripts/prepare-apps.py'],self.repo)
        call(['git','add','-A'],self.repo)
        call(['git','commit','-m','initial'],self.repo)
        call(['git','push','-u','origin','main'],self.repo)
        self.env=dict(os.environ,AURORA_GIT_REPO=str(self.bare),AURORA_GIT_REF='main')

    def tearDown(self):
        self.tmp.cleanup()

    def run_sync(self, opt=False, action='ensure'):
        env=dict(self.env)
        if opt:env['AURORA_AGENT_SOURCE_PUBLISH']='1'
        return call([sys.executable,'scripts/sync-managed-agent-source.py',action],self.repo,False,env)

    def test_clean_reuse_ignores_unrelated_untracked(self):
        (self.repo/'notes.local').write_text('not for deployment')
        p=self.run_sync()
        self.assertEqual(p.returncode,0,p.stderr)
        self.assertIn('match canonical source',p.stdout)

    def test_dirty_source_requires_explicit_push_optin_and_does_not_overwrite(self):
        (self.repo/'agents/main.py').write_text('print("v2")\n')
        original=(self.repo/'deployment/agent-manager/apps/aurora-account/main.py').read_text()
        p=self.run_sync()
        self.assertNotEqual(p.returncode,0)
        self.assertIn('AURORA_AGENT_SOURCE_PUBLISH=1',p.stderr)
        self.assertEqual((self.repo/'deployment/agent-manager/apps/aurora-account/main.py').read_text(),original)
        self.assertFalse(call(['git','diff','--cached','--name-only'],self.repo).stdout.strip())

    def test_optin_syncs_generated_sources_only_and_is_repeatable(self):
        (self.repo/'agents/main.py').write_text('print("v2")\n')
        (self.repo/'notes.local').write_text('keep me untracked')
        p=self.run_sync(opt=True)
        self.assertEqual(p.returncode,0,p.stderr+'\n'+p.stdout)
        self.assertEqual(call(['git','rev-parse','HEAD'],self.repo).stdout.strip(),
            call(['git','ls-remote','origin','refs/heads/main'],self.repo).stdout.split()[0])
        self.assertIn('v2',(self.repo/'deployment/agent-manager/apps/aurora-pix/main.py').read_text())
        self.assertTrue((self.repo/'notes.local').exists())
        before=call(['git','rev-parse','HEAD'],self.repo).stdout.strip()
        p=self.run_sync()
        self.assertEqual(p.returncode,0,p.stderr)
        self.assertEqual(before,call(['git','rev-parse','HEAD'],self.repo).stdout.strip())

    def test_real_prepare_apps_dependency_tree_is_complete_and_check_is_read_only(self):
        """Production generator also creates upstream-MCP and needs mcp_server.

        This guards against the exact /tmp/.../mcp_server FileNotFoundError.
        """
        real_generator=ROOT/'deployment/agent-manager/scripts/prepare-apps.py'
        self.assertTrue(real_generator.exists(), 'Production prepare-apps.py missing')
        shutil.copy2(real_generator,self.repo/'deployment/agent-manager/scripts/prepare-apps.py')
        call([sys.executable,'deployment/agent-manager/scripts/prepare-apps.py'],self.repo)
        call(['git','add','-A'],self.repo)
        call(['git','commit','-m','prepare real agent folders'],self.repo)
        call(['git','push','origin','main'],self.repo)
        initial_sha=call(['git','rev-parse','HEAD'],self.repo).stdout.strip()
        p=self.run_sync(action='check')
        self.assertEqual(p.returncode,0,p.stderr+'\n'+p.stdout)
        self.assertIn('match canonical source',p.stdout)
        self.assertEqual(initial_sha,call(['git','rev-parse','HEAD'],self.repo).stdout.strip())
        self.assertFalse(call(['git','status','--porcelain'],self.repo).stdout.strip())

    def test_generator_missing_openapi_fails_before_any_push(self):
        generator=self.repo/'deployment/agent-manager/scripts/prepare-apps.py'
        source=generator.read_text()
        generator.write_text(source.replace("    (a/'openapi.json').write_text(json.dumps({'openapi':'3.0.3','info':{'title':'Sample','version':'1'},'paths':paths}))", "    # intentionally missing spec"))
        p=self.run_sync(opt=True)
        self.assertNotEqual(p.returncode,0)
        self.assertIn('missing openapi.json',p.stderr)
        self.assertEqual(call(['git','rev-parse','HEAD'],self.repo).stdout.strip(),
            call(['git','ls-remote','origin','refs/heads/main'],self.repo).stdout.split()[0])

    def test_generator_changes_are_included_in_approved_commit(self):
        generator=self.repo/'deployment/agent-manager/scripts/prepare-apps.py'
        generator.write_text(generator.read_text()+"\n# documentation-only generator change\n")
        p=self.run_sync(opt=True)
        self.assertEqual(p.returncode,0,p.stderr+'\n'+p.stdout)
        changed=call(['git','show','--pretty=format:','--name-only','HEAD'],self.repo).stdout
        self.assertIn('deployment/agent-manager/scripts/prepare-apps.py',changed)
        self.assertEqual(call(['git','rev-parse','HEAD'],self.repo).stdout.strip(),
            call(['git','ls-remote','origin','refs/heads/main'],self.repo).stdout.split()[0])

    def test_generated_secret_signature_aborts_without_pushing(self):
        (self.repo/'agents/main.py').write_text('LEAK="ghp_'+('A'*30)+'"\n')
        p=self.run_sync(opt=True)
        self.assertNotEqual(p.returncode,0)
        self.assertIn('possible credential',p.stderr.lower())
        self.assertFalse(call(['git','diff','--cached','--name-only'],self.repo).stdout.strip())
        self.assertEqual(call(['git','rev-parse','HEAD'],self.repo).stdout.strip(),
            call(['git','ls-remote','origin','refs/heads/main'],self.repo).stdout.split()[0])

    def test_no_auto_push_with_existing_staged_changes(self):
        (self.repo/'agents/main.py').write_text('print("v2")\n')
        call(['git','add','agents/main.py'],self.repo)
        p=self.run_sync(opt=True)
        self.assertNotEqual(p.returncode,0)
        self.assertIn('already has staged changes',p.stderr)


if __name__=='__main__':unittest.main()
