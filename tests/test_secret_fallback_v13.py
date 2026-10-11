"""Defense-in-depth contract for missing/public service authentication secrets."""
from __future__ import annotations

import importlib.util
import os
import pathlib
import runpy
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
SEC_FILE = ROOT / 'banking_common' / 'security.py'
CONFIG_FILE = ROOT / 'banking_common' / 'config.py'


def load_security():
    spec = importlib.util.spec_from_file_location('aurora_secret_fallback_v13_test', SEC_FILE)
    if not spec or not spec.loader:
        raise AssertionError(f'Missing canonical source {SEC_FILE}')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ServiceKeyFallbackTests(unittest.TestCase):
    def setUp(self):
        self.s = load_security()

    def test_configured_service_key_works_independent_of_wso2_flag(self):
        for managed in ('false', 'true'):
            with self.subTest(managed=managed), mock.patch.dict(os.environ, {
                'WSO2_MODE': managed,
                'BANK_CORE_READ_KEY': 'z' * 32,
            }, clear=True):
                self.assertEqual(self.s.secret('BANK_CORE_READ_KEY'), 'z' * 32)

    def test_missing_key_denied_by_default_even_outside_wso2(self):
        with mock.patch.dict(os.environ, {'WSO2_MODE': 'false'}, clear=True):
            with self.assertRaises(RuntimeError):
                self.s.secret('BANK_APPROVAL_KEY')

    def test_public_key_denied_even_when_explicitly_configured(self):
        with mock.patch.dict(os.environ, {
            'WSO2_MODE': 'false',
            'BANK_CORE_READ_KEY': 'local-demo-internal-key',
        }, clear=True):
            with self.assertRaises(RuntimeError):
                self.s.secret('BANK_CORE_READ_KEY')

    def test_require_secret_does_not_accept_missing_key(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(RuntimeError):
                self.s.require_secret('local-demo-internal-key', 'BANK_CORE_READ_KEY')

    def test_explicit_local_rehearsal_optin_only_outside_k8s_and_wso2(self):
        env = {
            'AURORA_ALLOW_PUBLIC_LOCAL_DEMO_KEYS': 'local-only',
            'WSO2_MODE': 'false',
        }
        with mock.patch.dict(os.environ, env, clear=True):
            self.assertEqual(self.s.secret('BANK_CORE_READ_KEY'), 'local-demo-internal-key')
        with mock.patch.dict(os.environ, {**env, 'WSO2_MODE': 'true'}, clear=True):
            with self.assertRaises(RuntimeError):
                self.s.secret('BANK_CORE_READ_KEY')
        with mock.patch.dict(os.environ, {**env, 'KUBERNETES_SERVICE_HOST': 'kubernetes.default'}, clear=True):
            with self.assertRaises(RuntimeError):
                self.s.secret('BANK_CORE_READ_KEY')

    def test_config_has_no_implicit_public_key_defaults(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            config = runpy.run_path(str(CONFIG_FILE))
            self.assertEqual(config['INTERNAL_KEY'], '')
            self.assertEqual(config['MCP_TOKEN'], '')


if __name__ == '__main__':
    unittest.main()
