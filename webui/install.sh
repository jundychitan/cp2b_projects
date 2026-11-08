#!/bin/bash
# AMR Gateway Web UI - Installation Script
# Run with sudo on the CP2B gateway (Ubuntu 24.04 LTS).

set -e

REPO_DIR="$(cd "$(dirname "$0")" && pwd -P)"
source "$REPO_DIR/scripts/deployment_common.sh"

require_deployment_host
require_deployment_source
command -v apt-get >/dev/null || deployment_error "apt-get is required (Armbian/Debian/Ubuntu)."

WAS_RUNNING=false
if systemctl is-active --quiet amr_gateway_webui.service; then
    WAS_RUNNING=true
fi

echo "=== AMR Gateway Web UI Installer ==="

# Install system dependencies
echo "Installing system dependencies..."
apt-get update
apt-get install -y python3 python3-pip python3-venv rsync
python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' \
    || deployment_error "Python 3.11 or newer is required."

# Create application directory and copy files
echo "Setting up application directory..."
copy_application

# Create virtual environment
echo "Creating Python virtual environment..."
python3 -m venv "$APP_DIR/venv"
"$APP_DIR/venv/bin/pip" install --upgrade pip
"$APP_DIR/venv/bin/pip" install -r "$APP_DIR/requirements.txt"

# Create required directories on the device
echo "Creating required directories..."
mkdir -p "$PROJECTS_DIR/config_file"
mkdir -p "$PROJECTS_DIR/amr/meter"
mkdir -p "$PROJECTS_DIR/amr/upload"
mkdir -p "$PROJECTS_DIR/amr/scripts"
mkdir -p "$PROJECTS_DIR/tcp_client"

# Always install the current unit before reloading or restarting.
echo "Installing web UI systemd service..."
install_webui_unit
systemctl enable amr_gateway_webui.service
warn_legacy_network_helper
if [[ "$WAS_RUNNING" == true ]]; then
    echo "Restarting the running web UI with the updated unit..."
    systemctl restart amr_gateway_webui.service
fi

echo ""
echo "=== Installation Complete ==="
echo "Start the service:  systemctl start amr_gateway_webui"
echo "Check status:       systemctl status amr_gateway_webui"
echo "Access the UI:      http://<device-ip>:8080"
echo "Accounts:           admin (full), dec (operational), amr (read-only)"
echo "Initial passwords:  See README.md; existing passwords are preserved."
echo "Network profiles:   Unchanged; legacy eth0-apply helper is not enabled by this installer."
echo ""
