#!/bin/bash
# AMR Gateway Web UI - Quick Update Script
# Copies updated files without reinstalling dependencies.
# Run as root on the Orange Pi Zero H3 (Armbian)

set -e

APP_DIR="/opt/amr_gateway_webui"
REPO_DIR="$(cd "$(dirname "$0")" && pwd)"

echo "=== AMR Gateway Web UI - Quick Update ==="

# Copy updated files (preserve users.json)
echo "Copying updated files..."
rsync -av --exclude='venv' --exclude='users.json' --exclude='__pycache__' \
      --exclude='.git' --exclude='.gitignore' \
      "$REPO_DIR/" "$APP_DIR/"

# Restart the service
echo "Restarting service..."
systemctl restart amr_gateway_webui

echo ""
echo "=== Update Complete ==="
echo "Status: $(systemctl is-active amr_gateway_webui)"
echo ""
