"""v14.2 publisher handoff safety regression tests. No real processes or network."""
import importlib.util
from pathlib import Path
import signal
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]


def module(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    obj = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(obj)
    return obj


installer = module(ROOT/'scripts'/'apply-aurora-v14-2-handoff.py', 'v142_installer')
watch = module(ROOT/'scripts'/'aurora-mcp-forward-guard-v14.py', 'v142_watch')


class PublisherIntegrationTests(unittest.TestCase):
    def test_exact_stop_all_only_patched_and_repeated_noop(self):
        original = '''#!/usr/bin/env bash
stop_all() {
  echo "Stopping tunnel"
  kill_owned_tunnel
}
up() {
  echo "Incomplete or stale tunnel state detected. Reconciling local processes."
  stop_all
}
'''
        new = installer.transform_publisher(original)
        self.assertEqual(new.count(installer.MARK), 1)
        self.assertIn('handoff', new.split('echo "Stopping tunnel"')[0])
        self.assertEqual(installer.transform_publisher(new), new)
        self.assertEqual(new.count('kill_owned_tunnel'), 1)

    def test_refuses_unknown_or_partial_publisher(self):
        with self.assertRaisesRegex(RuntimeError, 'uniquely identify'):
            installer.transform_publisher('stop_all() {\n}\nstop_all(){\n}\n')
        with self.assertRaisesRegex(RuntimeError, 'uniquely identify'):
            installer.transform_publisher('stop_all_other() {\n}\n')
        with self.assertRaisesRegex(RuntimeError, 'incomplete'):
            installer.transform_publisher('stop_all(){\n  '+installer.MARK+'\n}\n')


class HandoffTests(unittest.TestCase):
    def test_no_forward_record_does_not_terminate_anything(self):
        with mock.patch.object(watch, 'stop_monitor_and_wait') as stop, \
             mock.patch.object(watch.Path, 'exists', return_value=False), \
             mock.patch.object(watch.os, 'kill') as kill:
            watch.handoff_to_publisher()
            stop.assert_called_once()
            kill.assert_not_called()

    def test_releases_only_exact_owned_forward(self):
        mock_path = mock.Mock()
        mock_path.exists.return_value = True
        mock_path.is_symlink.return_value = False
        mock_path.is_file.return_value = True
        mock_path.read_text.return_value = '81234\n'
        with mock.patch.object(watch, 'stop_monitor_and_wait') as stop, \
             mock.patch.object(watch, 'DIR', mock.MagicMock(__truediv__=mock.Mock(return_value=mock_path))), \
             mock.patch.object(watch, 'listener_pids', side_effect=[[81234], []]), \
             mock.patch.object(watch, 'is_our_kubectl', return_value=True), \
             mock.patch.object(watch.os, 'kill') as kill:
            watch.handoff_to_publisher()
            stop.assert_called_once()
            kill.assert_called_once_with(81234, signal.SIGTERM)
            mock_path.unlink.assert_called_once()

    def test_does_not_kill_ssh_listener_even_if_pid_record_matches(self):
        mock_path = mock.Mock()
        mock_path.exists.return_value = True
        mock_path.is_symlink.return_value = False
        mock_path.is_file.return_value = True
        mock_path.read_text.return_value = '81234'
        with mock.patch.object(watch, 'stop_monitor_and_wait'), \
             mock.patch.object(watch, 'DIR', mock.MagicMock(__truediv__=mock.Mock(return_value=mock_path))), \
             mock.patch.object(watch, 'listener_pids', return_value=[81234]), \
             mock.patch.object(watch, 'is_our_kubectl', return_value=False), \
             mock.patch.object(watch.os, 'kill') as kill:
            with self.assertRaisesRegex(RuntimeError, 'identity changed'):
                watch.handoff_to_publisher()
            kill.assert_not_called()

    def test_nonwatchdog_listener_left_for_publisher_to_handle(self):
        mock_path = mock.Mock()
        mock_path.exists.return_value = True
        mock_path.is_symlink.return_value = False
        mock_path.is_file.return_value = True
        mock_path.read_text.return_value = '81234'
        with mock.patch.object(watch, 'stop_monitor_and_wait'), \
             mock.patch.object(watch, 'DIR', mock.MagicMock(__truediv__=mock.Mock(return_value=mock_path))), \
             mock.patch.object(watch, 'listener_pids', return_value=[55555]), \
             mock.patch.object(watch.os, 'kill') as kill:
            watch.handoff_to_publisher()
            kill.assert_not_called()

    def test_monitor_wait_is_bounded_and_fails_closed(self):
        with mock.patch.object(watch, 'monitor_running', return_value=True), \
             mock.patch.object(watch, 'DIR', mock.MagicMock()) as join, \
             mock.patch.object(watch.os, 'kill') as kill, \
             mock.patch.object(watch.time, 'sleep') as sleep:
            (join / 'v14-monitor.pid').read_text.return_value = '81234'
            with self.assertRaisesRegex(RuntimeError, 'monitor did not stop'):
                watch.stop_monitor_and_wait()
            kill.assert_called_once_with(81234, signal.SIGTERM)
            self.assertEqual(sleep.call_count, 50)

    def test_monitor_can_exit_cleanly(self):
        with mock.patch.object(watch, 'monitor_running', side_effect=[True, True, False]), \
             mock.patch.object(watch, 'DIR', mock.MagicMock()) as join, \
             mock.patch.object(watch.os, 'kill'), \
             mock.patch.object(watch.time, 'sleep') as sleep:
            (join / 'v14-monitor.pid').read_text.return_value = '81234'
            watch.stop_monitor_and_wait()
            self.assertEqual(sleep.call_count, 1)


if __name__ == '__main__':
    unittest.main()
