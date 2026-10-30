"""LTE Setting API routes — EC200A AT command interface."""

import os
import subprocess
import time

import serial
from flask import Blueprint, jsonify, request
from flask_login import login_required

from auth import admin_required
from config import (LTE_SERIAL_PORT, LTE_SERIAL_BAUD,
                    WATCHDOG_SCRIPT, WATCHDOG_TARGETS_FILE, CONFIG_FILE_DIR)

lte_bp = Blueprint('lte', __name__)


def _run(cmd):
    """Run a shell command and return (success, stdout, stderr)."""
    try:
        result = subprocess.run(
            cmd, shell=True, capture_output=True, text=True, timeout=30
        )
        return result.returncode == 0, result.stdout.strip(), result.stderr.strip()
    except subprocess.TimeoutExpired:
        return False, '', 'Command timed out'


def _send_at(command, timeout=3):
    """Send an AT command to the EC200A module and return the response."""
    try:
        ser = serial.Serial(
            port=LTE_SERIAL_PORT,
            baudrate=LTE_SERIAL_BAUD,
            timeout=timeout,
            exclusive=True,
        )
        ser.reset_input_buffer()
        ser.write((command + '\r\n').encode())

        response = ''
        deadline = time.time() + timeout
        while time.time() < deadline:
            if ser.in_waiting:
                response += ser.read(ser.in_waiting).decode('utf-8', errors='ignore')
                if 'OK' in response or 'ERROR' in response:
                    break
            time.sleep(0.05)

        ser.close()
        return response.strip()
    except (serial.SerialException, OSError) as e:
        return f'ERROR: {str(e)}'


def _parse_at_response(response, prefix):
    """Extract the line starting with prefix from an AT response."""
    for line in response.splitlines():
        line = line.strip()
        if line.startswith(prefix):
            return line[len(prefix):].strip()
    return ''


def _get_watchdog_target():
    """Get the first watchdog target IP for ping testing."""
    try:
        with open(WATCHDOG_TARGETS_FILE, 'r') as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith('#'):
                    # Strip http(s):// prefix if present — AT+QPING needs IP or hostname
                    if '://' in line:
                        line = line.split('://', 1)[1]
                    # Strip trailing path
                    line = line.split('/')[0]
                    return line
    except FileNotFoundError:
        pass
    return '8.8.8.8'


@lte_bp.route('/api/lte/status')
@login_required
def get_status():
    """Get LTE modem status via AT commands."""
    # SIM status check first
    resp = _send_at('AT+CPIN?')
    cpin_line = _parse_at_response(resp, '+CPIN:')
    if 'READY' in cpin_line:
        sim_status = 'Inserted'
    elif 'ERROR' in resp:
        sim_status = 'Not Detected'
    else:
        sim_status = cpin_line or 'Not Detected'

    # If SIM not inserted, skip the rest
    if sim_status != 'Inserted':
        return jsonify({
            'sim_status': sim_status,
            'status': '--',
            'carrier': '--',
            'access': '--',
            'band': '--',
            'signal': '--',
        })

    # Internet connection status via AT+QPING
    ping_target = _get_watchdog_target()
    resp = _send_at(f'AT+QPING=1,"{ping_target}",4,1', timeout=10)
    # +QPING: 0,... means success; +QPING: 569,... or ERROR means failure
    ping_result = _parse_at_response(resp, '+QPING:')
    if ping_result:
        ping_code = ping_result.split(',')[0].strip()
        status = 'Connected' if ping_code == '0' else 'Disconnected'
    else:
        status = 'Disconnected'

    # Carrier
    resp = _send_at('AT+QSPN')
    carrier_line = _parse_at_response(resp, '+QSPN:')
    carrier_parts = [p.strip().strip('"') for p in carrier_line.split(',')]
    carrier = carrier_parts[0] if carrier_parts else ''

    # Access technology and Band
    resp = _send_at('AT+QNWINFO')
    nw_line = _parse_at_response(resp, '+QNWINFO:')
    nw_parts = [p.strip().strip('"') for p in nw_line.split(',')]
    access = nw_parts[0] if len(nw_parts) >= 1 else ''
    band = nw_parts[2] if len(nw_parts) >= 3 else ''

    # Signal strength
    resp = _send_at('AT+CSQ')
    csq_line = _parse_at_response(resp, '+CSQ:')
    csq_parts = [p.strip() for p in csq_line.split(',')]
    signal = csq_parts[0] if csq_parts else ''

    return jsonify({
        'sim_status': sim_status,
        'status': status,
        'carrier': carrier,
        'access': access,
        'band': band,
        'signal': signal,
    })


@lte_bp.route('/api/lte/reboot', methods=['POST'])
@admin_required
def reboot_lte():
    """Reboot the EC200A LTE module via AT+CFUN=1,1."""
    resp = _send_at('AT+CFUN=1,1', timeout=5)
    if 'ERROR' in resp:
        return jsonify({'error': f'Failed to reboot LTE module: {resp}'}), 500
    return jsonify({'message': 'LTE module rebooting...'})


# ---------------------------------------------------------------------------
# Internet Watchdog
# ---------------------------------------------------------------------------

@lte_bp.route('/api/lte/watchdog')
@login_required
def get_watchdog():
    """Get watchdog target IPs from config file."""
    targets = []
    try:
        with open(WATCHDOG_TARGETS_FILE, 'r') as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith('#'):
                    targets.append(line)
    except FileNotFoundError:
        # Fall back to reading from the script itself
        try:
            with open(WATCHDOG_SCRIPT, 'r') as f:
                for line in f:
                    if line.strip().startswith('TARGETS='):
                        # Parse TARGETS=("8.8.8.8" "1.1.1.1" "9.9.9.9")
                        import re
                        ips = re.findall(r'["\']([\d.]+)["\']', line)
                        targets = ips
                        break
        except FileNotFoundError:
            targets = ['8.8.8.8', '1.1.1.1', '9.9.9.9']

    return jsonify({'targets': targets})


@lte_bp.route('/api/lte/watchdog', methods=['POST'])
@admin_required
def set_watchdog():
    """Save watchdog targets (IPs or hostnames) to config file and update the script."""
    data = request.get_json(silent=True) or {}
    targets = data.get('targets', [])

    # Validate targets: allow IPs, hostnames, and URLs
    import re
    # Accept IPs, hostnames (e.g. google.com), or URLs (e.g. http://example.com)
    valid_targets = [t.strip() for t in targets if t.strip()]

    if not valid_targets:
        return jsonify({'error': 'At least one target is required'}), 400

    # Save to config file
    os.makedirs(os.path.dirname(WATCHDOG_TARGETS_FILE), exist_ok=True)
    with open(WATCHDOG_TARGETS_FILE, 'w') as f:
        f.write('\n'.join(valid_targets) + '\n')

    # Update the watchdog script TARGETS line
    if os.path.exists(WATCHDOG_SCRIPT):
        with open(WATCHDOG_SCRIPT, 'r') as f:
            lines = f.readlines()

        new_targets_line = 'TARGETS=("' + '" "'.join(valid_targets) + '")\n'
        with open(WATCHDOG_SCRIPT, 'w') as f:
            for line in lines:
                if line.strip().startswith('TARGETS='):
                    f.write(new_targets_line)
                else:
                    f.write(line)

    # Restart the watchdog service so it picks up the new targets
    _run('systemctl restart internet-watchdog')

    return jsonify({'message': f'Watchdog targets updated: {" ".join(valid_targets)}'})
