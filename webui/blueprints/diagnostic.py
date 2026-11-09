"""Diagnostic API routes: ping, device status, connection manager status."""

import subprocess
import secrets
from datetime import datetime

from flask import Blueprint, jsonify, make_response, render_template, request, session
from flask_login import login_required, logout_user

from auth import manage_required

diagnostic_bp = Blueprint('diagnostic', __name__)


@diagnostic_bp.app_context_processor
def reboot_template_context():
    def reboot_csrf_token():
        if 'reboot_csrf_token' not in session:
            session['reboot_csrf_token'] = secrets.token_hex(32)
        return session['reboot_csrf_token']
    return {'reboot_csrf_token': reboot_csrf_token}


@diagnostic_bp.route('/rebooting')
def rebooting():
    response = make_response(render_template('rebooting.html'))
    response.headers['Cache-Control'] = 'no-store'
    return response


@diagnostic_bp.route('/api/diagnostic/ready')
def ready():
    """Public liveness check: no authentication, system details, or commands."""
    response = jsonify({'status': 'ready'})
    response.headers['Cache-Control'] = 'no-store'
    return response


@diagnostic_bp.route('/api/diagnostic/time')
@login_required
def machine_time():
    """Return the device clock and local offset, never the browser's timezone."""
    now = datetime.now().astimezone()
    response = jsonify({
        'unix_ms': int(now.timestamp() * 1000),
        'utc_offset_seconds': int(now.utcoffset().total_seconds()),
        'timezone': now.tzname() or 'UTC',
    })
    response.headers['Cache-Control'] = 'no-store'
    return response


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
    from blueprints.lte import _modem_present, _send_at
    if not _modem_present():
        return jsonify({
            'error': 'LTE Check is unavailable: LTE modem is not installed.',
            'present': False,
        }), 503
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
@manage_required
def reboot_device():
    """Schedule reboot, then log out this browser before the timer fires."""
    token = session.get('reboot_csrf_token', '')
    supplied = request.headers.get('X-CSRF-Token', '')
    if not token or not secrets.compare_digest(token.encode(), supplied.encode()):
        return jsonify({'error': 'Invalid CSRF token; reload the page and try again'}), 403
    try:
        result = subprocess.run([
            'systemd-run', '--unit=amr-webui-reboot', '--on-active=5s',
            '/usr/bin/systemctl', 'reboot',
        ], capture_output=True, text=True, timeout=10)
    except subprocess.TimeoutExpired:
        return jsonify({'error': 'Reboot scheduling timed out; device state is uncertain'}), 504
    except OSError:
        return jsonify({'error': 'Unable to schedule reboot; systemd-run is required'}), 503
    if result.returncode != 0:
        return jsonify({'error': 'Failed to schedule reboot: ' + result.stderr.strip()}), 500
    logout_user()
    session.clear()
    # Preserve Flask-Login's cookie-clearing instruction after clearing the session.
    session['_remember'] = 'clear'
    response = jsonify({'message': 'Rebooting...', 'redirect': '/rebooting'})
    response.headers['Cache-Control'] = 'no-store'
    return response
