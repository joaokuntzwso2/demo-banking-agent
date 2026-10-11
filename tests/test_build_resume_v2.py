"""Idempotent build recovery tests: real operation boundaries, no write on reuse."""
import importlib.util
import pathlib
import subprocess
import unittest
from unittest import mock

ROOT=pathlib.Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('aurora_resume_v2', ROOT/'scripts/aurora-full-reconcile.py')
m=importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
SHA='634794269c3f'+'a'*28
OTHER='b'*40


def build(status, commit=SHA, uuid='build-1'):
    d={'buildStatus': status, 'buildName':uuid}
    if commit is not None:
        d['commitSha']=commit
    return d


class ExistingBuildResumeTests(unittest.TestCase):
    def test_queued_build_with_same_commit_resumed_without_create(self):
        steps=[build('Queued'),build('Building'),build('Completed')]
        with mock.patch.object(m,'latest_build',side_effect=steps) as read, \
             mock.patch.object(m,'state_get',return_value={}), \
             mock.patch.object(m,'state_save') as saved, \
             mock.patch.object(m,'cmd') as command, \
             mock.patch.object(m.time,'sleep'):
            m.build_agent('aurora-account',SHA)
            self.assertEqual(read.call_count,3)
            self.assertEqual(saved.call_args.args[0]['built']['aurora-account'],SHA)
            command.assert_not_called()

    def test_queued_build_without_commit_refuses_create(self):
        with mock.patch.object(m,'latest_build',return_value=build('Queued',commit=None)), \
             mock.patch.object(m,'state_get',return_value={}), \
             mock.patch.object(m,'state_save') as saved, \
             mock.patch.object(m,'cmd') as command:
            with self.assertRaisesRegex(RuntimeError,'commit cannot be verified'):
                m.build_agent('aurora-account',SHA)
            saved.assert_not_called()
            command.assert_not_called()

    def test_existing_build_for_another_commit_refuses_create(self):
        with mock.patch.object(m,'latest_build',return_value=build('Queued',commit=OTHER)), \
             mock.patch.object(m,'state_get',return_value={}), \
             mock.patch.object(m,'cmd') as command:
            with self.assertRaisesRegex(RuntimeError,'existing managed build for commit'):
                m.build_agent('aurora-account',SHA)
            command.assert_not_called()

    def test_switched_identity_during_polling_not_verified(self):
        steps=[build('Queued'),build('Completed',uuid='unrelated-build')]
        with mock.patch.object(m,'latest_build',side_effect=steps), \
             mock.patch.object(m,'state_get',return_value={}), \
             mock.patch.object(m,'state_save') as saved, \
             mock.patch.object(m,'cmd') as command, \
             mock.patch.object(m.time,'sleep'):
            with self.assertRaisesRegex(RuntimeError,'identity changed'):
                m.build_agent('aurora-account',SHA)
            saved.assert_not_called()
            command.assert_not_called()

    def test_different_commit_appears_during_polling(self):
        steps=[build('Queued'),build('Completed',commit=OTHER)]
        with mock.patch.object(m,'latest_build',side_effect=steps), \
             mock.patch.object(m,'state_get',return_value={}), \
             mock.patch.object(m,'state_save') as saved, \
             mock.patch.object(m,'cmd') as command, \
             mock.patch.object(m.time,'sleep'):
            with self.assertRaisesRegex(RuntimeError,'different Git commit'):
                m.build_agent('aurora-account',SHA)
            saved.assert_not_called()
            command.assert_not_called()

    def test_disappearing_build_never_requests_duplicate(self):
        with mock.patch.object(m,'latest_build',side_effect=[build('Queued'), None]), \
             mock.patch.object(m,'state_get',return_value={}), \
             mock.patch.object(m,'state_save') as saved, \
             mock.patch.object(m,'cmd') as command, \
             mock.patch.object(m.time,'sleep'):
            with self.assertRaisesRegex(RuntimeError,'disappeared'):
                m.build_agent('aurora-account',SHA)
            saved.assert_not_called()
            command.assert_not_called()

    def test_failed_build_never_recreated(self):
        with mock.patch.object(m,'latest_build',side_effect=[build('Queued'),build('Failed')]), \
             mock.patch.object(m,'state_get',return_value={}), \
             mock.patch.object(m,'state_save') as saved, \
             mock.patch.object(m,'cmd') as command, \
             mock.patch.object(m.time,'sleep'):
            with self.assertRaisesRegex(RuntimeError,'existing managed build failed'):
                m.build_agent('aurora-account',SHA)
            saved.assert_not_called()
            command.assert_not_called()

    def test_inventory_timeout_does_not_create(self):
        with mock.patch.object(m,'latest_build',side_effect=subprocess.TimeoutExpired('amctl',180)), \
             mock.patch.object(m,'state_get',return_value={}), \
             mock.patch.object(m,'cmd') as command:
            with self.assertRaisesRegex(RuntimeError,'inventory read timed out'):
                m.build_agent('aurora-account',SHA)
            command.assert_not_called()

    def test_unknown_status_does_not_create(self):
        with mock.patch.object(m,'latest_build',return_value=build('SomethingElse')), \
             mock.patch.object(m,'state_get',return_value={}), \
             mock.patch.object(m,'cmd') as command:
            with self.assertRaisesRegex(RuntimeError,'existing managed build is'):
                m.build_agent('aurora-account',SHA)
            command.assert_not_called()

if __name__=='__main__': unittest.main()
