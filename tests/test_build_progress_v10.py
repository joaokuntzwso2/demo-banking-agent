"""v1.0.0 managed build progress contract: never duplicate a queued build."""
import importlib.util
from pathlib import Path
import subprocess
import unittest
from unittest.mock import patch

SOURCE=Path(__file__).resolve().parents[1]/'scripts'/'aurora-full-reconcile.py'
spec=importlib.util.spec_from_file_location('aurora_reconcile_v10',SOURCE)
reconcile=importlib.util.module_from_spec(spec)
spec.loader.exec_module(reconcile)
SHA='a'*40

class BuildProgressTests(unittest.TestCase):
    def test_new_build_progress_from_queued_to_success(self):
        messages=[]; saved=[]
        states=iter([None, {'buildName':'new-job','buildStatus':'queued','commitSha':SHA},
                     {'buildName':'new-job','buildStatus':'running','commitSha':SHA},
                     {'buildName':'new-job','buildStatus':'succeeded','commitSha':SHA}])
        with patch.object(reconcile,'log',side_effect=messages.append), \
             patch.object(reconcile,'state_get',return_value={}), \
             patch.object(reconcile,'state_save',side_effect=saved.append), \
             patch.object(reconcile,'latest_build',side_effect=lambda agent:next(states)), \
             patch.object(reconcile,'cmd',return_value=subprocess.CompletedProcess([],0,'','')) as command, \
             patch.object(reconcile.time,'sleep'),patch.object(reconcile.time,'monotonic',side_effect=range(50)):
            reconcile.build_agent('aurora-account',SHA)
        self.assertEqual(command.call_count,1)
        self.assertIn('build create',' '.join(command.call_args.args[0]))
        self.assertEqual(saved[0]['built']['aurora-account'],SHA)
        combined='\n'.join(messages)
        for marker in ('requesting managed build','status=queued','status=running','Build succeeded'):
            self.assertIn(marker,combined)

    def test_build_create_timeout_explains_backend_may_have_accepted_request(self):
        with patch.object(reconcile,'state_get',return_value={}),patch.object(reconcile,'log'), \
             patch.object(reconcile,'latest_build',return_value=None), \
             patch.object(reconcile,'cmd',side_effect=subprocess.TimeoutExpired('amctl',180)):
            with self.assertRaisesRegex(RuntimeError,'server might have accepted'):
                reconcile.build_agent('aurora-account',SHA)

    def test_existing_verified_build_is_not_retriggered(self):
        with patch.object(reconcile,'state_get',return_value={'built':{'aurora-account':SHA}}), \
             patch.object(reconcile,'latest_build',return_value={'buildStatus':'succeeded'}), \
             patch.object(reconcile,'log'),patch.object(reconcile,'cmd') as command:
            reconcile.build_agent('aurora-account',SHA)
            command.assert_not_called()

    def test_queued_existing_build_not_recreated_on_missing_revision(self):
        with patch.object(reconcile,'state_get',return_value={}), \
             patch.object(reconcile,'latest_build',return_value={'buildStatus':'queued','buildName':'previous-job'}), \
             patch.object(reconcile,'cmd') as command:
            with self.assertRaisesRegex(RuntimeError,'commit cannot be verified'):
                reconcile.build_agent('aurora-account',SHA)
            command.assert_not_called()

if __name__=='__main__': unittest.main()
