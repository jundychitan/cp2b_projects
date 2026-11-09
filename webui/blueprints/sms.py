"""Read stored EC200A SMS. Listing marks unread messages read on the modem."""

import csv
import math
import re
import time

import serial
from flask import Blueprint, jsonify
from flask_login import login_required

from blueprints.lte import _modem_present
from config import LTE_SERIAL_BAUD, LTE_SERIAL_PORT

sms_bp = Blueprint('sms', __name__)

GSM7 = (
    '@£$¥èéùìòÇ\nØø\rÅåΔ_ΦΓΛΩΠΨΣΘΞ\x1bÆæßÉ'
    ' !"#¤%&\'()*+,-./0123456789:;<=>?'
    '¡ABCDEFGHIJKLMNOPQRSTUVWXYZÄÖÑÜ§'
    '¿abcdefghijklmnopqrstuvwxyzäöñüà'
)
GSM7_EXT = {10: '\f', 20: '^', 40: '{', 41: '}', 47: '\\',
            60: '[', 61: '~', 62: ']', 64: '|', 101: '€'}


def _command(port, command, timeout=3):
    """Wait for a complete final result; PDU payloads are hexadecimal only."""
    port.write((command + '\r\n').encode('ascii'))
    response = ''
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if port.in_waiting:
            response += port.read(port.in_waiting).decode('ascii', errors='replace')
            if len(response) > 1024 * 1024:
                raise ValueError('SMS response exceeds the supported size')
            lines = response.split('\r\n')
            for line in lines[:-1]:
                if line == 'ERROR' or line.startswith(('+CMS ERROR:', '+CME ERROR:')):
                    raise ValueError('The modem rejected the SMS request: ' + line)
            if 'OK' in lines[:-1]:
                return response
        time.sleep(0.05)
    raise ValueError('The LTE modem timed out while reading SMS')


def _gsm7(data, count, offset=0):
    bits = int.from_bytes(data, 'little')
    result = []
    escaped = False
    for index in range(count):
        value = (bits >> (offset + index * 7)) & 127
        if escaped:
            result.append(GSM7_EXT.get(value, '�'))
            escaped = False
        elif value == 27:
            escaped = True
        else:
            result.append(GSM7[value])
    if escaped:
        result.append('�')
    return ''.join(result)


def _address(data, length, kind):
    if kind & 0x70 == 0x50:
        return _gsm7(data, length * 4 // 7)
    digits = ''.join(f'{byte & 15:X}{byte >> 4:X}' for byte in data)[:length]
    return ('+' if kind & 0x70 == 0x10 else '') + digits.lower().translate(
        str.maketrans({'a': '*', 'b': '#', 'c': 'a', 'd': 'b', 'e': 'c'}))


def _decode_pdu(value):
    """Decode SMS-DELIVER only; keep multipart segments as separate rows."""
    if not re.fullmatch(r'(?:[0-9A-Fa-f]{2})+', value):
        raise ValueError('Malformed SMS PDU')
    data = bytes.fromhex(value)
    position = 0

    def take(length):
        nonlocal position
        result = data[position:position + length]
        if len(result) != length:
            raise ValueError('Truncated SMS PDU')
        position += length
        return result

    take(1 + data[0])  # SMS service-centre address.
    first = take(1)[0]
    if first & 3 != 0:
        return None  # Stored outgoing messages and delivery reports are not inbox SMS.
    address_length, address_type = take(2)
    sender = _address(take((address_length + 1) // 2), address_length, address_type)
    take(1)  # Protocol identifier.
    dcs = take(1)[0]
    stamp = take(7)
    numbers = [(byte & 15) * 10 + (byte >> 4) for byte in stamp[:6]]
    zone = (stamp[6] & 7) * 10 + (stamp[6] >> 4)
    timestamp = ('%02d/%02d/%02d,%02d:%02d:%02d' % tuple(numbers)
                 + ('-' if stamp[6] & 8 else '+') + f'{zone:02d}')
    length = take(1)[0]
    # General coding group, message-waiting groups, and class-coded GSM/8-bit.
    if dcs & 0xC0 == 0:
        alphabet = (dcs >> 2) & 3 if not dcs & 0x20 else 3
    elif dcs & 0xF0 in (0xC0, 0xD0):
        alphabet = 0
    elif dcs & 0xF0 == 0xE0:
        alphabet = 2
    elif dcs & 0xF0 == 0xF0:
        alphabet = 1 if dcs & 4 else 0
    else:
        alphabet = 3
    payload = take(math.ceil(length * 7 / 8) if alphabet == 0 else length)
    header_length = 0
    part = ''
    if first & 0x40:
        if not payload:
            raise ValueError('Missing SMS user-data header')
        header_length = payload[0] + 1
        if header_length > len(payload):
            raise ValueError('Truncated SMS user-data header')
        cursor = 1
        while cursor < header_length:
            if cursor + 2 > header_length:
                raise ValueError('Malformed SMS user-data header')
            tag, size = payload[cursor:cursor + 2]
            content = payload[cursor + 2:cursor + 2 + size]
            if cursor + 2 + size > header_length:
                raise ValueError('Malformed SMS user-data header')
            if (tag == 0 and size == 3) or (tag == 8 and size == 4):
                part = f'[Part {content[-1]}/{content[-2]}] '
            # National-language shift tables require a different GSM alphabet.
            if tag in (0x24, 0x25):
                alphabet = 3
            cursor += 2 + size
    if alphabet == 0:
        skipped = math.ceil(header_length * 8 / 7)
        if skipped > length:
            raise ValueError('Invalid SMS user-data length')
        text = _gsm7(payload, length - skipped, skipped * 7)
    elif alphabet == 2:
        text = payload[header_length:].decode('utf-16-be', errors='replace')
    else:
        text = '[Binary/unsupported SMS encoding: ' + payload[header_length:].hex().upper() + ']'
    return {'sender': sender, 'timestamp': timestamp, 'text': part + text}


def _parse_messages(response):
    messages = []
    lines = response.split('\r\n')
    for index, line in enumerate(lines):
        if not line.startswith('+CMGL:'):
            continue
        try:
            fields = next(csv.reader([line[len('+CMGL:'):].strip()], skipinitialspace=True))
            if len(fields) != 4 or not fields[0].isdigit() or not fields[3].isdigit():
                raise ValueError
            if fields[1] not in ('0', '1'):
                continue
            pdu = lines[index + 1]
            message = _decode_pdu(pdu)
            raw = bytes.fromhex(pdu)
            if len(raw) - 1 - raw[0] != int(fields[3]):
                raise ValueError('SMS length does not match its header')
            if message is not None:
                message.update(index=int(fields[0]),
                               status='REC UNREAD' if fields[1] == '0' else 'REC READ')
                messages.append(message)
        except (IndexError, csv.Error, ValueError) as error:
            raise ValueError('Unable to decode a stored SMS message') from error
    return messages


def _read_messages():
    # One exclusive serial session prevents other web UI workers from
    # interleaving commands while the SMS display format is temporarily changed.
    with serial.Serial(port=LTE_SERIAL_PORT, baudrate=LTE_SERIAL_BAUD,
                       timeout=0.2, write_timeout=3, exclusive=True) as port:
        port.reset_input_buffer()
        response = _command(port, 'AT+CPIN?')
        if not re.search(r'^\+CPIN:\s*READY\s*$', response, re.M):
            raise ValueError('SMS unavailable: SIM is missing or requires a PIN')
        storage_response = _command(port, 'AT+CPMS?')
        storage = re.search(r'^\+CPMS:\s*"([A-Z]+)",\s*(\d+),\s*(\d+)', storage_response, re.M)
        if not storage:
            raise ValueError('Unable to read SMS storage information')
        response = _command(port, 'AT+CMGF?')
        setting = re.search(r'^\+CMGF:\s*([01])\s*$', response, re.M)
        if not setting:
            raise ValueError('Unable to read modem SMS format')
        previous = setting.group(1)
        try:
            if previous != '0':
                _command(port, 'AT+CMGF=0')
            # EC200A documents no preservation mode: this marks unread SMS read.
            messages = _parse_messages(_command(port, 'AT+CMGL=4', timeout=20))
        finally:
            if previous != '0':
                try:
                    _command(port, 'AT+CMGF=' + previous)
                except (ValueError, serial.SerialException, OSError) as error:
                    raise ValueError('Unable to restore modem SMS format; retry when available') from error
        return {
            'present': True, 'messages': messages,
            'storage': storage.group(1),
            'used': int(storage.group(2)), 'capacity': int(storage.group(3)),
        }


@sms_bp.after_request
def no_sms_cache(response):
    response.headers['Cache-Control'] = 'no-store'
    return response


@sms_bp.route('/api/lte/sms')
@login_required
def get_sms():
    if not _modem_present():
        return jsonify(present=False, error='LTE module is not installed'), 503
    try:
        return jsonify(_read_messages())
    except (serial.SerialException, OSError):
        return jsonify(present=_modem_present(),
                       error='LTE modem is busy or unavailable. Please retry.'), 503
    except ValueError as error:
        return jsonify(present=_modem_present(), error=str(error)), 503
