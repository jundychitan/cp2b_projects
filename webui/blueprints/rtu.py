"""RTU Setting API routes."""

import os
import re
import subprocess

from flask import Blueprint, jsonify, request
from flask_login import login_required

from auth import admin_required
from config import ETH_CONNECTION_ID, LTE_CONNECTION_ID, ETH1_CONNECTION_ID, ETH_DEVICE_NAME, SERVER_IP_FILE, LOCATION_FILE, CONFIG_FILE_DIR

rtu_bp = Blueprint('rtu', __name__)


def _run(cmd):
    """Run a shell command and return (success, stdout, stderr)."""
    try:
        result = subprocess.run(
            cmd, shell=True, capture_output=True, text=True, timeout=30
        )
        return result.returncode == 0, result.stdout.strip(), result.stderr.strip()
    except subprocess.TimeoutExpired:
        return False, '', 'Command timed out'


def _read_file(path):
    """Read a text file, return content or empty string."""
    try:
        with open(path, 'r') as f:
            return f.read().strip()
    except (FileNotFoundError, PermissionError):
        return ''


def _write_file(path, content):
    """Write content to a text file, creating directories as needed."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w') as f:
        f.write(content.strip() + '\n')


def _cleanup_competing_profiles():
    """Delete auto-created connection profiles that compete with Eth0.

    NetworkManager may auto-create profiles like 'Wired connection 1' for
    the same ethernet device. These can override Eth0 settings on boot.
    """
    ok, out, _ = _run('nmcli -t -f NAME,TYPE con show')
    if not ok:
        return
    for line in out.splitlines():
        parts = line.split(':')
        if len(parts) >= 2:
            name = parts[0].strip()
            conn_type = parts[1].strip()
            # Delete auto-created ethernet profiles that aren't ours
            if conn_type == '802-3-ethernet' and name != ETH_CONNECTION_ID and name != LTE_CONNECTION_ID and name != ETH1_CONNECTION_ID:
                _run(f'nmcli con delete "{name}"')


@rtu_bp.route('/api/rtu/config')
@login_required
def get_config():
    """Get current RTU network settings and config files."""
    # Read IP method (auto = DHCP, manual = static)
    ok, out, _ = _run(f'nmcli -t -f ipv4.method con show {ETH_CONNECTION_ID}')
    method = 'auto'
    if ok and ':' in out:
        method = out.split(':', 1)[1].strip()

    # Read IP address/prefix
    ok, out, _ = _run(f'nmcli -t -f ipv4.addresses con show {ETH_CONNECTION_ID}')
    ip_address = ''
    subnet = '24'
    if ok and ':' in out:
        addr = out.split(':', 1)[1].strip()
        if '/' in addr:
            ip_address, subnet = addr.split('/', 1)

    # Read gateway
    ok, out, _ = _run(f'nmcli -t -f ipv4.gateway con show {ETH_CONNECTION_ID}')
    gateway = ''
    if ok and ':' in out:
        gateway = out.split(':', 1)[1].strip()

    # Read DNS servers
    ok, out, _ = _run(f'nmcli -t -f ipv4.dns con show {ETH_CONNECTION_ID}')
    dns = ''
    if ok and ':' in out:
        dns = out.split(':', 1)[1].strip()
        # nmcli may return multiple DNS separated by commas or spaces
        dns = dns.replace(' ', ', ')

    # Read MAC address of end0
    ok, out, _ = _run(f'cat /sys/class/net/{ETH_DEVICE_NAME}/address')
    mac_address = out.strip().upper() if ok else ''

    # Read config files
    server_ip = _read_file(SERVER_IP_FILE)
    location = _read_file(LOCATION_FILE)

    return jsonify({
        'method': method,
        'ip_address': ip_address,
        'subnet': subnet,
        'gateway': gateway,
        'dns': dns,
        'mac_address': mac_address,
        'server_ip': server_ip,
        'location': location,
    })


@rtu_bp.route('/api/rtu/network', methods=['POST'])
@admin_required
def set_network():
    """Set network configuration for end0 (DHCP or static)."""
    data = request.get_json(silent=True) or {}
    method = data.get('method', 'auto').strip()

    # Delete any competing auto-created profiles for the same interface
    # (e.g. "Wired connection 1") that may override Eth0 on boot
    _cleanup_competing_profiles()

    # Get DNS servers from request
    dns_servers = data.get('dns', '').strip()

    if method == 'auto':
        # Switch to DHCP
        ok, _, err = _run(
            f'nmcli con modify {ETH_CONNECTION_ID} '
            f'connection.interface-name {ETH_DEVICE_NAME} '
            f'ipv4.method auto ipv4.addresses "" ipv4.gateway "" ipv4.dns "" '
            f'connection.autoconnect yes '
            f'connection.autoconnect-priority 10'
        )
        if not ok:
            return jsonify({'error': f'Failed to set DHCP: {err}'}), 500
    else:
        # Validate inputs
        ip_address = data.get('ip_address', '').strip()
        subnet_raw = data.get('subnet', '24').strip()
        gateway = data.get('gateway', '').strip()

        if not ip_address:
            return jsonify({'error': 'IP address is required for static config'}), 400

        # Basic IP validation
        ip_pattern = r'^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}$'
        if not re.match(ip_pattern, ip_address):
            return jsonify({'error': 'Invalid IP address format'}), 400

        # Convert subnet: accept both IP mask (255.255.255.0) and CIDR prefix (/24 or 24)
        subnet = subnet_raw.lstrip('/')
        if re.match(ip_pattern, subnet):
            # Convert subnet mask to CIDR prefix length
            import ipaddress
            try:
                prefix = ipaddress.IPv4Network(f'0.0.0.0/{subnet}', strict=False).prefixlen
                subnet = str(prefix)
            except ValueError:
                return jsonify({'error': f'Invalid subnet mask: {subnet_raw}'}), 400
        elif not subnet.isdigit() or not (0 <= int(subnet) <= 32):
            return jsonify({'error': f'Invalid subnet: {subnet_raw}. Use CIDR (e.g. 24) or mask (e.g. 255.255.255.0)'}), 400

        cmd = (
            f'nmcli con modify {ETH_CONNECTION_ID} '
            f'connection.interface-name {ETH_DEVICE_NAME} '
            f'ipv4.addresses {ip_address}/{subnet} '
            f'ipv4.method manual '
            f'connection.autoconnect yes '
            f'connection.autoconnect-priority 10'
        )
        if gateway:
            if not re.match(ip_pattern, gateway):
                return jsonify({'error': 'Invalid gateway format'}), 400
            cmd += f' ipv4.gateway {gateway}'
        else:
            # Clear gateway if not provided
            cmd += ' ipv4.gateway ""'

        # Add DNS servers if provided
        if dns_servers:
            # Parse comma/space separated DNS IPs
            dns_list = [d.strip() for d in dns_servers.replace(',', ' ').split() if d.strip()]
            for d in dns_list:
                if not re.match(ip_pattern, d):
                    return jsonify({'error': f'Invalid DNS address: {d}'}), 400
            cmd += f' ipv4.dns "{" ".join(dns_list)}"'
        else:
            cmd += ' ipv4.dns ""'

        ok, _, err = _run(cmd)
        if not ok:
            return jsonify({'error': f'Failed to set static IP: {err}'}), 500

    # Reload profiles from disk so NetworkManager picks up changes
    _run('nmcli con reload')

    # Bring connection down and up to apply changes cleanly
    _run(f'nmcli con down {ETH_CONNECTION_ID}')
    _run(f'nmcli con up {ETH_CONNECTION_ID}')

    return jsonify({'message': 'Network settings saved successfully'})


@rtu_bp.route('/api/rtu/server', methods=['POST'])
@admin_required
def set_server_ip():
    """Save remote server IP."""
    data = request.get_json(silent=True) or {}
    server_ip = data.get('server_ip', '').strip()

    if not server_ip:
        return jsonify({'error': 'Server IP is required'}), 400

    _write_file(SERVER_IP_FILE, server_ip)
    return jsonify({'message': 'Server IP saved successfully'})


@rtu_bp.route('/api/rtu/location', methods=['POST'])
@admin_required
def set_location():
    """Save location."""
    data = request.get_json(silent=True) or {}
    location = data.get('location', '').strip()

    _write_file(LOCATION_FILE, location)
    return jsonify({'message': 'Location saved successfully'})
