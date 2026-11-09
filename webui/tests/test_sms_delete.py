"""SMS Delete All tests never open real hardware or remove actual messages."""

import unittest
from pathlib import Path
from unittest.mock import call, patch

from flask import Flask, render_template
from flask_login import login_required

import auth
from blueprints import sms


class SmsDeleteTests(unittest.TestCase):
    def setUp(self):
        app = Flask(__name__, template_folder=str(Path(__file__).resolve().parents[1] / 'templates'))
        app.config.update(TESTING=True, SECRET_KEY='test-only')
        auth.login_manager.init_app(app)
        app.register_blueprint(auth.auth_bp)
        app.register_blueprint(sms.sms_bp)

        @app.route('/')
        @login_required
        def index():
            return render_template('sms.html')

        self.client = app.test_client()
        patcher = patch.object(auth, '_load_users', return_value={
            role: {'role': role} for role in ('admin', 'dec', 'amr')
        })
        patcher.start()
        self.addCleanup(patcher.stop)
        self.login('admin')

    def login(self, role):
        with self.client.session_transaction() as session:
            session.clear()
            if role:
                session['_user_id'] = role
                session['_fresh'] = True
            session['sms_csrf_token'] = 'test-csrf'

    def delete(self, confirm=True, token='test-csrf'):
        return self.client.post('/api/lte/sms/delete-all', json={'confirm': confirm},
                                headers={'X-CSRF-Token': token})

    def test_permission_matrix_runs_before_hardware_access(self):
        with patch.object(sms, '_modem_present', return_value=True) as present:
            with patch.object(sms, '_delete_all_messages', return_value={
                'present': True, 'storage': 'ME', 'used': 0, 'capacity': 100,
            }) as delete:
                for role in ('admin', 'dec', 'amr', None):
                    self.login(role)
                    response = self.delete()
                    self.assertEqual(response.status_code, 302 if role is None else (403 if role == 'amr' else 200))
                    self.assertEqual(response.headers['Cache-Control'], 'no-store')
                self.assertEqual(delete.call_count, 2)
                self.assertEqual(present.call_count, 2)

    def test_csrf_and_explicit_confirmation_reject_before_hardware(self):
        with patch.object(sms, '_modem_present') as present:
            for token in ('', 'wrong', '\u00e9'):
                self.assertEqual(self.delete(token=token).status_code, 403)
            for confirm in (False, None, 0, 1, 'true', [], {}):
                self.assertEqual(self.delete(confirm=confirm).status_code, 400)
            for body in ({}, [], None):
                response = self.client.post('/api/lte/sms/delete-all', json=body,
                                            headers={'X-CSRF-Token': 'test-csrf'})
                self.assertEqual(response.status_code, 400)
            with self.client.session_transaction() as session:
                session.pop('sms_csrf_token')
            self.assertEqual(self.delete().status_code, 403)
            present.assert_not_called()
        self.assertEqual(self.client.get('/api/lte/sms/delete-all').status_code, 405)

    def test_absent_modem_never_opens_serial(self):
        with patch.object(sms, '_modem_present', return_value=False):
            with patch.object(sms.serial, 'Serial') as port:
                response = self.delete()
                self.assertEqual(response.status_code, 503)
                self.assertFalse(response.get_json()['present'])
                port.assert_not_called()

    def test_fixed_delete_command_exclusive_session_and_post_delete_verification(self):
        with patch.object(sms.serial, 'Serial') as serial:
            with patch.object(sms, '_command', side_effect=[
                '+CPIN: READY\r\nOK\r\n',
                '+CPMS: "ME",5,100,"ME",5,100,"ME",5,100\r\nOK\r\n',
                'OK\r\n', '+CPMS: "ME",0,100,"ME",0,100,"ME",0,100\r\nOK\r\n',
            ]) as command:
                result = sms._delete_all_messages()
            port = serial.return_value.__enter__.return_value
            serial.assert_called_once_with(port=sms.LTE_SERIAL_PORT, baudrate=sms.LTE_SERIAL_BAUD,
                                           timeout=0.2, write_timeout=3, exclusive=True)
            self.assertEqual(command.call_args_list, [
                call(port, 'AT+CPIN?'), call(port, 'AT+CPMS?'),
                call(port, 'AT+CMGD=1,4', timeout=30), call(port, 'AT+CPMS?'),
            ])
            serial.return_value.__exit__.assert_called_once()
            self.assertEqual(result, {'present': True, 'storage': 'ME', 'used': 0, 'capacity': 100})

    def test_sim_or_storage_failure_before_delete_sends_no_destructive_command(self):
        for replies in (
            ['+CPIN: SIM PIN\r\nOK'],
            ['+CPIN: READY\r\nOK', 'malformed storage'],
            ['+CPIN: READY\r\nOK', '+CPMS: "ME",101,100\r\nOK'],
        ):
            with self.subTest(replies=replies):
                with patch.object(sms.serial, 'Serial'):
                    with patch.object(sms, '_command', side_effect=replies) as command:
                        with self.assertRaises(ValueError):
                            sms._delete_all_messages()
                        self.assertFalse(any(c.args[1].startswith('AT+CMGD') for c in command.call_args_list))

    def test_command_timeout_and_verification_failure_are_uncertain_and_close_port(self):
        for replies in (
            ['+CPIN: READY\r\nOK', '+CPMS: "SM",3,20\r\nOK', ValueError('timed out')],
            ['+CPIN: READY\r\nOK', '+CPMS: "SM",3,20\r\nOK', 'OK', ValueError('bad verification')],
            ['+CPIN: READY\r\nOK', '+CPMS: "SM",3,20\r\nOK', 'OK', sms.serial.SerialException('removed')],
            ['+CPIN: READY\r\nOK', '+CPMS: "SM",3,20\r\nOK', 'OK', '+CPMS: "ME",0,100\r\nOK'],
        ):
            with self.subTest(replies=replies):
                with patch.object(sms.serial, 'Serial') as serial:
                    with patch.object(sms, '_command', side_effect=replies):
                        with self.assertRaisesRegex(ValueError, 'uncertain'):
                            sms._delete_all_messages()
                    serial.return_value.__exit__.assert_called_once()

    def test_remaining_messages_are_not_reported_as_success(self):
        with patch.object(sms, '_modem_present', return_value=True):
            with patch.object(sms, '_delete_all_messages', return_value={
                'present': True, 'storage': 'SM', 'used': 1, 'capacity': 20,
            }):
                response = self.delete()
        self.assertEqual(response.status_code, 409)
        self.assertIn('messages remain', response.get_json()['error'])
        self.assertNotIn('message', response.get_json())

    def test_busy_and_uncertain_errors_do_not_claim_success(self):
        for error in (sms.serial.SerialException('busy'), ValueError('Delete result is uncertain')):
            with self.subTest(error=error):
                with patch.object(sms, '_modem_present', return_value=True):
                    with patch.object(sms, '_delete_all_messages', side_effect=error):
                        response = self.delete()
                self.assertEqual(response.status_code, 503)
                self.assertIn('error', response.get_json())
                self.assertNotIn('message', response.get_json())

    def test_deletion_does_not_change_modem_settings_or_use_client_commands(self):
        with patch.object(sms, '_modem_present', return_value=True):
            with patch.object(sms, '_delete_all_messages', return_value={
                'present': True, 'storage': 'ME', 'used': 0, 'capacity': 100,
            }) as delete:
                response = self.client.post('/api/lte/sms/delete-all', json={
                    'confirm': True, 'command': 'AT+CFUN=1,1', 'storage': 'SM',
                }, headers={'X-CSRF-Token': 'test-csrf'})
                self.assertEqual(response.status_code, 200)
                delete.assert_called_once_with()

    def test_template_columns_no_explainer_and_delete_controls_follow_roles(self):
        for role in ('admin', 'dec', 'amr'):
            self.login(role)
            with self.client.session_transaction() as session:
                session.pop('sms_csrf_token')
            response = self.client.get('/')
            self.assertEqual(response.status_code, 200)
            html = response.get_data(as_text=True)
            self.assertIn('15ch', html)
            self.assertIn('>DateTime</th>', html)
            self.assertNotIn('Received (SMS timestamp)', html)
            self.assertNotIn("Received messages in the modem's current SMS storage.", html)
            self.assertEqual('id="smsDeleteAllBtn"' in html, role != 'amr')
            with self.client.session_transaction() as session:
                self.assertEqual('sms_csrf_token' in session, role != 'amr')


if __name__ == '__main__':
    unittest.main()
