"""CP2B-specific invariants; never access real services, hardware or user stores."""

import importlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import call, patch

import auth
import config
from blueprints import rtu


ROOT = Path(__file__).resolve().parents[1]


class Cp2bTests(unittest.TestCase):
    def test_target_paths_and_interfaces(self):
        self.assertEqual(config.WORKING_DIR, '/home/admin/projects')
        self.assertEqual(config.ETH_CONNECTION_ID, 'ETH0')
        self.assertEqual(config.ETH1_CONNECTION_ID, 'ETH1')
        self.assertEqual(config.LTE_CONNECTION_ID, 'LTE')
        self.assertEqual(config.ETH_DEVICE_NAME, 'eth0')
        self.assertEqual(Path(config.WATCHDOG_SCRIPT).as_posix(),
                         '/home/admin/projects/modem/internet-watchdog.sh')
        helper = (ROOT / 'scripts/deployment_common.sh').read_text()
        self.assertIn('PROJECTS_DIR="/home/admin/projects"', helper)
        unit = (ROOT / 'amr_gateway_webui.service').read_text()
        self.assertIn('User=root', unit)
        self.assertIn('--worker-class gthread --threads 2', unit)

    def test_cleanup_preserves_cp2b_profiles(self):
        profiles = '\n'.join(f'{name}:802-3-ethernet' for name in (
            'ETH0', 'ETH1', 'LTE', 'Wired connection 1',
        ))
        with patch.object(rtu, '_run', side_effect=[
            (True, profiles, ''), (True, '', ''),
        ]) as run:
            rtu._cleanup_competing_profiles()
        self.assertEqual(run.call_args_list, [
            call('nmcli -t -f NAME,TYPE con show'),
            call('nmcli con delete "Wired connection 1"'),
        ])

    def test_binding_not_registered_or_rendered_and_roles_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(auth, 'USERS_FILE', str(Path(directory) / 'users.json')):
                module = importlib.import_module('app')
                app = module.create_app()
                app.config['TESTING'] = True
                client = app.test_client()
                rules = {rule.rule for rule in app.url_map.iter_rules()}
                self.assertFalse(any('binding' in rule for rule in rules))
                self.assertNotIn('/api/diagnostic/reboot-logs', rules)
                for role in ('admin', 'dec', 'amr'):
                    with self.subTest(role=role):
                        with client.session_transaction() as session:
                            session.clear()
                            session['_user_id'] = role
                            session['_fresh'] = True
                        response = client.get('/')
                        self.assertEqual(response.status_code, 200)
                        html = response.get_data(as_text=True)
                        self.assertNotIn('rtuBoundMac', html)
                        self.assertNotIn('rtuResetBinding', html)
                        self.assertNotIn('binding_csrf_token', html)
                        self.assertNotIn('diagRebootLogs', html)
                        self.assertIn('id="lteSimStatus"', html)
                        self.assertIn('id="lteIpAddress"', html)
                        self.assertEqual('id="connection-tab"' in html, role == 'admin')
                        self.assertIn(f'const CAN_MANAGE = {str(role != "amr").lower()}', html)
                        with client.session_transaction() as session:
                            self.assertNotIn('binding_csrf_token', session)
        source = (ROOT / 'static/js/app.js').read_text(encoding='utf-8')
        self.assertNotIn('loadRtuBinding', source)
        self.assertNotIn('/api/rtu/binding', source)
        for name in ('templates/lte.html', 'templates/rtu.html'):
            self.assertNotIn('admin-only', (ROOT / name).read_text())


if __name__ == '__main__':
    unittest.main()
