"""Protect existing Agent Manager builds from accidental duplicated creates."""
import importlib.util
import pathlib
import unittest
from unittest import mock

ROOT=pathlib.Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('aurora_build',ROOT/'scripts/aurora-full-reconcile.py')
m=importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
SHA='634794269c3f'+'a'*28
OTHER='b'*40

class BuildReuseTests(unittest.TestCase):
    def test_successful_remote_matching_sha_reused_after_local_state_lost(self):
        with mock.patch.object(m,'state_get',return_value={}), \
             mock.patch.object(m,'latest_build',return_value={'buildStatus':'Completed','commitSha':SHA}), \
             mock.patch.object(m,'state_save') as save, mock.patch.object(m,'cmd') as command:
            m.build_agent('aurora-account',SHA)
            save.assert_called_once()
            self.assertEqual(save.call_args.args[0]['built']['aurora-account'],SHA)
            command.assert_not_called()

    def test_successful_build_with_matching_private_state_reused_without_remote_sha(self):
        with mock.patch.object(m,'state_get',return_value={'built':{'aurora-account':SHA}}), \
             mock.patch.object(m,'latest_build',return_value={'buildStatus':'Succeeded'}), \
             mock.patch.object(m,'state_save') as save, mock.patch.object(m,'cmd') as command:
            m.build_agent('aurora-account',SHA)
            save.assert_not_called()
            command.assert_not_called()

    def test_successful_remote_build_with_unknown_commit_and_no_state_refuses_duplicate(self):
        with mock.patch.object(m,'state_get',return_value={}), \
             mock.patch.object(m,'latest_build',return_value={'buildStatus':'Completed'}), \
             mock.patch.object(m,'cmd') as command:
            with self.assertRaisesRegex(RuntimeError,'commit cannot be verified'):
                m.build_agent('aurora-account',SHA)
            command.assert_not_called()

    def test_in_progress_matching_commit_is_resumed_without_duplicate(self):
        states=[{'buildStatus':'Building','commitSha':SHA,'buildName':'job-1'},
                {'buildStatus':'Completed','commitSha':SHA,'buildName':'job-1'}]
        with mock.patch.object(m,'state_get',return_value={}), \
             mock.patch.object(m,'latest_build',side_effect=states), \
             mock.patch.object(m,'state_save') as saved, \
             mock.patch.object(m.time,'sleep'), mock.patch.object(m,'cmd') as command:
            m.build_agent('aurora-account',SHA)
            self.assertEqual(saved.call_args.args[0]['built']['aurora-account'],SHA)
            command.assert_not_called()

    def test_different_commit_while_building_refuses(self):
        with mock.patch.object(m,'state_get',return_value={}), \
             mock.patch.object(m,'latest_build',return_value={'buildStatus':'Building','commitSha':OTHER}), \
             mock.patch.object(m,'cmd') as command:
            with self.assertRaisesRegex(RuntimeError,'existing managed build for commit'):
                m.build_agent('aurora-account',SHA)
            command.assert_not_called()

    def test_unavailable_build_inventory_with_local_state_refuses(self):
        with mock.patch.object(m,'state_get',return_value={'built':{'aurora-account':SHA}}), \
             mock.patch.object(m,'latest_build',return_value=None), \
             mock.patch.object(m,'cmd') as command:
            with self.assertRaisesRegex(RuntimeError,'no remote managed build'):
                m.build_agent('aurora-account',SHA)
            command.assert_not_called()

    def test_commit_sha_does_not_confuse_proxy_uuid_with_git_commit(self):
        self.assertIsNone(m.build_commit_sha({'buildStatus':'Completed','uuid':'1'*36}))
        self.assertIsNone(m.build_commit_sha({'commit':'b'*40,'source':{'commit':'a'*40}}))
        self.assertEqual(m.build_commit_sha({'source':{'commitSha':SHA}}),SHA)

if __name__=='__main__':unittest.main()
