"""Admin-only lifecycle control of the installed connection manager service."""

import secrets
import subprocess

from flask import Blueprint, jsonify, request, session

from auth import admin_required
from config import CONNECTION_MANAGER_SERVICE

connection_bp = Blueprint('connection', __name__)


@connection_bp.app_context_processor
def connection_template_context():
    def connection_csrf_token():
        if 'connection_csrf_token' not in session:
            session['connection_csrf_token'] = secrets.token_hex(32)
        return session['connection_csrf_token']
    return {'connection_csrf_token': connection_csrf_token}


def _run(args):
    """Run fixed argument lists, never a shell or a client module import."""
    try:
        result = subprocess.run(args, capture_output=True, text=True, timeout=30)
        return result.returncode == 0, result.stdout.strip(), result.stderr.strip()
    except subprocess.TimeoutExpired:
        return False, '', 'Command timed out; the operation may still be in progress'
    except OSError:
        return False, '', 'Unable to run systemctl; this feature requires Linux with systemd'


def _service_status():
    ok, out, err = _run([
        'systemctl', '--no-pager', 'show',
        '--property=LoadState,ActiveState,SubState,UnitFileState',
        CONNECTION_MANAGER_SERVICE,
    ])
    properties = dict(line.split('=', 1) for line in out.splitlines() if '=' in line)
    if properties.get('LoadState') == 'not-found':
        return None, 'Connection manager service is not installed'
    if not ok or properties.get('LoadState') != 'loaded':
        return None, err or 'Unable to read the installed connection manager service'
    if not all(properties.get(key) for key in ('ActiveState', 'SubState', 'UnitFileState')):
        return None, 'Incomplete connection manager service status'
    return {
        'service': CONNECTION_MANAGER_SERVICE,
        'enabled': properties['UnitFileState'] == 'enabled',
        'running': properties['ActiveState'] == 'active',
        'active_state': properties['ActiveState'],
        'sub_state': properties['SubState'],
        'boot_state': properties['UnitFileState'],
    }, None


@connection_bp.route('/api/connection/status')
@admin_required
def get_status():
    status, error = _service_status()
    response = jsonify({'error': error} if error else status)
    response.headers['Cache-Control'] = 'no-store'
    return response, 503 if error else 200


@connection_bp.route('/api/connection/toggle', methods=['POST'])
@admin_required
def toggle_connection():
    token = session.get('connection_csrf_token', '')
    supplied = request.headers.get('X-CSRF-Token', '')
    if not token or not secrets.compare_digest(token.encode(), supplied.encode()):
        return jsonify({'error': 'Invalid CSRF token; reload the page and try again'}), 403
    data = request.get_json(silent=True)
    if not isinstance(data, dict) or type(data.get('enabled')) is not bool:
        return jsonify({'error': 'enabled must be a JSON boolean'}), 400
    status, error = _service_status()
    if error:
        return jsonify({'error': error}), 503
    enabled = data['enabled']
    action = 'enable' if enabled else 'disable'
    ok, out, err = _run(['systemctl', action, '--now', CONNECTION_MANAGER_SERVICE])
    if not ok:
        return jsonify({
            'error': f'Could not {action} connection manager: {err or out}. '
                     'Boot or runtime state may have changed; refresh status before retrying.',
        }), 500
    status, error = _service_status()
    if error:
        return jsonify({'error': f'Command completed but status could not be verified: {error}'}), 503
    expected_boot = 'enabled' if enabled else 'disabled'
    runtime_matches = status['running'] if enabled else status['active_state'] in ('inactive', 'failed')
    if status['boot_state'] != expected_boot or not runtime_matches:
        return jsonify({
            **status,
            'error': 'Service has not reached the requested boot/runtime state. Refresh status to check.',
        }), 409
    return jsonify({
        **status,
        'message': 'Connection manager enabled and started. Saved client configuration retained.'
                   if enabled else
                   'Connection manager disabled and stopped, including boot startup. '
                   'Saved client configuration retained.',
    })
