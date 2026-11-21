"""Diagnostic API routes: ping, device status, connection manager status."""

import os
import stat
import subprocess
import secrets
from datetime import datetime

from flask import Blueprint, jsonify, make_response, render_template, request, session
from flask_login import login_required, logout_user

from auth import manage_required

diagnostic_bp = Blueprint('diagnostic', __name__)

# Match the TCP client installer and the watchdog's built-in rotation settings.
# These paths are server-controlled; requests select only an allowlisted key.
DIAGNOSTIC_LOGS = {
    'cm': ('/var/log/tcp_client/connection_manager.log', 3),
    'ping': ('/var/log/internet-watchdog.log', 5),
}


@diagnostic_bp.app_context_processor
def reboot_template_context():
    def reboot_csrf_token():
        if 'reboot_csrf_token' not in session:
            session['reboot_csrf_token'] = secrets.token_hex(32)
        return session['reboot_csrf_token']
    def diagnostic_logs_csrf_token():
        if 'diagnostic_logs_csrf_token' not in session:
            session['diagnostic_logs_csrf_token'] = secrets.token_hex(32)
        return session['diagnostic_logs_csrf_token']
    return {
        'reboot_csrf_token': reboot_csrf_token,
        'diagnostic_logs_csrf_token': diagnostic_logs_csrf_token,
    }


@diagnostic_bp.after_request
def no_diagnostic_cache(response):
    response.headers['Cache-Control'] = 'no-store'
    return response


def _clear_log_files(source):
    """Truncate active logs and remove exact rotations, never following links."""
    path, backups = DIAGNOSTIC_LOGS[source]
    directory = os.path.dirname(path)
    if os.path.normcase(os.path.realpath(directory)) != os.path.normcase(os.path.abspath(directory)):
        raise ValueError('Log deletion refused: the log directory resolves through a link.')
    entries = []
    for candidate in [path] + [f'{path}.{index}' for index in range(1, backups + 1)]:
        try:
            info = os.lstat(candidate)
        except FileNotFoundError:
            continue
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise ValueError('Log deletion refused: a log is not a regular, single-link file.')
        entries.append((candidate, info))

    # Preflight every known entry before modifying any of them. Recheck the
    # opened inode to reject replacement, symlinks and hard links before truncation.
    changed = False
    try:
        for candidate, expected in entries:
            flags = os.O_WRONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0)
            descriptor = os.open(candidate, flags)
            try:
                actual = os.fstat(descriptor)
                if (not stat.S_ISREG(actual.st_mode) or actual.st_nlink != 1
                        or (actual.st_dev, actual.st_ino) != (expected.st_dev, expected.st_ino)):
                    raise OSError('Log changed while deletion was running')
                changed = True
                os.ftruncate(descriptor, 0)
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
            if candidate != path:
                current = os.lstat(candidate)
                if (current.st_dev, current.st_ino) != (expected.st_dev, expected.st_ino):
                    raise OSError('Rotated log changed while deletion was running')
                os.unlink(candidate)
    except OSError as error:
        if changed:
            raise ValueError('Deletion was not fully confirmed. Some log data may already '
                             'have been deleted; refresh the logs before retrying.') from error
        raise
    return len(entries)


@diagnostic_bp.route('/api/diagnostic/logs/<source>/delete', methods=['POST'])
@manage_required
def delete_diagnostic_logs(source):
    if source not in DIAGNOSTIC_LOGS:
        return jsonify(error='Unknown diagnostic log source'), 404
    token = session.get('diagnostic_logs_csrf_token', '')
    supplied = request.headers.get('X-CSRF-Token', '')
    if not token or not secrets.compare_digest(token.encode(), supplied.encode()):
        return jsonify(error='Invalid CSRF token; reload the page and try again'), 403
    data = request.get_json(silent=True)
    if not isinstance(data, dict) or data.get('confirm') is not True:
        return jsonify(error='Explicit confirmation is required'), 400
    try:
        count = _clear_log_files(source)
    except ValueError as error:
        return jsonify(error=str(error)), 409
    except OSError:
        return jsonify(error='Unable to delete log files; check permissions and retry.'), 500
    name = 'CM' if source == 'cm' else 'Ping'
    message = (f'{name} log-file history deleted.' if count
               else f'No saved {name} log files were present.')
    return jsonify(message=message + ' New entries may appear while the service runs. '
                   'Shared system journal history was not deleted.', files_cleared=count)


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
    """Display the dedicated CM file so viewing and deletion use the same source."""
    ok, out, err = _run(
        'tail -n 100 ' + DIAGNOSTIC_LOGS['cm'][0],
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
    ok, out, err = _run('tail -n 100 ' + DIAGNOSTIC_LOGS['ping'][0])
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
