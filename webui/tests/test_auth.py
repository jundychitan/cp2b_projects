"""Account initialization and upgrade regression tests."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from flask import Flask
from werkzeug.security import check_password_hash, generate_password_hash

import auth


class UserInitializationTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / 'users.json'
        patcher = patch.object(auth, 'USERS_FILE', str(self.path))
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_fresh_install_creates_management_dec_with_hashed_password(self):
        auth.init_users()
        data = json.loads(self.path.read_text())
        self.assertEqual(set(data['users']), {'admin', 'amr', 'dec'})
        dec = data['users']['dec']
        self.assertEqual(dec['role'], 'dec')
        self.assertNotEqual(dec['password_hash'], '1234')
        self.assertTrue(check_password_hash(dec['password_hash'], '1234'))
        self.assertFalse(auth.load_user('dec').is_admin)
        self.assertTrue(auth.load_user('dec').can_manage)

    def test_existing_dec_role_is_migrated_without_changing_credentials(self):
        for role in ('amr', 'admin'):
            with self.subTest(role=role):
                original = {
                    'secret_key': 'test-only',
                    'users': {'dec': {'role': role, 'password_hash': 'preserve-hash'}},
                }
                self.path.write_text(json.dumps(original))
                auth.init_users()
                updated = json.loads(self.path.read_text())
                self.assertEqual(updated['secret_key'], original['secret_key'])
                self.assertEqual(updated['users']['dec'],
                                 {'role': 'dec', 'password_hash': 'preserve-hash'})

    def test_permission_matrix(self):
        for role, manage, admin in (
            ('admin', True, True), ('dec', True, False),
            ('amr', False, False), ('unknown', False, False),
        ):
            with self.subTest(role=role):
                user = auth.User(role, role)
                self.assertEqual(user.can_manage, manage)
                self.assertEqual(user.is_admin, admin)

    def test_upgrade_preserves_existing_accounts_and_secret(self):
        original = {
            'secret_key': 'test-only',
            'metadata': 'preserve',
            'users': {'admin': {'password_hash': 'unchanged', 'role': 'admin'}},
        }
        self.path.write_text(json.dumps(original))
        auth.init_users()
        updated = json.loads(self.path.read_text())
        self.assertEqual(updated['secret_key'], original['secret_key'])
        self.assertEqual(updated['metadata'], 'preserve')
        self.assertEqual(updated['users']['admin'], original['users']['admin'])
        self.assertTrue(check_password_hash(updated['users']['dec']['password_hash'], '1234'))

    def test_repeated_startup_preserves_changed_dec_password(self):
        auth.init_users()
        users = auth._load_users()
        users['dec']['password_hash'] = generate_password_hash('changed-test-password')
        auth._save_users(users)
        before = self.path.read_bytes()
        auth.init_users()
        self.assertEqual(self.path.read_bytes(), before)

    def test_dec_can_login_and_change_own_password(self):
        auth.init_users()
        app = Flask(__name__)
        app.config.update(TESTING=True, SECRET_KEY='test-only')
        auth.login_manager.init_app(app)
        app.register_blueprint(auth.auth_bp)
        app.add_url_rule('/', endpoint='index', view_func=lambda: 'home')
        client = app.test_client()
        self.assertEqual(client.post('/login', data={
            'username': 'dec', 'password': '1234',
        }).status_code, 302)
        response = client.post('/api/auth/change-password', json={
            'old_password': '1234', 'new_password': 'new-test-password',
        })
        self.assertEqual(response.status_code, 200)
        auth.init_users()
        self.assertTrue(check_password_hash(
            auth._load_users()['dec']['password_hash'], 'new-test-password',
        ))


if __name__ == '__main__':
    unittest.main()
