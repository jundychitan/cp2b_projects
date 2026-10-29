"""Connection Manager API routes.

Wraps the existing /projects/tcp_client/connection_manager.py.
The exact integration depends on what that module exposes.
This provides a subprocess-based fallback that runs it as a script.
"""

import subprocess
import sys
import os

from flask import Blueprint, jsonify, request
from flask_login import login_required

from auth import admin_required
from config import TCP_CLIENT_DIR

connection_bp = Blueprint('connection', __name__)

# Try to import connection_manager as a module
_cm_module = None
try:
    sys.path.insert(0, TCP_CLIENT_DIR)
    import connection_manager as _cm_module
except (ImportError, ModuleNotFoundError):
    _cm_module = None


def _run(cmd, cwd=None):
    """Run a shell command and return (success, stdout, stderr)."""
    try:
        result = subprocess.run(
            cmd, shell=True, capture_output=True, text=True, timeout=30, cwd=cwd
        )
        return result.returncode == 0, result.stdout.strip(), result.stderr.strip()
    except subprocess.TimeoutExpired:
        return False, '', 'Command timed out'


@connection_bp.route('/api/connection/status')
@login_required
def get_status():
    """Get connection manager status."""
    # Check if the connection_manager process is running
    ok, out, _ = _run('pgrep -f connection_manager.py')
    running = ok and out != ''

    script_path = os.path.join(TCP_CLIENT_DIR, 'connection_manager.py')
    script_exists = os.path.exists(script_path)

    return jsonify({
        'running': running,
        'script_exists': script_exists,
        'script_path': script_path,
    })


@connection_bp.route('/api/connection/start', methods=['POST'])
@admin_required
def start_connection():
    """Start the connection manager script."""
    script_path = os.path.join(TCP_CLIENT_DIR, 'connection_manager.py')

    if not os.path.exists(script_path):
        return jsonify({'error': f'Script not found: {script_path}'}), 404

    # Start in background
    ok, _, err = _run(
        f'nohup python3 {script_path} > /dev/null 2>&1 &',
        cwd=TCP_CLIENT_DIR
    )

    if not ok:
        return jsonify({'error': f'Failed to start: {err}'}), 500

    return jsonify({'message': 'Connection manager started'})


@connection_bp.route('/api/connection/stop', methods=['POST'])
@admin_required
def stop_connection():
    """Stop the connection manager script."""
    ok, _, err = _run('pkill -f connection_manager.py')

    if not ok:
        return jsonify({'error': 'No running process found or failed to stop'}), 500

    return jsonify({'message': 'Connection manager stopped'})


@connection_bp.route('/api/connection/restart', methods=['POST'])
@admin_required
def restart_connection():
    """Restart the connection manager script."""
    _run('pkill -f connection_manager.py')

    script_path = os.path.join(TCP_CLIENT_DIR, 'connection_manager.py')
    if not os.path.exists(script_path):
        return jsonify({'error': f'Script not found: {script_path}'}), 404

    _run(
        f'nohup python3 {script_path} > /dev/null 2>&1 &',
        cwd=TCP_CLIENT_DIR
    )

    return jsonify({'message': 'Connection manager restarted'})
