"""Live Meter API routes: run process_meters.sh, parse meter log files."""

import os
import signal
import subprocess

from flask import Blueprint, jsonify
from flask_login import login_required

from config import PROCESS_METERS_SCRIPT

livemeter_bp = Blueprint('livemeter', __name__)

# Directory where the script saves per-meter log files
METER_LOG_DIR = '/tmp/amr/log'

# File-based process tracking (works across Gunicorn workers)
OUTPUT_FILE = '/tmp/amr_livemeter_output.log'
PID_FILE = '/tmp/amr_livemeter.pid'

# Parameters to extract from '&'-delimited meter data
PARAM_KEYS = [
    'meter_id', 'datetime', 'vrms_a', 'vrms_b', 'vrms_c',
    'irms_a', 'irms_b', 'irms_c', 'freq', 'watt', 'wh_total',
]


def _parse_meter_file(filepath):
    """Parse a single meter log file (fields delimited by '&').

    Returns a dict with the extracted parameters, or None if parsing fails.
    The file may contain key=value pairs or positional values separated by '&'.
    """
    try:
        with open(filepath, 'r') as f:
            content = f.read().strip()
    except (FileNotFoundError, PermissionError):
        return None

    if not content:
        return None

    fields = [f.strip() for f in content.split('&')]

    result = {}
    # Try key=value format first
    kv_found = False
    for field in fields:
        if '=' in field:
            key, _, val = field.partition('=')
            key = key.strip().lower()
            val = val.strip()
            if key in PARAM_KEYS:
                result[key] = val
                kv_found = True

    if kv_found and result:
        # Fill in missing keys with empty strings
        for k in PARAM_KEYS:
            result.setdefault(k, '')
        return result

    # Fallback: search all fields for values that look like our params
    # by looking for known key names anywhere in the delimited data
    for field in fields:
        field_lower = field.lower()
        for key in PARAM_KEYS:
            if field_lower.startswith(key):
                val = field[len(key):].lstrip(':= ').strip()
                if val:
                    result[key] = val

    if result:
        for k in PARAM_KEYS:
            result.setdefault(k, '')
        return result

    return None


def _is_script_running():
    """Check if the meter script is still running via PID file."""
    try:
        with open(PID_FILE, 'r') as f:
            pid = int(f.read().strip())
        # Check if process exists
        os.kill(pid, 0)
        return True
    except (FileNotFoundError, ValueError, ProcessLookupError, PermissionError):
        return False


@livemeter_bp.route('/api/livemeter/read', methods=['POST'])
@login_required
def read_meters():
    """Start process_meters.sh in the background."""
    if not os.path.exists(PROCESS_METERS_SCRIPT):
        return jsonify({'error': f'Script not found: {PROCESS_METERS_SCRIPT}'}), 404

    # Check if already running
    if _is_script_running():
        return jsonify({'error': 'Script is already running'}), 409

    # Handle Read LP flag
    from flask import request
    data = request.get_json(silent=True) or {}
    read_lp = data.get('read_lp', False)

    lp_flag_file = '/tmp/lp_read'
    if read_lp:
        subprocess.run(['touch', lp_flag_file], timeout=5)
    else:
        if os.path.exists(lp_flag_file):
            os.remove(lp_flag_file)

    # Clear output file
    with open(OUTPUT_FILE, 'w') as f:
        f.write('')

    # Run script in background using shell, write PID file
    # Uses stdbuf for line buffering, redirects output to file
    script_dir = os.path.dirname(PROCESS_METERS_SCRIPT)
    cmd = (
        f'cd {script_dir} && '
        f'stdbuf -oL bash {PROCESS_METERS_SCRIPT} '
        f'>> {OUTPUT_FILE} 2>&1 & '
        f'echo $! > {PID_FILE}'
    )
    subprocess.run(cmd, shell=True)

    return jsonify({'message': 'Script started'})


@livemeter_bp.route('/api/livemeter/output')
@login_required
def get_output():
    """Return current script output and running status."""
    output = ''
    try:
        with open(OUTPUT_FILE, 'r') as f:
            output = f.read()
    except FileNotFoundError:
        pass

    running = _is_script_running()

    return jsonify({'output': output, 'running': running})


@livemeter_bp.route('/api/livemeter/data')
@login_required
def get_meter_data():
    """Read all meter log files from /tmp/amr/log and return parsed data."""
    meters = []

    if not os.path.isdir(METER_LOG_DIR):
        return jsonify({'meters': meters})

    for filename in sorted(os.listdir(METER_LOG_DIR)):
        filepath = os.path.join(METER_LOG_DIR, filename)
        if os.path.isfile(filepath):
            parsed = _parse_meter_file(filepath)
            if parsed:
                meters.append(parsed)

    return jsonify({'meters': meters})
