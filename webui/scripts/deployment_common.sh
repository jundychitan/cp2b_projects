#!/bin/bash
# Shared by install.sh and update.sh; source from a trusted checkout.

APP_DIR="/opt/amr_gateway_webui"
SYSTEMD_DIR="/etc/systemd/system"
PROJECTS_DIR="/home/admin/projects"

deployment_error() {
    printf 'ERROR: %s\n' "$*" >&2
    exit 1
}

require_deployment_host() {
    [[ "$(uname -s)" == Linux ]] || deployment_error "Run this script on the Linux device, not Windows."
    [[ "$(id -u)" == 0 ]] || deployment_error "Run this script as root (sudo bash $0)."
    command -v systemctl >/dev/null || deployment_error "systemd/systemctl is required."
}

require_deployment_source() {
    local file
    for file in app.py auth.py config.py requirements.txt amr_gateway_webui.service \
                templates/base.html static/js/app.js scripts/deployment_common.sh; do
        [[ -f "$REPO_DIR/$file" ]] || deployment_error "Incomplete source tree: missing $file."
    done
}

copy_application() {
    mkdir -p "$APP_DIR"
    if [[ "$(cd "$REPO_DIR" && pwd -P)" == "$(cd "$APP_DIR" && pwd -P)" ]]; then
        printf 'Source is the installation directory; skipping file copy.\n'
        return
    fi
    # No --delete: retain target-only files, including all local credentials.
    rsync -a --exclude='venv/' --exclude='.venv/' --exclude='users.json' \
        --exclude='.env' --exclude='.env.*' --exclude='__pycache__/' \
        --exclude='*.pyc' --exclude='*.pyo' --exclude='*.log' --exclude='.git/' \
        "$REPO_DIR/" "$APP_DIR/"
}

install_webui_unit() {
    install -d -m 0755 "$SYSTEMD_DIR"
    install -m 0644 "$APP_DIR/amr_gateway_webui.service" "$SYSTEMD_DIR/amr_gateway_webui.service"
    systemctl daemon-reload
}

warn_legacy_network_helper() {
    if systemctl is-enabled --quiet eth0-apply.service; then
        printf '%s\n' "WARNING: Existing eth0-apply.service is enabled and has been left unchanged."
        printf '%s\n' "Review its profile/interface settings; the legacy helper assumes netplan-eth0/eth0."
    fi
}
