#!/bin/bash
# AMR Gateway Web UI - Quick Update Script
# Copies updated files without reinstalling dependencies.
# Run with sudo on the CP2B gateway (Ubuntu 24.04 LTS).

set -e

REPO_DIR="$(cd "$(dirname "$0")" && pwd -P)"
source "$REPO_DIR/scripts/deployment_common.sh"

require_deployment_host
require_deployment_source
command -v rsync >/dev/null || deployment_error "rsync is missing; run the full install.sh first."
[[ -x "$APP_DIR/venv/bin/gunicorn" ]] \
    || deployment_error "No installed virtual environment; run the full install.sh first."

echo "=== AMR Gateway Web UI - Quick Update ==="

# Copy updated files (preserve users.json)
echo "Copying updated files..."
copy_application

echo "Installing the updated service unit and reloading systemd..."
install_webui_unit
warn_legacy_network_helper

# Restart the service
echo "Restarting service..."
systemctl restart amr_gateway_webui.service

echo ""
echo "=== Update Complete ==="
echo "Status: $(systemctl is-active amr_gateway_webui)"
echo "If requirements.txt changed, run the full install.sh to update dependencies."
echo ""
