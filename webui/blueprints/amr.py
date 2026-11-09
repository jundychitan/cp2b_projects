"""AMR Setting API routes: auto read, meter batch, config file editor."""

import csv
import io
import os
import re
import secrets
import stat
import subprocess
import tempfile
import threading
from contextlib import contextmanager

from flask import Blueprint, jsonify, request, session
from flask_login import login_required

from auth import manage_required
from config import AMR_METER_DIR, AMR_UPLOAD_DIR, METER_BATCH_FILE, PROCESS_METERS_SCRIPT, SYNC_SERVER_FILE

amr_bp = Blueprint('amr', __name__)
MAX_METERS = 32
_meter_thread_lock = threading.Lock()


@amr_bp.app_context_processor
def sync_time_template_context():
    def sync_time_csrf_token():
        if 'sync_time_csrf_token' not in session:
            session['sync_time_csrf_token'] = secrets.token_hex(32)
        return session['sync_time_csrf_token']
    return {'sync_time_csrf_token': sync_time_csrf_token}


@amr_bp.route('/api/amr/sync-time')
@login_required
def get_sync_time():
    """Report marker presence without creating files or changing device time."""
    try:
        mode = os.stat(SYNC_SERVER_FILE, follow_symlinks=False).st_mode
        if not stat.S_ISREG(mode):
            return jsonify({'error': 'sync_server is not a regular file; check the config directory'}), 409
        enabled = True
    except FileNotFoundError:
        enabled = False
    except OSError:
        return jsonify({'error': 'Unable to read Sync Server Time state'}), 500
    response = jsonify({'enabled': enabled})
    response.headers['Cache-Control'] = 'no-store'
    return response


@amr_bp.route('/api/amr/sync-time', methods=['POST'])
@manage_required
def set_sync_time():
    """Create an empty sync_server marker when checked, remove it when unchecked."""
    token = session.get('sync_time_csrf_token', '')
    supplied = request.headers.get('X-CSRF-Token', '')
    if not token or not secrets.compare_digest(token.encode(), supplied.encode()):
        return jsonify({'error': 'Invalid CSRF token; reload the page and try again'}), 403
    data = request.get_json(silent=True)
    if not isinstance(data, dict) or type(data.get('enabled')) is not bool:
        return jsonify({'error': 'enabled must be a JSON boolean'}), 400
    enabled = data['enabled']
    try:
        if enabled:
            os.makedirs(os.path.dirname(SYNC_SERVER_FILE), exist_ok=True)
            flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, 'O_NOFOLLOW', 0)
            descriptor = os.open(SYNC_SERVER_FILE, flags, 0o644)
            os.close(descriptor)
        else:
            try:
                os.unlink(SYNC_SERVER_FILE)
            except FileNotFoundError:
                pass
    except OSError:
        return jsonify({'error': 'Unable to save Sync Server Time; check config directory permissions'}), 500
    response = jsonify({
        'enabled': enabled,
        'message': 'Sync Server Time enabled (sync_server created).' if enabled
                   else 'Sync Server Time disabled (sync_server removed).',
    })
    response.headers['Cache-Control'] = 'no-store'
    return response


def _run(cmd):
    """Run a shell command and return (success, stdout, stderr)."""
    try:
        result = subprocess.run(
            cmd, shell=True, capture_output=True, text=True, timeout=30
        )
        return result.returncode == 0, result.stdout.strip(), result.stderr.strip()
    except subprocess.TimeoutExpired:
        return False, '', 'Command timed out'


def _safe_filename(name):
    """Validate filename to prevent path traversal. Allows any extension or no extension."""
    name = os.path.basename(name)
    if not name or '..' in name or '/' in name or '\\' in name:
        return None
    return name


# ---------------------------------------------------------------------------
# Auto Read (crontab management)
# ---------------------------------------------------------------------------

@amr_bp.route('/api/amr/autoread')
@login_required
def get_autoread():
    """Get auto read status and interval from crontab."""
    ok, out, _ = _run('crontab -l')
    enabled = False
    interval = '30'

    if ok:
        for line in out.splitlines():
            if PROCESS_METERS_SCRIPT in line and not line.strip().startswith('#'):
                enabled = True
                # Parse interval from */N pattern
                match = re.match(r'^\*/(\d+)', line.strip())
                if match:
                    interval = match.group(1)
                break

    return jsonify({'enabled': enabled, 'interval': interval})


@amr_bp.route('/api/amr/autoread', methods=['POST'])
@manage_required
def set_autoread():
    """Enable/disable auto read in crontab."""
    data = request.get_json(silent=True) or {}
    enable = data.get('enable', False)
    interval = str(data.get('interval', '30')).strip()

    # Validate interval
    if enable and (not interval.isdigit() or int(interval) < 1):
        return jsonify({'error': 'Interval must be a positive integer (minutes)'}), 400

    # Read current crontab
    ok, current, _ = _run('crontab -l')
    lines = current.splitlines() if ok else []

    # Remove existing process_meters entries
    lines = [l for l in lines if PROCESS_METERS_SCRIPT not in l]

    if enable:
        cron_line = f'*/{interval} * * * * {PROCESS_METERS_SCRIPT}'
        lines.append(cron_line)

    # Write back crontab
    new_crontab = '\n'.join(lines) + '\n' if lines else ''
    result = subprocess.run(
        'crontab -', shell=True, input=new_crontab,
        capture_output=True, text=True, timeout=10
    )

    if result.returncode != 0:
        return jsonify({'error': f'Failed to update crontab: {result.stderr}'}), 500

    status = 'enabled' if enable else 'disabled'
    return jsonify({'message': f'Auto read {status} successfully'})


# ---------------------------------------------------------------------------
# Meter Batch File
# ---------------------------------------------------------------------------

@contextmanager
def _meter_batch_lock():
    """Serialize web workers using a stable sidecar, not the replaced CSV inode."""
    os.makedirs(os.path.dirname(METER_BATCH_FILE), exist_ok=True)
    with _meter_thread_lock, open(METER_BATCH_FILE + '.lock', 'a+b') as lock:
        try:
            import fcntl
        except ImportError:
            # Windows development tests use the thread lock; devices use flock.
            yield
        else:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def _write_meter_rows(rows, original_stat=None):
    """Atomically publish complete CSV rows, preserving existing access metadata."""
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode='w', newline='', dir=os.path.dirname(METER_BATCH_FILE), delete=False
        ) as f:
            temporary = f.name
            csv.writer(f).writerows(rows)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(temporary, stat.S_IMODE(original_stat.st_mode) if original_stat else 0o644)
        if original_stat and hasattr(os, 'geteuid') and os.geteuid() == 0:
            os.chown(temporary, original_stat.st_uid, original_stat.st_gid)
        os.replace(temporary, METER_BATCH_FILE)
    finally:
        if temporary is not None and os.path.exists(temporary):
            os.unlink(temporary)


@amr_bp.route('/api/amr/meters')
@login_required
def get_meters():
    """Load at most 32 valid entries and persist that limit for oversized files."""
    rows = []
    truncated = False
    try:
        try:
            os.stat(METER_BATCH_FILE)
        except FileNotFoundError:
            response = jsonify({'meters': [], 'truncated': False})
            response.headers['Cache-Control'] = 'no-store'
            return response
        with _meter_batch_lock():
            with open(METER_BATCH_FILE, 'r', newline='') as f:
                original_stat = os.fstat(f.fileno())
                for row in csv.reader(f):
                    if len(row) < 2:
                        continue
                    if len(rows) == MAX_METERS:
                        truncated = True
                        break
                    rows.append(row)
            if truncated:
                _write_meter_rows(rows, original_stat)
    except (OSError, csv.Error, UnicodeError):
        return jsonify({'error': 'Unable to load or trim meter_batch.csv; check the file and permissions'}), 500

    meters = [{
        'meter_sn': row[0].strip(),
        'config_file': row[1].strip(),
        'alt_address': row[2].strip() if len(row) >= 3 else '',
    } for row in rows]
    response = jsonify({'meters': meters, 'truncated': truncated})
    response.headers['Cache-Control'] = 'no-store'
    return response


@amr_bp.route('/api/amr/meters', methods=['POST'])
@manage_required
def save_meters():
    """Save meter batch list to CSV."""
    data = request.get_json(silent=True) or {}
    meters = data.get('meters', [])

    if len(meters) > MAX_METERS:
        return jsonify({'error': 'Maximum of 32 meters allowed'}), 400

    rows = [[
        m.get('meter_sn', '').strip(),
        m.get('config_file', '').strip(),
        m.get('alt_address', '').strip(),
    ] for m in meters]
    try:
        with _meter_batch_lock():
            try:
                original_stat = os.stat(METER_BATCH_FILE)
            except FileNotFoundError:
                original_stat = None
            _write_meter_rows(rows, original_stat)
    except OSError:
        return jsonify({'error': 'Unable to save meter_batch.csv; check directory permissions'}), 500

    return jsonify({'message': f'Saved {len(meters)} meter(s) to meter_batch.csv'})


@amr_bp.route('/api/amr/meter-configs')
@login_required
def get_meter_configs():
    """List *.cfg files in meter directory, excluding defaults.cfg."""
    configs = []
    try:
        for f in sorted(os.listdir(AMR_METER_DIR)):
            if f.endswith('.cfg') and f != 'defaults.cfg':
                configs.append(f)
    except FileNotFoundError:
        pass

    return jsonify({'configs': configs})


# ---------------------------------------------------------------------------
# Config File Editor
# ---------------------------------------------------------------------------

@amr_bp.route('/api/amr/configs')
@login_required
def list_configs():
    """List all files in the meter directory."""
    configs = []
    try:
        for f in sorted(os.listdir(AMR_METER_DIR)):
            if os.path.isfile(os.path.join(AMR_METER_DIR, f)):
                configs.append(f)
    except FileNotFoundError:
        pass

    return jsonify({'configs': configs})


@amr_bp.route('/api/amr/configs/<filename>')
@login_required
def get_config(filename):
    """Read a config file's content."""
    safe = _safe_filename(filename)
    if not safe:
        return jsonify({'error': 'Invalid filename'}), 400

    filepath = os.path.join(AMR_METER_DIR, safe)
    try:
        with open(filepath, 'r') as f:
            content = f.read()
        return jsonify({'filename': safe, 'content': content})
    except FileNotFoundError:
        return jsonify({'error': 'File not found'}), 404


@amr_bp.route('/api/amr/configs/<filename>', methods=['POST'])
@manage_required
def save_config(filename):
    """Save (overwrite) a config file."""
    safe = _safe_filename(filename)
    if not safe:
        return jsonify({'error': 'Invalid filename'}), 400

    data = request.get_json(silent=True) or {}
    content = data.get('content', '')

    filepath = os.path.join(AMR_METER_DIR, safe)
    os.makedirs(AMR_METER_DIR, exist_ok=True)

    with open(filepath, 'w') as f:
        f.write(content)

    return jsonify({'message': f'{safe} saved successfully'})


@amr_bp.route('/api/amr/configs', methods=['POST'])
@manage_required
def save_config_as():
    """Save As: create a new config file."""
    data = request.get_json(silent=True) or {}
    filename = data.get('filename', '').strip()
    content = data.get('content', '')

    if not filename:
        return jsonify({'error': 'Filename is required'}), 400

    safe = _safe_filename(filename)
    if not safe:
        return jsonify({'error': 'Invalid filename'}), 400

    filepath = os.path.join(AMR_METER_DIR, safe)
    os.makedirs(AMR_METER_DIR, exist_ok=True)

    with open(filepath, 'w') as f:
        f.write(content)

    return jsonify({'message': f'{safe} created successfully', 'filename': safe})


@amr_bp.route('/api/amr/configs/<filename>', methods=['DELETE'])
@manage_required
def delete_config(filename):
    """Delete a config file."""
    safe = _safe_filename(filename)
    if not safe:
        return jsonify({'error': 'Invalid filename'}), 400

    filepath = os.path.join(AMR_METER_DIR, safe)

    if not os.path.exists(filepath):
        return jsonify({'error': 'File not found'}), 404

    os.remove(filepath)
    return jsonify({'message': f'{safe} deleted successfully'})
