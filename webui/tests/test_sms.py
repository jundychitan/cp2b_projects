"""SMS regressions use mocked serial only; no device or user-store writes."""

import unittest
from unittest.mock import MagicMock, patch

from flask import Flask

from auth import auth_bp, login_manager
from blueprints import sms


def make_pdu(payload, length, dcs=8, header=False):
    # +123456; 26/11/09,10:20:30+32 (timezone is in quarter-hours).
    prefix = bytes.fromhex('0040069121436500' if header else '0000069121436500')
    return (prefix + bytes([dcs]) + bytes.fromhex('62119001020323')
            + bytes([length]) + payload).hex().upper()


def incoming(index=1, status='REC UNREAD', text='Hello\nOK\nERROR <script>你好'):
    payload = text.encode('utf-16-be')
    pdu = make_pdu(payload, len(payload))
    return f'+CMGL: {index},{0 if status == "REC UNREAD" else 1},,{len(bytes.fromhex(pdu)) - 1}\r\n{pdu}\r\n'


class SmsTests(unittest.TestCase):
    def setUp(self):
        app = Flask(__name__)
        app.config.update(TESTING=True, SECRET_KEY='test-only')
        login_manager.init_app(app)
        app.register_blueprint(auth_bp)
        app.register_blueprint(sms.sms_bp)
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

    def test_login_required_without_hardware_access(self):
        with self.client.session_transaction() as session:
            session.clear()
        with patch.object(sms, '_modem_present') as present:
            response = self.client.get('/api/lte/sms')
            self.assertEqual(response.status_code, 302)
            present.assert_not_called()
            self.assertEqual(response.headers['Cache-Control'], 'no-store')

    def test_all_signed_in_roles_can_read(self):
        for role in ('admin', 'dec', 'amr'):
            with self.subTest(role=role):
                self.login(role)
                with patch.object(sms, '_modem_present', return_value=True), \
                        patch.object(sms, '_read_messages', return_value={'messages': []}):
                    response = self.client.get('/api/lte/sms')
                    self.assertEqual(response.status_code, 200)
                    self.assertEqual(response.headers['Cache-Control'], 'no-store')

    def test_no_modem_does_not_open_port(self):
        with patch.object(sms, '_modem_present', return_value=False), \
                patch.object(sms.serial, 'Serial') as port:
            response = self.client.get('/api/lte/sms')
            self.assertEqual(response.status_code, 503)
            self.assertFalse(response.get_json()['present'])
            port.assert_not_called()

    def test_busy_and_command_errors_are_not_empty_inboxes(self):
        for error in (sms.serial.SerialException('busy'), ValueError('SIM requires PIN')):
            with self.subTest(error=error):
                with patch.object(sms, '_modem_present', return_value=True), \
                        patch.object(sms, '_read_messages', side_effect=error):
                    response = self.client.get('/api/lte/sms')
                    self.assertEqual(response.status_code, 503)
                    self.assertIn('error', response.get_json())
                    self.assertNotIn('messages', response.get_json())

    def test_unicode_newlines_and_timestamp_comma(self):
        messages = sms._parse_messages('\r\n' + incoming() + incoming(2, 'REC READ', '1234') + '\r\nOK\r\n')
        self.assertEqual(len(messages), 2)
        self.assertEqual(messages[0]['sender'], '+123456')
        self.assertEqual(messages[0]['timestamp'], '26/11/09,10:20:30+32')
        self.assertEqual(messages[0]['text'], 'Hello\nOK\nERROR <script>你好')
        self.assertEqual(messages[1]['text'], '1234')
        self.assertEqual(messages[1]['status'], 'REC READ')

    def test_empty_sent_and_malformed_messages(self):
        self.assertEqual(sms._parse_messages('\r\nOK\r\n'), [])
        self.assertEqual(sms._parse_messages('+CMGL: 2,3,,10\r\n0011\r\nOK\r\n'), [])
        for payload in ('+CMGL: bad\r\nOK\r\n', incoming().replace('214365', 'xyz'),
                        '+CMGL: 1,1,,100\r\nnot hex\r\nOK\r\n',
                        '+CMGL: 1,0,,9999\r\n' + make_pdu(b'', 0) + '\r\nOK\r\n'):
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                sms._parse_messages(payload)

    def test_known_gsm7_pdu(self):
        message = sms._decode_pdu('0000069121436500006211900102032305E8329BFD06')
        self.assertEqual(message['sender'], '+123456')
        self.assertEqual(message['timestamp'], '26/11/09,10:20:30+32')
        self.assertEqual(message['text'], 'hello')
        self.assertEqual(len(sms.GSM7), 128)

    def test_gsm_extensions_and_alphanumeric_sender(self):
        # Septets ESC 0x14 = ^; ESC 0x65 = euro sign.
        codes = [27, 20, 27, 101]
        bits = sum(value << (7 * i) for i, value in enumerate(codes))
        pdu = make_pdu(bits.to_bytes(4, 'little'), 4, dcs=0)
        self.assertEqual(sms._decode_pdu(pdu)['text'], '^€')
        # Alphanumeric originating address length is in useful semi-octets.
        name = b'\x41\x21'  # Packed GSM septets "AB".
        original = bytes.fromhex(make_pdu(b'', 0))
        alpha = original[:2] + bytes([4, 0xD0]) + name + original[7:]
        self.assertEqual(sms._decode_pdu(alpha.hex())['sender'], 'AB')

    def test_multipart_gsm7_padding_and_unicode(self):
        header = bytes.fromhex('050003AA0201')
        codes = [sms.GSM7.index(char) for char in 'hello']
        bits = int.from_bytes(header, 'little')
        for index, code in enumerate(codes):
            bits |= code << (49 + index * 7)
        packed = bits.to_bytes(11, 'little')
        pdu = make_pdu(packed, 12, dcs=0, header=True)
        self.assertEqual(sms._decode_pdu(pdu)['text'], '[Part 1/2] hello')
        payload = bytes.fromhex('06080412340202') + '你好'.encode('utf-16-be')
        pdu = make_pdu(payload, len(payload), header=True)
        self.assertEqual(sms._decode_pdu(pdu)['text'], '[Part 2/2] 你好')

    def test_binary_unsupported_and_bad_headers(self):
        for dcs in (4, 12, 0x20):
            message = sms._decode_pdu(make_pdu(b'\x01\x02', 2, dcs=dcs))
            self.assertIn('Binary/unsupported', message['text'])
            self.assertIn('0102', message['text'])
        for value in ('', '00', 'zz', make_pdu(b'\x05\x00', 2, header=True),
                      make_pdu(b'\x02\x00\x05', 3, header=True)):
            with self.subTest(value=value), self.assertRaises(ValueError):
                sms._decode_pdu(value)
        self.assertIsNone(sms._decode_pdu('0002'))

    def session_reply(self, port, command, **kwargs):
        replies = {
            'AT+CPIN?': '\r\n+CPIN: READY\r\nOK\r\n',
            'AT+CPMS?': '\r\n+CPMS: "ME",2,100,"ME",2,100,"ME",2,100\r\nOK\r\n',
            'AT+CMGF?': '\r\n+CMGF: 1\r\nOK\r\n',
            'AT+CMGL=4': incoming() + '\r\nOK\r\n',
        }
        return replies.get(command, '\r\nOK\r\n')

    def test_one_exclusive_session_and_settings_restored(self):
        with patch.object(sms.serial, 'Serial') as serial, \
                patch.object(sms, '_command', side_effect=self.session_reply) as command:
            data = sms._read_messages()
            serial.assert_called_once()
            self.assertTrue(serial.call_args.kwargs['exclusive'])
            serial.return_value.__exit__.assert_called_once()
            commands = [call.args[1] for call in command.call_args_list]
            self.assertIn('AT+CMGL=4', commands)
            self.assertEqual(commands[-1], 'AT+CMGF=1')
            self.assertEqual(data['storage'], 'ME')
            self.assertEqual(data['capacity'], 100)
            self.assertEqual(len(data['messages']), 1)
            self.assertFalse(any(c.startswith(('AT+CMGD', 'AT+CMGS', 'AT+CNMI', 'AT+CPMS=')) for c in commands))

    def test_restore_even_if_list_or_setup_fails(self):
        for failing in ('AT+CMGL=4', 'AT+CMGF=0'):
            def reply(port, command, **kwargs):
                if command == failing:
                    raise ValueError('modem error')
                return self.session_reply(port, command, **kwargs)
            with self.subTest(failing=failing):
                with patch.object(sms.serial, 'Serial') as serial, \
                        patch.object(sms, '_command', side_effect=reply) as command:
                    with self.assertRaises(ValueError):
                        sms._read_messages()
                    commands = [call.args[1] for call in command.call_args_list]
                    self.assertEqual(commands[-1], 'AT+CMGF=1')
                    serial.return_value.__exit__.assert_called_once()

    def test_restore_failure_is_reported(self):
        def reply(port, command, **kwargs):
            if command == 'AT+CMGF=1':
                raise ValueError('restore failed')
            return self.session_reply(port, command, **kwargs)
        with patch.object(sms.serial, 'Serial'), patch.object(sms, '_command', side_effect=reply) as command:
            with self.assertRaisesRegex(ValueError, 'restore'):
                sms._read_messages()
            self.assertEqual(command.call_args_list[-1].args[1], 'AT+CMGF=1')

    def test_sim_not_ready_does_not_change_settings(self):
        with patch.object(sms.serial, 'Serial'), \
                patch.object(sms, '_command', return_value='+CPIN: SIM PIN\r\nOK\r\n') as command:
            with self.assertRaisesRegex(ValueError, 'SIM'):
                sms._read_messages()
            self.assertEqual(command.call_count, 1)

    def test_command_waits_for_complete_final_result(self):
        chunks = [b'\r\n+CM', b'GL: 1,"REC READ"\r\n004F004B\r\n', b'\r\nO', b'K\r\n']
        port = MagicMock()
        port.in_waiting = 100
        port.read.side_effect = chunks
        with patch.object(sms.time, 'sleep'):
            response = sms._command(port, 'AT+CMGL=4')
        self.assertTrue(response.endswith('\r\nOK\r\n'))
        self.assertEqual(port.read.call_count, 4)

    def test_command_error_and_timeout(self):
        for final in ('ERROR', '+CMS ERROR: 310', '+CME ERROR: SIM not inserted'):
            port = MagicMock()
            port.in_waiting = 100
            port.read.return_value = ('\r\n' + final + '\r\n').encode()
            with self.subTest(final=final), self.assertRaises(ValueError):
                sms._command(port, 'AT+CPIN?')
        port = MagicMock()
        with patch.object(sms.time, 'monotonic', side_effect=[0, 4]):
            with self.assertRaisesRegex(ValueError, 'timed out'):
                sms._command(port, 'AT')


if __name__ == '__main__':
    unittest.main()
