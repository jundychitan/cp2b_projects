#!/bin/bash
# AMR Gateway Web UI - Installation Script
# Run as root on the Orange Pi Zero H3 (Armbian)

set -e

APP_DIR="/opt/amr_gateway_webui"
REPO_DIR="$(cd "$(dirname "$0")" && pwd)"

echo "=== AMR Gateway Web UI Installer ==="

# Install system dependencies
echo "Installing system dependencies..."
apt-get update
apt-get install -y python3 python3-pip python3-venv

# Create application directory and copy files
echo "Setting up application directory..."
mkdir -p "$APP_DIR"
cp -r "$REPO_DIR"/* "$APP_DIR"/
cp "$REPO_DIR"/.gitignore "$APP_DIR"/ 2>/dev/null || true

# Create virtual environment
echo "Creating Python virtual environment..."
python3 -m venv "$APP_DIR/venv"
"$APP_DIR/venv/bin/pip" install --upgrade pip
"$APP_DIR/venv/bin/pip" install -r "$APP_DIR/requirements.txt"

# Install systemd services
echo "Installing systemd services..."
cp "$APP_DIR/amr_gateway_webui.service" /etc/systemd/system/
cp "$APP_DIR/eth0-apply.service" /etc/systemd/system/
systemctl daemon-reload
systemctl enable amr_gateway_webui
systemctl enable eth0-apply

# Create required directories on the device
echo "Creating required directories..."
mkdir -p /projects/config_file
mkdir -p /projects/amr/meter
mkdir -p /projects/amr/upload
mkdir -p /projects/amr/scripts
mkdir -p /projects/tcp_client

echo ""
echo "=== Installation Complete ==="
echo "Start the service:  systemctl start amr_gateway_webui"
echo "Check status:       systemctl status amr_gateway_webui"
echo "Access the UI:      http://<device-ip>:8080"
echo "Default users:      admin/admin123 (full access), amr/amr123 (read-only)"
echo ""
