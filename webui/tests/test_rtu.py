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
        for value in ('Building A', '', 'Zone=A', 'location=Building A', 'Building "A"'):
            with self.subTest(value=value):
                response = self.client.post('/api/rtu/location', json={'location': value})
                self.assertEqual(response.status_code, 200)
                self.assertEqual(self.location_file.read_text(), f'location="{value}"\n')
                response = self.client.get('/api/rtu/config')
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.get_json()['location'], value)

    def test_save_trims_surrounding_whitespace(self):
        response = self.client.post('/api/rtu/location', json={'location': '  Building A  '})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.location_file.read_text(), 'location="Building A"\n')

    def test_load_location_formats(self):
        self.location_file.parent.mkdir()
        for content, expected in (
            ('location = "DEC"\n', 'DEC'),
            ('location ="DEC"\n', 'DEC'),
            ('location= "DEC"\n', 'DEC'),
            ('  location \t= \t"DEC"  \n', 'DEC'),
            ('location = DEC\n', 'DEC'),
            ('location = ""\n', ''),
            ('location = "Zone=A"\n', 'Zone=A'),
            ('location="Building A"\n', 'Building A'),
            ('location=""\n', ''),
            ('location="Building "A""\n', 'Building "A"'),
            ('location=Building A\n', 'Building A'),
            ('location=Building "A"\n', 'Building "A"'),
            ('location="Building A\n', '"Building A'),
        ):
            with self.subTest(content=content):
                self.location_file.write_text(content)
                response = self.client.get('/api/rtu/config')
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.get_json()['location'], expected)
                self.assertEqual(self.location_file.read_text(), content)

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

    def test_server_ip_save_and_reload_preserves_location(self):
        self.location_file.parent.mkdir()
        self.location_file.write_text('location="Building A"\n')
        before = self.location_file.read_bytes()
        response = self.client.post('/api/rtu/server', json={'server_ip': '  10.2.3.4  '})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.server_file.read_text(), '10.2.3.4\n')
        data = self.client.get('/api/rtu/config').get_json()
        self.assertEqual(data['server_ip'], '10.2.3.4')
        self.assertEqual(data['location'], 'Building A')
        self.assertEqual(self.location_file.read_bytes(), before)

    def test_blank_server_ip_does_not_overwrite_saved_server(self):
        before = self.server_file.read_bytes()
        for body in ({}, {'server_ip': ''}, {'server_ip': '   '}):
            response = self.client.post('/api/rtu/server', json=body)
            self.assertEqual(response.status_code, 400)
            self.assertEqual(self.server_file.read_bytes(), before)

    def test_repeated_save_does_not_duplicate_prefix(self):
        self.client.post('/api/rtu/location', json={'location': 'Building A'})
        value = self.client.get('/api/rtu/config').get_json()['location']
        response = self.client.post('/api/rtu/location', json={'location': value})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.location_file.read_text(), 'location="Building A"\n')


if __name__ == '__main__':
    unittest.main()
