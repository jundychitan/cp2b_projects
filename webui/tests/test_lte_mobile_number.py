"""SIM number status checks: mocked AT commands, no real modem access."""

import unittest
from unittest.mock import patch

from flask import Flask

from auth import auth_bp, login_manager
from blueprints import lte


class SubscriberNumberTests(unittest.TestCase):
    def test_cnum_formats(self):
        for response, expected in [
            ('+CNUM: "","+1234567890",145\r\nOK', '+1234567890'),
            ('AT+CNUM\r\n+CNUM: "Voice, primary","1234567890",145,,4\r\nOK', '+1234567890'),
            ('+CNUM: ,"0012345",129\r\nOK', '0012345'),
            ('+CNUM: "","",129\r\n+CNUM: "Voice","012345",129\r\nOK', '012345'),
            ('+CNUM: "", "123456", 145\r\nOK', '+123456'),
            ('OK', ''), ('', ''), ('ERROR', ''), ('+CME ERROR: 10', ''),
            ('+CNUM: "","12345",129', ''),
            ('+CNUM: "","12345",129\r\nERROR', ''),
            ('+CNUM: "","12345",129\r\n+CME ERROR: 10\r\nOK', ''),
            ('+CNUM: "","",129\r\nOK', ''),
            ('+CNUM: "","unknown",129\r\nOK', ''),
            ('+CNUM: "","<script>",129\r\nOK', ''),
            ('+CNUM: "label"\r\nOK', ''),
            ('+CNUM: "unterminated\r\nOK', ''),
            ('+CNUM: "1234567890","",129\r\nOK', ''),
        ]:
            with self.subTest(response=response):
                self.assertEqual(lte._parse_subscriber_number(response), expected)

    def setUp(self):
        app = Flask(__name__)
        app.config.update(TESTING=True, SECRET_KEY='test-only')
        login_manager.init_app(app)
        app.register_blueprint(auth_bp)
        app.register_blueprint(lte.lte_bp)
        self.client = app.test_client()
        patcher = patch('auth._load_users', return_value={
            role: {'role': role} for role in ('admin', 'dec', 'amr')
        })
        patcher.start()
        self.addCleanup(patcher.stop)
        self.login('admin')

    def login(self, role):
        with self.client.session_transaction() as session:
            session['_user_id'] = role
            session['_fresh'] = True

    def status_with_number(self, cnum):
        replies = {
            'AT+CPIN?': '+CPIN: READY\r\nOK',
            'AT+CGPADDR=1': '+CGPADDR: 1,"10.1.2.3"\r\nOK',
            'AT+QSPN': '+QSPN: "Carrier","Carrier",0\r\nOK',
            'AT+QNWINFO': '+QNWINFO: "FDD LTE","00101","LTE BAND 3",1300\r\nOK',
            'AT+CSQ': '+CSQ: 25,99\r\nOK',
            'AT+CNUM': cnum,
        }
        with patch.object(lte, '_modem_present', return_value=True), \
                patch.object(lte, '_get_watchdog_target', return_value='8.8.8.8'), \
                patch.object(lte, '_send_at', side_effect=lambda cmd, **kw: replies.get(
                    cmd, '+QPING: 0,"8.8.8.8",32,100,255\r\nOK')) as send:
            response = self.client.get('/api/lte/status')
        self.assertEqual(response.status_code, 200)
        send.assert_any_call('AT+CNUM')
        self.assertEqual(send.call_count, 7)
        return response.get_json()

    def test_number_available_to_all_signed_in_roles(self):
        for role in ('admin', 'dec', 'amr'):
            with self.subTest(role=role):
                self.login(role)
                data = self.status_with_number('+CNUM: "","1234567890",145\r\nOK')
                self.assertEqual(data['mobile_number'], '+1234567890')
                self.assertEqual(data['sim_status'], 'Inserted')

    def test_unknown_or_unsupported_number_does_not_break_status(self):
        for reply in ('OK', 'ERROR', '+CME ERROR: 4', '', 'partial reply'):
            with self.subTest(reply=reply):
                data = self.status_with_number(reply)
                self.assertEqual(data['mobile_number'], '')
                self.assertEqual(data['status'], 'Connected')
                self.assertEqual(data['ip_address'], '10.1.2.3')
                self.assertEqual(data['carrier'], 'Carrier')

    def test_nonready_sim_skips_number_query(self):
        for reply in ('+CPIN: SIM PIN\r\nOK', '+CPIN: SIM PUK\r\nOK',
                      '+CPIN: NOT INSERTED\r\nOK', '+CME ERROR: 10'):
            with self.subTest(reply=reply):
                with patch.object(lte, '_modem_present', return_value=True), \
                        patch.object(lte, '_send_at', return_value=reply) as send:
                    data = self.client.get('/api/lte/status').get_json()
                self.assertEqual(data['mobile_number'], '')
                send.assert_called_once_with('AT+CPIN?')

    def test_absent_modem_skips_number_query(self):
        with patch.object(lte, '_modem_present', return_value=False), \
                patch.object(lte, '_send_at') as send:
            data = self.client.get('/api/lte/status').get_json()
        self.assertEqual(data['mobile_number'], '')
        send.assert_not_called()

    def test_status_requires_login(self):
        with self.client.session_transaction() as session:
            session.clear()
        with patch.object(lte, '_send_at') as send:
            self.assertEqual(self.client.get('/api/lte/status').status_code, 302)
            send.assert_not_called()


if __name__ == '__main__':
    unittest.main()
