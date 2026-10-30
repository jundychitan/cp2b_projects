"""Diagnostic API routes: ping, device status, connection manager status."""

import subprocess

from flask import Blueprint, jsonify, request
from flask_login import login_required

diagnostic_bp = Blueprint('diagnostic', __name__)


def _run(cmd, timeout=30):
    """Run a shell command and return (success, stdout, stderr)."""
    try:
        result = subprocess.run(
            cmd, shell=True, capture_output=True, text=True, timeout=timeout
        )
        return result.returncode == 0, result.stdout.strip(), result.stderr.strip()
    except subprocess.TimeoutExpired:
        return False, '', 'Command timed out'


@diagnostic_bp.route('/api/diagnostic/ping', methods=['POST'])
@login_required
def ping_target():
    """Ping a target IP/hostname/URL."""
    data = request.get_json(silent=True) or {}
    target = data.get('target', '').strip()

    if not target:
        return jsonify({'error': 'Target is required'}), 400

    # Sanitize: only allow alphanumeric, dots, hyphens, colons, slashes
    import re
    if not re.match(r'^[a-zA-Z0-9.\-:/]+$', target):
        return jsonify({'error': 'Invalid target format'}), 400

    ok, out, err = _run(f'ping -c 4 -W 5 {target}', timeout=30)
    output = out if out else err

    return jsonify({'output': output, 'success': ok})


@diagnostic_bp.route('/api/diagnostic/devices', methods=['POST'])
@login_required
def check_devices():
    """Run nmcli device status."""
    ok, out, err = _run('nmcli device status')
    output = out if out else err

    return jsonify({'output': output, 'success': ok})


@diagnostic_bp.route('/api/diagnostic/cm-status', methods=['POST'])
@login_required
def check_cm_status():
    """Run journalctl for connection_manager.service."""
    ok, out, err = _run(
        'journalctl -u connection-manager.service -e --no-pager',
        timeout=15
    )
    output = out if out else err

    return jsonify({'output': output, 'success': ok})


@diagnostic_bp.route('/api/diagnostic/routes', methods=['POST'])
@login_required
def check_routes():
    """Run ip route show."""
    ok, out, err = _run('ip route show')
    output = out if out else err

    return jsonify({'output': output, 'success': ok})


@diagnostic_bp.route('/api/diagnostic/lte-check', methods=['POST'])
@login_required
def lte_check():
    """Query LTE modem info via AT commands."""
    from blueprints.lte import _send_at
    lines = []
    for cmd in ['ATI', 'AT+CPIN?', 'AT+CSQ', 'AT+QNWINFO', 'AT+QSPN', 'AT+QNETDEVCTL?']:
        resp = _send_at(cmd, timeout=3)
        lines.append(f'> {cmd}')
        lines.append(resp)
        lines.append('')
    output = '\n'.join(lines)
    return jsonify({'output': output, 'success': True})


@diagnostic_bp.route('/api/diagnostic/ping-logs', methods=['POST'])
@login_required
def ping_logs():
    """Display internet-watchdog log."""
    ok, out, err = _run('tail -n 100 /var/log/internet-watchdog.log')
    output = out if out else err

    return jsonify({'output': output, 'success': ok})


@diagnostic_bp.route('/api/diagnostic/reboot', methods=['POST'])
@login_required
def reboot_device():
    """Reboot the device."""
    from auth import admin_required as _  # verify import available
    from flask_login import current_user
    if not current_user.is_admin:
        return jsonify({'error': 'Admin access required'}), 403

    # Schedule reboot in 2 seconds so the response can be sent
    _run('nohup bash -c "sleep 2 && reboot" &')
    return jsonify({'message': 'Device is rebooting...'})
