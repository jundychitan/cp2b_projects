"""Keep password verification security unchanged while diagnosing slow devices."""

import shlex
import unittest
from pathlib import Path
from unittest.mock import patch

from flask import Flask

import auth


class LoginTimingTests(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)

    def test_verification_uses_existing_hash_and_returns_original_result(self):
        for valid in (True, False):
            with self.subTest(valid=valid), self.app.app_context():
                with patch.object(auth, 'check_password_hash', return_value=valid) as verify:
                    with patch.object(auth, 'perf_counter', side_effect=[10.0, 10.2]):
                        with patch.object(self.app.logger, 'warning') as log:
                            self.assertEqual(auth._verify_login_password('existing-hash', 'test-password'), valid)
                            verify.assert_called_once_with('existing-hash', 'test-password')
                            log.assert_not_called()

    def test_slow_verification_logs_duration_only_for_either_result(self):
        for valid in (True, False):
            with self.subTest(valid=valid), self.app.app_context():
                with patch.object(auth, 'check_password_hash', return_value=valid):
                    with patch.object(auth, 'perf_counter', side_effect=[10.0, 11.5]):
                        with patch.object(self.app.logger, 'warning') as log:
                            self.assertEqual(auth._verify_login_password('existing-hash', 'test-password'), valid)
                            log.assert_called_once_with('Slow login password verification: %.3f seconds', 1.5)

    def test_service_keeps_bounded_concurrency_for_hardware_and_login(self):
        root = Path(__file__).resolve().parents[1]
        content = (root / 'amr_gateway_webui.service').read_text()
        command = next(line.partition('=')[2] for line in content.splitlines()
                       if line.startswith('ExecStart='))
        args = shlex.split(command)
        self.assertEqual(args[args.index('--worker-class') + 1], 'gthread')
        self.assertEqual(args[args.index('-w') + 1], '2')
        self.assertEqual(args[args.index('--threads') + 1], '2')
        self.assertEqual(args[-1], 'app:app')


if __name__ == '__main__':
    unittest.main()
