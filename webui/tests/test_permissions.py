"""Exercise operational routes with isolated files and mocked device commands."""

import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from flask import Flask

from auth import auth_bp, login_manager
from blueprints import amr, connection, diagnostic, lte, rtu


class ManagementPermissionsTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        app = Flask(__name__)
        app.config.update(TESTING=True, SECRET_KEY='test-only')
        login_manager.init_app(app)
        for blueprint in (auth_bp, amr.amr_bp, connection.connection_bp,
                          diagnostic.diagnostic_bp, lte.lte_bp, rtu.rtu_bp):
            app.register_blueprint(blueprint)
        self.client = app.test_client()
        patches = [
            patch('auth._load_users', return_value={
                name: {'role': name} for name in ('admin', 'dec', 'amr')
            }),
            patch.object(lte, '_send_at', return_value='OK'),
            patch.object(lte, '_modem_present', return_value=True),
            patch.object(amr.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, '', '')),
        ]
        for module in (amr, connection, diagnostic, lte, rtu):
            patches.append(patch.object(module, '_run', return_value=(True, '', '')))
        for module, name, value in (
            (amr, 'AMR_METER_DIR', self.root),
            (amr, 'AMR_UPLOAD_DIR', self.root),
            (amr, 'METER_BATCH_FILE', self.root / 'meter_batch.csv'),
            (rtu, 'SERVER_IP_FILE', self.root / 'server_ip.txt'),
            (rtu, 'LOCATION_FILE', self.root / 'location.cfg'),
            (lte, 'WATCHDOG_TARGETS_FILE', self.root / 'targets.cfg'),
            (lte, 'WATCHDOG_SCRIPT', self.root / 'missing-watchdog.sh'),
        ):
            patches.append(patch.object(module, name, str(value)))
        for patcher in patches:
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_operational_route_access_matrix(self):
        operations = [
            ('POST', '/api/lte/reboot', {}),
            ('POST', '/api/lte/watchdog', {'targets': ['8.8.8.8']}),
            ('POST', '/api/rtu/network', {'method': 'auto'}),
            ('POST', '/api/rtu/server', {'server_ip': '10.0.0.1'}),
            ('POST', '/api/rtu/location', {'location': 'Test'}),
            ('POST', '/api/amr/autoread', {'enable': True, 'interval': 30}),
            ('POST', '/api/amr/meters', {'meters': []}),
            ('POST', '/api/amr/configs', {'filename': 'test.cfg', 'content': 'test'}),
            ('POST', '/api/amr/configs/test.cfg', {'content': 'updated'}),
            ('DELETE', '/api/amr/configs/test.cfg', {}),
            ('POST', '/api/diagnostic/reboot', {}),
        ]
        for role in ('admin', 'dec', 'amr', None):
            with self.client.session_transaction() as session:
                session.clear()
                if role:
                    session['_user_id'] = role
                    session['_fresh'] = True
                    session['reboot_csrf_token'] = 'test-csrf'
            for method, url, body in operations:
                with self.subTest(role=role, url=url, method=method):
                    response = self.client.open(url, method=method, json=body,
                                                headers={'X-CSRF-Token': 'test-csrf'})
                    expected = 302 if role is None else (403 if role == 'amr' else 200)
                    self.assertEqual(response.status_code, expected, response.get_data(as_text=True))


if __name__ == '__main__':
    unittest.main()
