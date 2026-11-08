"""Reboot and session logout regression tests; no actual reboot is scheduled."""

import subprocess
import unittest
from pathlib import Path
from unittest.mock import patch

from flask import Flask
from flask_login import login_required, login_user

import auth
from blueprints import diagnostic


class RebootTests(unittest.TestCase):
    def setUp(self):
        app = Flask(__name__, template_folder=str(Path(__file__).resolve().parents[1] / 'templates'))
        app.config.update(TESTING=True, SECRET_KEY='test-only')
        auth.login_manager.init_app(app)
        app.register_blueprint(auth.auth_bp)
        app.register_blueprint(diagnostic.diagnostic_bp)

        @app.route('/')
        @login_required
        def index():
            return 'protected'

        @app.route('/test-login/<role>')
        def test_login(role):
            login_user(auth.User(role, role), remember=True)
            return 'test login'

        self.client = app.test_client()
        patcher = patch('auth._load_users', return_value={
            role: {'role': role} for role in ('admin', 'dec', 'amr')
        })
        patcher.start()
        self.addCleanup(patcher.stop)
        patcher = patch.object(diagnostic.subprocess, 'run',
                               return_value=subprocess.CompletedProcess([], 0, '', ''))
        self.run = patcher.start()
        self.addCleanup(patcher.stop)

    def login(self, role='admin'):
        self.client.get(f'/test-login/{role}')
        with self.client.session_transaction() as session:
            session['reboot_csrf_token'] = 'test-csrf'

    def reboot(self, token='test-csrf'):
        return self.client.post('/api/diagnostic/reboot', json={},
                                headers={'X-CSRF-Token': token})

    def test_success_logs_out_session_and_remember_cookie_before_reboot(self):
        for role in ('admin', 'dec'):
            with self.subTest(role=role):
                self.login(role)
                self.assertIsNotNone(self.client.get_cookie('remember_token'))
                response = self.reboot()
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.get_json()['redirect'], '/rebooting')
                self.assertEqual(response.headers['Cache-Control'], 'no-store')
                self.assertIsNone(self.client.get_cookie('remember_token'))
                with self.client.session_transaction() as session:
                    self.assertNotIn('_user_id', session)
                    self.assertNotIn('reboot_csrf_token', session)
                self.assertEqual(self.client.get('/').status_code, 302)
                self.assertEqual(self.client.get('/login').status_code, 200)
        self.run.assert_called_with([
            'systemd-run', '--unit=amr-webui-reboot', '--on-active=5s',
            '/usr/bin/systemctl', 'reboot',
        ], capture_output=True, text=True, timeout=10)

    def test_readonly_and_anonymous_cannot_reboot(self):
        self.assertEqual(self.reboot().status_code, 302)
        self.login('amr')
        self.assertEqual(self.reboot().status_code, 403)
        self.run.assert_not_called()

    def test_csrf_required_before_scheduling_or_logout(self):
        self.login()
        for token in ('', 'wrong', '\u00e9'):
            self.assertEqual(self.reboot(token).status_code, 403)
        with self.client.session_transaction() as session:
            del session['reboot_csrf_token']
        self.assertEqual(self.reboot().status_code, 403)
        self.assertEqual(self.client.get('/').status_code, 200)
        self.run.assert_not_called()

    def test_scheduling_errors_do_not_logout_or_claim_success(self):
        for result, code in (
            (subprocess.CompletedProcess([], 1, '', 'Access denied'), 500),
            (OSError('missing'), 503),
            (subprocess.TimeoutExpired('systemd-run', 10), 504),
        ):
            with self.subTest(code=code):
                self.login()
                self.run.side_effect = result if isinstance(result, Exception) else None
                if not isinstance(result, Exception):
                    self.run.return_value = result
                response = self.reboot()
                self.assertEqual(response.status_code, code)
                self.assertIn('error', response.get_json())
                self.assertEqual(self.client.get('/').status_code, 200)
                self.assertIsNotNone(self.client.get_cookie('remember_token'))

    def test_waiting_page_and_readiness_are_public_and_uncached(self):
        response = self.client.get('/rebooting')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers['Cache-Control'], 'no-store')
        html = response.get_data(as_text=True)
        self.assertIn('Rebooting', html)
        self.assertIn('/login', html)
        self.assertNotIn('js/app.js', html)
        response = self.client.get('/api/diagnostic/ready')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json(), {'status': 'ready'})
        self.assertEqual(response.headers['Cache-Control'], 'no-store')
        self.run.assert_not_called()


if __name__ == '__main__':
    unittest.main()
