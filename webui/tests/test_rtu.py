"""Regression tests for RTU location persistence."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from flask import Flask

from auth import User, login_manager
from blueprints import rtu


class LocationTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.location_file = Path(self.temp_dir.name) / 'config' / 'location.cfg'
        self.server_file = Path(self.temp_dir.name) / 'server_ip.txt'
        self.server_file.write_text('10.0.0.1\n')

        for name, value in (
            ('LOCATION_FILE', str(self.location_file)),
            ('SERVER_IP_FILE', str(self.server_file)),
        ):
            patcher = patch.object(rtu, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

        commands = patch.object(rtu, '_run', return_value=(False, '', ''))
        commands.start()
        self.addCleanup(commands.stop)

        app = Flask(__name__)
        app.config.update(TESTING=True, SECRET_KEY='test-only')
        login_manager.init_app(app)
        app.register_blueprint(rtu.rtu_bp)
        self.client = app.test_client()

        users = patch('auth._load_users', return_value={'admin': {'role': 'admin'}})
        users.start()
        self.addCleanup(users.stop)
        with self.client.session_transaction() as session:
            session['_user_id'] = User('admin', 'admin').get_id()
            session['_fresh'] = True

    def test_save_and_reload_location(self):
        for value in ('Building A', '', 'Zone=A', 'location=Building A'):
            with self.subTest(value=value):
                response = self.client.post('/api/rtu/location', json={'location': value})
                self.assertEqual(response.status_code, 200)
                self.assertEqual(self.location_file.read_text(), f'location={value}\n')
                response = self.client.get('/api/rtu/config')
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.get_json()['location'], value)

    def test_save_trims_surrounding_whitespace(self):
        response = self.client.post('/api/rtu/location', json={'location': '  Building A  '})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.location_file.read_text(), 'location=Building A\n')

    def test_load_legacy_location(self):
        self.location_file.parent.mkdir()
        self.location_file.write_text('Building A\n')
        response = self.client.get('/api/rtu/config')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()['location'], 'Building A')
        self.assertEqual(response.get_json()['server_ip'], '10.0.0.1')

    def test_load_missing_location(self):
        response = self.client.get('/api/rtu/config')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()['location'], '')

    def test_repeated_save_does_not_duplicate_prefix(self):
        self.client.post('/api/rtu/location', json={'location': 'Building A'})
        value = self.client.get('/api/rtu/config').get_json()['location']
        response = self.client.post('/api/rtu/location', json={'location': value})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.location_file.read_text(), 'location=Building A\n')


if __name__ == '__main__':
    unittest.main()
