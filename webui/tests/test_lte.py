"""Modem absence and boot-time response tests with no hardware access."""

import unittest
from unittest.mock import patch

from flask import Flask

from auth import auth_bp, login_manager
from blueprints import diagnostic, lte


class LteStatusTests(unittest.TestCase):
    def setUp(self):
        app = Flask(__name__)
        app.config.update(TESTING=True, SECRET_KEY='test-only')
        login_manager.init_app(app)
        app.register_blueprint(auth_bp)
        app.register_blueprint(lte.lte_bp)
        app.register_blueprint(diagnostic.diagnostic_bp)
        self.client = app.test_client()
        patcher = patch('auth._load_users', return_value={'admin': {'role': 'admin'}})
        patcher.start()
        self.addCleanup(patcher.stop)
        with self.client.session_transaction() as session:
            session['_user_id'] = 'admin'
            session['_fresh'] = True

    def test_missing_modem_returns_immediately_without_serial_or_at_queries(self):
        with patch.object(lte.os.path, 'exists', return_value=False) as exists:
            with patch.object(lte, '_send_at') as send, patch.object(lte.serial, 'Serial') as serial:
                response = self.client.get('/api/lte/status')
                self.assertEqual(response.status_code, 200)
                data = response.get_json()
                self.assertEqual(data['status'], 'Not installed')
                self.assertFalse(data['present'])
                self.assertFalse(data['available'])
                self.assertEqual(data['carrier'], '')
                exists.assert_called_once_with(lte.LTE_SERIAL_PORT)
                send.assert_not_called()
                serial.assert_not_called()

    def test_missing_modem_never_opens_serial_for_diagnostics(self):
        with patch.object(lte, '_modem_present', return_value=False):
            with patch.object(lte.serial, 'Serial') as serial:
                self.assertIn('not present', lte._send_at('ATI'))
                serial.assert_not_called()

    def test_unresponsive_or_erroring_modem_stops_after_first_query(self):
        with patch.object(lte, '_modem_present', return_value=True):
            for reply in ('', 'ERROR: port busy', 'partial response'):
                with self.subTest(reply=reply), patch.object(lte, '_send_at', return_value=reply) as send:
                    data = self.client.get('/api/lte/status').get_json()
                    self.assertEqual(data['status'], 'Unavailable')
                    self.assertTrue(data['present'])
                    self.assertFalse(data['available'])
                    send.assert_called_once_with('AT+CPIN?')

    def test_modem_removed_during_query_is_reported_not_installed(self):
        with patch.object(lte, '_modem_present', side_effect=[True, False]):
            with patch.object(lte, '_send_at', return_value='ERROR: disconnected'):
                data = self.client.get('/api/lte/status').get_json()
                self.assertEqual(data['status'], 'Not installed')

    def test_present_modem_preserves_status_fields(self):
        with patch.object(lte, '_modem_present', return_value=True):
            with patch.object(lte, '_send_at', side_effect=[
                '+CPIN: READY\r\nOK',
                '+CGPADDR: 1,"10.1.2.3"\r\nOK',
                '+QPING: 0,"8.8.8.8",32,100,255\r\nOK',
                '+QSPN: "Carrier","Carrier",0\r\nOK',
                '+QNWINFO: "FDD LTE","00101","LTE BAND 3",1300\r\nOK',
                '+CSQ: 25,99\r\nOK',
                '+CNUM: "","+1234567890",145\r\nOK',
            ]) as send:
                data = self.client.get('/api/lte/status').get_json()
                self.assertEqual(data, {
                    'present': True, 'available': True, 'status': 'Connected',
                    'carrier': 'Carrier', 'access': 'FDD LTE',
                    'band': 'LTE BAND 3', 'signal': '25',
                    'sim_status': 'Inserted', 'ip_address': '10.1.2.3',
                    'mobile_number': '+1234567890',
                })
                self.assertEqual(send.call_count, 7)
                send.assert_any_call('AT+CNUM')
                send.assert_any_call('AT+QPING=1,"8.8.8.8",4,1', timeout=10)

    def test_locked_sim_retains_sim_status_and_skips_internet_checks(self):
        with patch.object(lte, '_modem_present', return_value=True):
            with patch.object(lte, '_send_at', return_value='+CPIN: SIM PIN\r\nOK') as send:
                data = self.client.get('/api/lte/status').get_json()
        self.assertEqual(data['sim_status'], 'SIM PIN')
        self.assertTrue(data['present'])
        self.assertTrue(data['available'])
        self.assertEqual(data['ip_address'], '--')
        send.assert_called_once_with('AT+CPIN?')

    def test_qping_failure_reports_disconnected_and_uses_saved_target(self):
        with patch.object(lte, '_modem_present', return_value=True):
            with patch.object(lte, '_get_watchdog_target', return_value='example.invalid'):
                with patch.object(lte, '_send_at', side_effect=[
                    '+CPIN: READY\r\nOK', '+CGPADDR: 1,"10.1.2.3"\r\nOK',
                    '+QPING: 569\r\nOK', 'OK', 'OK', 'OK', 'OK',
                ]) as send:
                    data = self.client.get('/api/lte/status').get_json()
        self.assertEqual(data['status'], 'Disconnected')
        self.assertEqual(data['ip_address'], '10.1.2.3')
        send.assert_any_call('AT+QPING=1,"example.invalid",4,1', timeout=10)

    def test_presence_endpoint_does_not_open_or_query_modem(self):
        for present in (False, True):
            with self.subTest(present=present):
                with patch.object(lte, '_modem_present', return_value=present):
                    with patch.object(lte, '_send_at') as send, patch.object(lte.serial, 'Serial') as serial:
                        response = self.client.get('/api/lte/presence')
                        self.assertEqual(response.status_code, 200)
                        self.assertEqual(response.get_json(), {'present': present})
                        self.assertEqual(response.headers['Cache-Control'], 'no-store')
                        send.assert_not_called()
                        serial.assert_not_called()

    def test_diagnostic_rejects_absent_modem_without_commands(self):
        with patch.object(lte, '_modem_present', return_value=False):
            with patch.object(lte, '_send_at') as send:
                response = self.client.post('/api/diagnostic/lte-check', json={})
                self.assertEqual(response.status_code, 503)
                self.assertFalse(response.get_json()['present'])
                self.assertIn('not installed', response.get_json()['error'])
                send.assert_not_called()

    def test_diagnostic_runs_when_modem_is_present(self):
        with patch.object(lte, '_modem_present', return_value=True):
            with patch.object(lte, '_send_at', return_value='OK') as send:
                response = self.client.post('/api/diagnostic/lte-check', json={})
                self.assertEqual(response.status_code, 200)
                self.assertTrue(response.get_json()['success'])
                self.assertEqual(send.call_count, 6)

    def test_presence_and_diagnostic_require_login(self):
        with self.client.session_transaction() as session:
            session.clear()
        with patch.object(lte, '_modem_present') as present:
            self.assertEqual(self.client.get('/api/lte/presence').status_code, 302)
            self.assertEqual(self.client.post('/api/diagnostic/lte-check', json={}).status_code, 302)
            present.assert_not_called()

    def test_missing_modem_reboot_is_rejected_without_at_commands(self):
        with patch.object(lte, '_modem_present', return_value=False):
            with patch.object(lte, '_send_at') as send:
                self.assertEqual(self.client.post('/api/lte/reboot', json={}).status_code, 503)
                send.assert_not_called()


if __name__ == '__main__':
    unittest.main()
