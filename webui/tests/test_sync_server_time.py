"""Sync Server Time marker tests; never change the clock or actual device files."""

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from flask import Flask, render_template
from flask_login import login_required

import auth
import config
from blueprints import amr


class SyncServerTimeTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.marker = self.root / 'config_file' / 'sync_server'
        for patcher in (
            patch.object(amr, 'SYNC_SERVER_FILE', str(self.marker)),
            patch.object(auth, '_load_users', return_value={
                role: {'role': role} for role in ('admin', 'dec', 'amr')
            }),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)
        app = Flask(__name__, template_folder=str(Path(__file__).resolve().parents[1] / 'templates'))
        app.config.update(TESTING=True, SECRET_KEY='test-only')
        auth.login_manager.init_app(app)
        app.register_blueprint(auth.auth_bp)
        app.register_blueprint(amr.amr_bp)

        @app.route('/')
        @login_required
        def index():
            return render_template('amr.html')

        self.client = app.test_client()
        self.login('admin')

    def login(self, role):
        with self.client.session_transaction() as session:
            session.clear()
            if role:
                session['_user_id'] = role
                session['_fresh'] = True
            session['sync_time_csrf_token'] = 'test-csrf'

    def save(self, enabled, token='test-csrf'):
        return self.client.post('/api/amr/sync-time', json={'enabled': enabled},
                                headers={'X-CSRF-Token': token})

    def test_config_uses_platform_config_directory_and_correct_marker_name(self):
        self.assertEqual(config.SYNC_SERVER_FILE, os.path.join(config.CONFIG_FILE_DIR, 'sync_server'))
        self.assertFalse(hasattr(config, 'SYNC_TIME_FILE'))

    def test_get_missing_is_uncached_readonly_and_does_not_create_directory(self):
        response = self.client.get('/api/amr/sync-time')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json(), {'enabled': False})
        self.assertEqual(response.headers['Cache-Control'], 'no-store')
        self.assertFalse(self.marker.parent.exists())

    def test_checked_creates_zero_byte_file_and_unchecked_removes_only_marker(self):
        for role in ('admin', 'dec'):
            with self.subTest(role=role):
                self.login(role)
                response = self.save(True)
                self.assertEqual(response.status_code, 200)
                self.assertTrue(response.get_json()['enabled'])
                self.assertEqual(self.marker.read_bytes(), b'')
                other = self.marker.parent / 'location.cfg'
                other.write_text('preserve')
                self.assertTrue(self.client.get('/api/amr/sync-time').get_json()['enabled'])
                self.assertEqual(self.save(True).status_code, 200)
                self.assertEqual(self.marker.read_bytes(), b'')
                self.assertEqual(self.save(False).status_code, 200)
                self.assertFalse(self.marker.exists())
                self.assertEqual(other.read_text(), 'preserve')
                self.assertFalse((self.marker.parent / 'sync_time').exists())

    def test_enabling_existing_marker_ensures_it_is_blank(self):
        self.marker.parent.mkdir()
        self.marker.write_text('old contents')
        self.assertEqual(self.save(True).status_code, 200)
        self.assertEqual(self.marker.read_bytes(), b'')

    def test_disabling_missing_marker_is_idempotent_and_does_not_create_parent(self):
        self.assertEqual(self.save(False).status_code, 200)
        self.assertEqual(self.save(False).status_code, 200)
        self.assertFalse(self.marker.parent.exists())

    def test_readonly_can_view_but_cannot_change_and_anonymous_is_rejected(self):
        self.login('amr')
        self.assertEqual(self.client.get('/api/amr/sync-time').status_code, 200)
        self.assertEqual(self.save(True).status_code, 403)
        self.assertEqual(self.save(False).status_code, 403)
        self.login(None)
        self.assertEqual(self.client.get('/api/amr/sync-time').status_code, 302)
        self.assertEqual(self.save(True).status_code, 302)
        self.assertFalse(self.marker.parent.exists())

    def test_csrf_missing_invalid_and_nonascii_rejected_before_filesystem_writes(self):
        for token in ('', 'bad', '\u00e9'):
            self.assertEqual(self.save(True, token).status_code, 403)
        with self.client.session_transaction() as session:
            session.pop('sync_time_csrf_token')
        self.assertEqual(self.save(True).status_code, 403)
        self.assertFalse(self.marker.parent.exists())

    def test_only_json_boolean_is_accepted(self):
        for value in ('true', 'false', '', None, 0, 1, [], {}):
            self.assertEqual(self.save(value).status_code, 400)
        for body in ({}, [], None):
            response = self.client.post('/api/amr/sync-time', json=body,
                                        headers={'X-CSRF-Token': 'test-csrf'})
            self.assertEqual(response.status_code, 400)
        self.assertFalse(self.marker.parent.exists())

    def test_directory_marker_is_reported_and_never_deleted(self):
        self.marker.mkdir(parents=True)
        self.assertEqual(self.client.get('/api/amr/sync-time').status_code, 409)
        self.assertEqual(self.save(True).status_code, 500)
        self.assertEqual(self.save(False).status_code, 500)
        self.assertTrue(self.marker.is_dir())

    def test_filesystem_failures_do_not_claim_success(self):
        with patch.object(amr.os, 'open', side_effect=PermissionError('test-only')):
            self.assertEqual(self.save(True).status_code, 500)
        self.marker.touch()
        with patch.object(amr.os, 'unlink', side_effect=PermissionError('test-only')):
            self.assertEqual(self.save(False).status_code, 500)
        self.assertTrue(self.marker.exists())

    def test_template_label_and_csrf_follow_operational_permissions(self):
        for role in ('admin', 'dec', 'amr'):
            with self.subTest(role=role):
                self.login(role)
                with self.client.session_transaction() as session:
                    session.pop('sync_time_csrf_token')
                response = self.client.get('/')
                self.assertEqual(response.status_code, 200)
                html = response.get_data(as_text=True)
                self.assertIn('for="syncServerTime">Sync Server Time</label>', html)
                self.assertIn('onchange="saveSyncServerTime()"', html)
                with self.client.session_transaction() as session:
                    self.assertEqual('sync_time_csrf_token' in session, role != 'amr')


if __name__ == '__main__':
    unittest.main()
