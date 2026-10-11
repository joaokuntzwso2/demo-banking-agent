"""Captured AMP 1.0.0 buildParameters.commitId behavior (read-only regression)."""
import importlib.util
import subprocess
import pathlib
import unittest
from unittest import mock

ROOT=pathlib.Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('build_contract_v3',ROOT/'scripts/aurora-full-reconcile.py')
m=importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
SHA='634794269c3f'+'a'*28

def captured(agent='aurora-card',status='Completed',commit='63479426'):
    return {
        'agentName':agent,
        'buildId':'4b94b5aa-523f-49ab-b8dc-e798e559f4e8',
        'buildName':f'{agent}-1791651682505',
        'buildParameters':{
            'appPath':f'/deployment/agent-manager/apps/{agent}',
            'branch':'main',
            'commitId':commit,
            'language':'python',
            'languageVersion':'3.12',
            'repoUrl':'https://github.com/joaokuntzwso2/demo-banking-agent',
            'runCommand':'python main.py'
        },
        'imageId':f'host.k3d.internal:10082/{agent}-1791651682505-image:v1-{commit}',
        'projectName':'banco-aurora',
        'startedAt':'2026-10-10T17:01:22Z',
        'status':status,
    }

class CapturedBuildContractTests(unittest.TestCase):
    def git(self,sha=SHA,returncode=0):
        return mock.patch.object(m,'cmd', return_value=subprocess.CompletedProcess([],returncode,sha+'\n',''))

    def test_real_short_commit_becomes_verified_full_sha(self):
        with self.git() as cmd:
            self.assertEqual(m.verified_build_revision(captured(),'aurora-card',SHA),SHA)
            cmd.assert_called_once_with(['git','rev-parse','--verify','--quiet','63479426^{commit}'],optional=True,timeout=30)

    def test_existing_completed_card_is_reused_with_missing_local_state(self):
        with self.git() as command, mock.patch.object(m,'latest_build',return_value=captured()), \
             mock.patch.object(m,'state_get',return_value={}), mock.patch.object(m,'state_save') as saved:
            m.build_agent('aurora-card',SHA)
            self.assertEqual(saved.call_args.args[0]['built']['aurora-card'],SHA)
            self.assertEqual(command.call_count,1) # Git read; no amctl build create

    def test_wrong_source_coordinates_fail_without_git_or_write(self):
        for field,value in [('branch','feature'),('repoUrl','https://example.org/other'),
                            ('appPath','/deployment/agent-manager/apps/aurora-pix')]:
            b=captured()
            b['buildParameters'][field]=value
            with self.subTest(field=field), mock.patch.object(m,'cmd') as command:
                with self.assertRaisesRegex(RuntimeError,'source coordinates'):
                    m.verified_build_revision(b,'aurora-card',SHA)
                command.assert_not_called()

    def test_ambiguous_short_prefix_fails_closed(self):
        with self.git('63479426'+'b'*32):
            with self.assertRaisesRegex(RuntimeError,'does not resolve uniquely'):
                m.verified_build_revision(captured(),'aurora-card',SHA)

    def test_unknown_local_commit_fails_closed(self):
        with self.git('',1):
            with self.assertRaisesRegex(RuntimeError,'does not resolve uniquely'):
                m.verified_build_revision(captured(),'aurora-card',SHA)

    def test_wrong_commit_prefix_is_distinct(self):
        with mock.patch.object(m,'cmd') as command:
            self.assertEqual(m.verified_build_revision(captured(commit='aabbccdd'),'aurora-card',SHA),'aabbccdd')
            command.assert_not_called()

    def test_mismatched_full_and_short_commits_fail(self):
        b=captured()
        b['commitSha']='b'*40
        with mock.patch.object(m,'cmd') as command:
            with self.assertRaisesRegex(RuntimeError,'inconsistent'):
                m.verified_build_revision(b,'aurora-card',SHA)
            command.assert_not_called()

    def test_commit_id_malformed_fails_closed(self):
        for invalid in ('6347942','6347942Z',None,456):
            b=captured(); b['buildParameters']['commitId']=invalid
            with self.subTest(invalid=invalid), mock.patch.object(m,'cmd') as command:
                if invalid is None:
                    self.assertIsNone(m.verified_build_revision(b,'aurora-card',SHA))
                else:
                    with self.assertRaisesRegex(RuntimeError,'invalid buildParameters'):
                        m.verified_build_revision(b,'aurora-card',SHA)
                command.assert_not_called()

    def test_actual_cli_build_list_envelope(self):
        import json
        body={'instance':'default','org':'default','project':'banco-aurora',
              'agent':'aurora-card','data':{'builds':[captured()],
              'limit':50,'offset':0,'total':1}}
        response=subprocess.CompletedProcess([],0,json.dumps(body),'')
        with mock.patch.object(m,'cmd',return_value=response) as command:
            result=m.latest_build('aurora-card')
            self.assertEqual(result['buildParameters']['commitId'],'63479426')
            self.assertEqual(result['status'],'Completed')
            self.assertEqual(command.call_count,1)

    def test_queued_build_polls_same_id_without_create(self):
        status=[captured(status='Queued'),captured(status='Building'),captured(status='Completed')]
        with self.git() as command, mock.patch.object(m,'latest_build',side_effect=status), \
             mock.patch.object(m,'state_get',return_value={}),mock.patch.object(m,'state_save') as saved, \
             mock.patch.object(m.time,'sleep'):
            m.build_agent('aurora-card',SHA)
            self.assertEqual(saved.call_args.args[0]['built']['aurora-card'],SHA)
            self.assertEqual(command.call_count,5)  # Git verification only; no build create

    def test_success_and_wrong_sha_do_not_reuse(self):
        b=captured(commit='aabbccdd')
        with mock.patch.object(m,'latest_build',return_value=b), mock.patch.object(m,'state_get',return_value={}),\
             mock.patch.object(m,'cmd',side_effect=RuntimeError('new build call prevented')) as command:
            # Completed stale build is grounds for a new build, not reuse.
            with self.assertRaisesRegex(RuntimeError,'new build call prevented'):
                m.build_agent('aurora-card',SHA)
            self.assertTrue(any('build' in ' '.join(c.args[0]) for c in command.call_args_list))

if __name__=='__main__': unittest.main()
