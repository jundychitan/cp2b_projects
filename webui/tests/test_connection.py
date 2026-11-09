"""Isolated service lifecycle tests; never start or stop real device services."""

import importlib
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import call, patch

from flask import Flask, render_template
from flask_login import login_required

import auth
from blueprints import amr, connection, diagnostic, rtu


def service_state(boot='enabled', active='active', sub='running'):
    return True, (f'LoadState=loaded\nActiveState={active}\n'
                  f'SubState={sub}\nUnitFileState={boot}'), ''


class ConnectionTests(unittest.TestCase):
    def setUp(self):
        app = Flask(__name__, template_folder=str(Path(__file__).resolve().parents[1] / 'templates'))
        app.config.update(TESTING=True, SECRET_KEY='test-only')
        auth.login_manager.init_app(app)
        for blueprint in (auth.auth_bp, amr.amr_bp, connection.connection_bp, rtu.rtu_bp, diagnostic.diagnostic_bp):
            app.register_blueprint(blueprint)

        @app.route('/')
        @login_required
        def index():
            return render_template('base.html')

        self.client = app.test_client()
        for patcher in (
            patch('auth._load_users', return_value={
                role: {'role': role} for role in ('admin', 'dec', 'amr')
            }),
            patch.object(connection, '_run', return_value=service_state()),
        ):
            mock = patcher.start()
            self.addCleanup(patcher.stop)
            self.command = mock
        self.login('admin')

    def login(self, role):
        with self.client.session_transaction() as session:
            session.clear()
            if role:
                session['_user_id'] = role
                session['_fresh'] = True
            session['connection_csrf_token'] = 'test-csrf'

    def toggle(self, enabled, token='test-csrf'):
        return self.client.post('/api/connection/toggle', json={'enabled': enabled},
                                headers={'X-CSRF-Token': token})

    def test_admin_only_api_and_markup(self):
        for role in ('dec', 'amr', None):
            with self.subTest(role=role):
                self.login(role)
                expected = 403 if role else 302
                self.assertEqual(self.client.get('/api/connection/status').status_code, expected)
                self.assertEqual(self.toggle(False).status_code, expected)
                response = self.client.get('/')
                self.assertNotIn('id="connection-tab"', response.get_data(as_text=True))
                self.assertNotIn('id="connEnableBtn"', response.get_data(as_text=True))
        self.command.assert_not_called()

    def test_admin_markup_creates_csrf_token(self):
        with self.client.session_transaction() as session:
            del session['connection_csrf_token']
        response = self.client.get('/')
        self.assertEqual(response.status_code, 200)
        self.assertIn('id="connection-tab"', response.get_data(as_text=True))
        with self.client.session_transaction() as session:
            token = session['connection_csrf_token']
        self.assertIn(f'data-csrf-token="{token}"', response.get_data(as_text=True))
        self.command.assert_not_called()

    def test_status_uses_systemd_not_process_name_or_config_path(self):
        response = self.client.get('/api/connection/status')
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.get_json()['running'])
        self.assertTrue(response.get_json()['enabled'])
        self.assertEqual(response.headers['Cache-Control'], 'no-store')
        self.command.assert_called_once_with([
            'systemctl', '--no-pager', 'show',
            '--property=LoadState,ActiveState,SubState,UnitFileState',
            connection.CONNECTION_MANAGER_SERVICE,
        ])

    def test_runtime_and_boot_state_are_independent(self):
        for boot, active, sub in (
            ('disabled', 'active', 'running'),
            ('enabled', 'inactive', 'dead'),
            ('enabled-runtime', 'active', 'running'),
            ('masked', 'inactive', 'dead'),
            ('enabled', 'activating', 'auto-restart'),
        ):
            with self.subTest(boot=boot, active=active):
                self.command.return_value = service_state(boot, active, sub)
                data = self.client.get('/api/connection/status').get_json()
                self.assertEqual(data['boot_state'], boot)
                self.assertEqual(data['active_state'], active)
                self.assertEqual(data['enabled'], boot == 'enabled')
                self.assertEqual(data['running'], active == 'active')

    def test_enable_and_disable_persist_and_preserve_client_files(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / 'client_config.json'
            original = b'{ "auto_connect": true, "custom": {"port": 9001} }\n'
            config.write_bytes(original)
            for enabled in (False, True):
                with self.subTest(enabled=enabled):
                    final = service_state() if enabled else service_state('disabled', 'inactive', 'dead')
                    self.command.reset_mock()
                    self.command.side_effect = [service_state(), (True, '', ''), final]
                    response = self.toggle(enabled)
                    self.assertEqual(response.status_code, 200, response.get_json())
                    self.assertEqual(response.get_json()['enabled'], enabled)
                    self.assertEqual(response.get_json()['running'], enabled)
                    self.assertEqual(self.command.call_args_list[1], call([
                        'systemctl', 'enable' if enabled else 'disable', '--now',
                        connection.CONNECTION_MANAGER_SERVICE,
                    ]))
                    self.assertEqual(self.command.call_count, 3)
                    self.assertEqual(config.read_bytes(), original)
                    self.assertEqual(list(Path(directory).iterdir()), [config])

    def test_csrf_rejects_missing_wrong_and_nonascii_tokens(self):
        for token in ('', 'invalid', '\u00e9'):
            self.assertEqual(self.toggle(False, token).status_code, 403)
        with self.client.session_transaction() as session:
            del session['connection_csrf_token']
        self.assertEqual(self.toggle(False).status_code, 403)
        self.command.assert_not_called()

    def test_only_json_booleans_are_accepted(self):
        for value in (None, '', 'false', 0, 1, [], {}):
            with self.subTest(value=value):
                self.assertEqual(self.toggle(value).status_code, 400)
        for body in ({}, [], None):
            response = self.client.post('/api/connection/toggle', json=body,
                                        headers={'X-CSRF-Token': 'test-csrf'})
            self.assertEqual(response.status_code, 400)
        self.assertEqual(self.client.get('/api/connection/toggle').status_code, 405)
        self.command.assert_not_called()

    def test_missing_or_unreadable_service_never_runs_toggle(self):
        for result in (
            (False, 'LoadState=not-found', ''),
            (False, '', 'Access denied'),
            (True, 'LoadState=loaded', ''),
        ):
            with self.subTest(result=result):
                self.command.reset_mock()
                self.command.return_value = result
                self.assertEqual(self.client.get('/api/connection/status').status_code, 503)
                self.assertEqual(self.toggle(False).status_code, 503)
                self.assertEqual(self.command.call_count, 2)

    def test_command_failure_reports_possible_partial_change(self):
        self.command.side_effect = [service_state(), (False, '', 'stop timed out')]
        response = self.toggle(False)
        self.assertEqual(response.status_code, 500)
        self.assertIn('state may have changed', response.get_json()['error'])
        self.assertEqual(self.command.call_count, 2)

    def test_post_command_status_failure(self):
        self.command.side_effect = [service_state(), (True, '', ''), (False, '', 'Access denied')]
        response = self.toggle(True)
        self.assertEqual(response.status_code, 503)
        self.assertIn('could not be verified', response.get_json()['error'])

    def test_requested_state_not_reached_is_not_reported_as_success(self):
        for enabled, final in (
            (False, service_state('disabled', 'deactivating', 'stop-sigterm')),
            (True, service_state('enabled', 'failed', 'failed')),
            (True, service_state('enabled-runtime', 'active', 'running')),
        ):
            with self.subTest(enabled=enabled, final=final):
                self.command.side_effect = [service_state(), (True, '', ''), final]
                response = self.toggle(enabled)
                self.assertEqual(response.status_code, 409)

    def test_legacy_unmanaged_routes_are_unavailable(self):
        for action in ('start', 'stop', 'restart'):
            self.assertEqual(self.client.post(f'/api/connection/{action}', json={}).status_code, 404)
        self.command.assert_not_called()

    def test_real_command_wrapper_is_shell_free_and_handles_errors(self):
        with patch.object(connection.subprocess, 'run') as run:
            args = ['systemctl', 'disable', '--now', connection.CONNECTION_MANAGER_SERVICE]
            run.return_value = subprocess.CompletedProcess(args, 0, 'done\n', '')
            self.assertEqual(self._real_run(args), (True, 'done', ''))
            run.assert_called_once_with(args, capture_output=True, text=True, timeout=30)
            for exception in (OSError(), subprocess.TimeoutExpired(args, 30)):
                run.side_effect = exception
                self.assertFalse(self._real_run(args)[0])

    def test_application_registers_connection_blueprint(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(auth, 'USERS_FILE', str(Path(directory) / 'users.json')):
                module = importlib.import_module('app')
                app = module.create_app()
                rules = {rule.rule for rule in app.url_map.iter_rules()}
                self.assertIn('/api/connection/status', rules)
                self.assertIn('/api/connection/toggle', rules)

    _real_run = staticmethod(connection._run)


if __name__ == '__main__':
    unittest.main()
