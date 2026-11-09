"""Admin password-reset tests using only temporary user stores."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from flask import Flask, render_template
from flask_login import login_required
from werkzeug.security import check_password_hash, generate_password_hash

import auth
from blueprints import amr, connection, diagnostic, rtu, sms


class PasswordResetTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name) / 'users.json'
        password_hash = generate_password_hash('old-test-password')
        self.original = {
            'secret_key': 'test-only-secret', 'metadata': {'preserve': True},
            'users': {name: {'role': name, 'password_hash': password_hash}
                      for name in ('admin', 'dec', 'amr', 'custom')},
        }
        self.path.write_text(json.dumps(self.original))
        patcher = patch.object(auth, 'USERS_FILE', str(self.path))
        patcher.start()
        self.addCleanup(patcher.stop)
        app = Flask(__name__, template_folder=str(Path(__file__).resolve().parents[1] / 'templates'))
        app.config.update(TESTING=True, SECRET_KEY='test-only')
        auth.login_manager.init_app(app)
        for blueprint in (auth.auth_bp, amr.amr_bp, connection.connection_bp,
                          diagnostic.diagnostic_bp, rtu.rtu_bp, sms.sms_bp):
            app.register_blueprint(blueprint)

        @app.route('/')
        @login_required
        def index():
            return render_template('base.html')

        self.client = app.test_client()
        self.sign_in('admin')

    def sign_in(self, username):
        with self.client.session_transaction() as session:
            session.clear()
            if username:
                session['_user_id'] = username
                session['_fresh'] = True
            session['password_reset_csrf_token'] = 'test-csrf'

    def reset(self, username='dec', token='test-csrf'):
        return self.client.post('/api/auth/reset-password', json={'username': username},
                                headers={'X-CSRF-Token': token})

    def test_admin_resets_only_selected_hash_preserving_everything_else(self):
        for username in ('dec', 'amr'):
            with self.subTest(username=username):
                self.path.write_text(json.dumps(self.original))
                response = self.reset(username)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.headers['Cache-Control'], 'no-store')
                updated = json.loads(self.path.read_text())
                new_hash = updated['users'][username]['password_hash']
                self.assertNotEqual(new_hash, '1234')
                self.assertTrue(check_password_hash(new_hash, '1234'))
                self.assertFalse(check_password_hash(new_hash, 'old-test-password'))
                updated['users'][username]['password_hash'] = self.original['users'][username]['password_hash']
                self.assertEqual(updated, self.original)
                with self.client.session_transaction() as session:
                    self.assertEqual(session['_user_id'], 'admin')

    def test_nonadmins_and_anonymous_cannot_reset_any_account(self):
        before = self.path.read_bytes()
        for username in ('dec', 'amr', 'custom', None):
            self.sign_in(username)
            for target in ('dec', 'amr'):
                with self.subTest(username=username, target=target):
                    self.assertEqual(self.reset(target).status_code, 403 if username else 302)
        self.assertEqual(self.path.read_bytes(), before)

    def test_csrf_missing_wrong_and_nonascii_never_change_store(self):
        before = self.path.read_bytes()
        for token in ('', 'wrong', '\u00e9'):
            self.assertEqual(self.reset(token=token).status_code, 403)
        with self.client.session_transaction() as session:
            session.pop('password_reset_csrf_token')
        self.assertEqual(self.reset().status_code, 403)
        self.assertEqual(self.path.read_bytes(), before)

    def test_only_fixed_existing_usernames_and_json_objects_accepted(self):
        before = self.path.read_bytes()
        for username in ('admin', 'custom', '', 'DEC', '../admin', None, [], {}, 1, True):
            with self.subTest(username=username):
                self.assertEqual(self.reset(username).status_code, 400)
        for body in ({}, [], None, 'dec'):
            response = self.client.post('/api/auth/reset-password', json=body,
                                        headers={'X-CSRF-Token': 'test-csrf'})
            self.assertEqual(response.status_code, 400)
        response = self.client.post('/api/auth/reset-password', data='{"username":',
                                    content_type='application/json',
                                    headers={'X-CSRF-Token': 'test-csrf'})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.client.get('/api/auth/reset-password').status_code, 405)
        self.assertEqual(self.path.read_bytes(), before)

    def test_missing_target_is_not_created(self):
        data = json.loads(self.path.read_text())
        del data['users']['amr']
        self.path.write_text(json.dumps(data))
        before = self.path.read_bytes()
        self.assertEqual(self.reset('amr').status_code, 404)
        self.assertEqual(self.path.read_bytes(), before)

    def test_client_cannot_choose_replacement_password(self):
        response = self.client.post('/api/auth/reset-password',
                                    json={'username': 'amr', 'new_password': 'not-the-default'},
                                    headers={'X-CSRF-Token': 'test-csrf'})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(check_password_hash(auth._load_users()['amr']['password_hash'], '1234'))

    def test_failed_atomic_replace_keeps_original_and_removes_temporary_file(self):
        before = self.path.read_bytes()
        with patch.object(auth.os, 'replace', side_effect=PermissionError('test-only')):
            response = self.reset()
        self.assertEqual(response.status_code, 500)
        self.assertIn('Unable to save', response.get_json()['error'])
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(list(self.path.parent.iterdir()), [self.path])

    def test_reset_password_allows_login_and_subsequent_own_password_change(self):
        for username in ('dec', 'amr'):
            with self.subTest(username=username):
                self.sign_in('admin')
                self.assertEqual(self.reset(username).status_code, 200)
                user_client = self.client.application.test_client()
                response = user_client.post('/login', data={'username': username, 'password': '1234'})
                self.assertEqual(response.status_code, 302)
                with user_client.session_transaction() as session:
                    self.assertEqual(session['_user_id'], username)
                response = user_client.post('/api/auth/change-password', json={
                    'old_password': '1234', 'new_password': 'new-test-password',
                })
                self.assertEqual(response.status_code, 200)
                auth.init_users()
                self.assertTrue(check_password_hash(auth._load_users()[username]['password_hash'],
                                                    'new-test-password'))

    def test_only_admin_receives_controls_script_and_session_csrf_token(self):
        for username in ('admin', 'dec', 'amr'):
            with self.subTest(username=username):
                self.sign_in(username)
                with self.client.session_transaction() as session:
                    session.pop('password_reset_csrf_token')
                response = self.client.get('/')
                self.assertEqual(response.status_code, 200)
                html = response.get_data(as_text=True)
                for marker in ('id="resetUserPasswordMenu"', 'id="resetUserPasswordModal"',
                               'id="resetUserPasswordBtn"', 'js/password_reset.js'):
                    self.assertEqual(marker in html, username == 'admin')
                with self.client.session_transaction() as session:
                    self.assertEqual('password_reset_csrf_token' in session, username == 'admin')
                    if username == 'admin':
                        self.assertIn(f'data-csrf-token="{session["password_reset_csrf_token"]}"', html)


if __name__ == '__main__':
    unittest.main()
