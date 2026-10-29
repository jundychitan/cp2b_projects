"""AMR Setting API routes: auto read, meter batch, config file editor."""

import csv
import io
import os
import re
import subprocess

from flask import Blueprint, jsonify, request
from flask_login import login_required

from auth import admin_required
from config import AMR_METER_DIR, AMR_UPLOAD_DIR, METER_BATCH_FILE, PROCESS_METERS_SCRIPT

amr_bp = Blueprint('amr', __name__)


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
@admin_required
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

@amr_bp.route('/api/amr/meters')
@login_required
def get_meters():
    """Read meter batch CSV and return as JSON list."""
    meters = []
    try:
        with open(METER_BATCH_FILE, 'r', newline='') as f:
            reader = csv.reader(f)
            for row in reader:
                if len(row) >= 3:
                    meters.append({
                        'meter_sn': row[0].strip(),
                        'config_file': row[1].strip(),
                        'alt_address': row[2].strip(),
                    })
                elif len(row) == 2:
                    meters.append({
                        'meter_sn': row[0].strip(),
                        'config_file': row[1].strip(),
                        'alt_address': '',
                    })
    except FileNotFoundError:
        pass

    return jsonify({'meters': meters})


@amr_bp.route('/api/amr/meters', methods=['POST'])
@admin_required
def save_meters():
    """Save meter batch list to CSV."""
    data = request.get_json(silent=True) or {}
    meters = data.get('meters', [])

    if len(meters) > 32:
        return jsonify({'error': 'Maximum of 32 meters allowed'}), 400

    os.makedirs(AMR_UPLOAD_DIR, exist_ok=True)

    with open(METER_BATCH_FILE, 'w', newline='') as f:
        writer = csv.writer(f)
        for m in meters:
            writer.writerow([
                m.get('meter_sn', '').strip(),
                m.get('config_file', '').strip(),
                m.get('alt_address', '').strip(),
            ])

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
@admin_required
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
@admin_required
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
@admin_required
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
