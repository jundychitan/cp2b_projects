#!/bin/bash
set -e

# ============================================================================
# Connection Manager Installer for Ubuntu 24.04 LTS
# ============================================================================
# Usage:
#   sudo ./install.sh                                    # Install to /opt/tcp_client
#   sudo ./install.sh --install-dir /projects/tcp_client  # Custom directory
#   sudo ./install.sh --install-dir /home/admin/projects/tcp_client
#   sudo ./install.sh --uninstall                         # Remove everything
#   sudo ./install.sh --status                            # Show service status
# ============================================================================

APP_NAME="connection-manager"
INSTALL_DIR="/opt/tcp_client"
SERVICE_FILE="/etc/systemd/system/${APP_NAME}.service"
LOG_DIR="/var/log/tcp_client"
PID_DIR="/var/run"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
NET_INTERFACE=""

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

log_info()  { echo -e "${GREEN}[INFO]${NC}  $1"; }
log_warn()  { echo -e "${YELLOW}[WARN]${NC}  $1"; }
log_error() { echo -e "${RED}[ERROR]${NC} $1"; }

# ---------------------------------------------------------------------------
# Pre-flight checks
# ---------------------------------------------------------------------------
check_root() {
    if [ "$(id -u)" -ne 0 ]; then
        log_error "This script must be run as root (use sudo)"
        exit 1
    fi
}

detect_interface() {
    # Auto-detect the primary network interface if not specified
    if [ -n "${NET_INTERFACE}" ]; then
        # User specified - verify it exists
        if [ ! -d "/sys/class/net/${NET_INTERFACE}" ]; then
            log_error "Network interface '${NET_INTERFACE}' not found"
            echo "  Available interfaces:"
            ls /sys/class/net/ | grep -v lo | sed 's/^/    /'
            exit 1
        fi
        log_info "Using network interface: ${NET_INTERFACE}"
        return
    fi

    # Auto-detect: prefer eth0, end0, then first non-lo interface
    for iface in eth0 end0; do
        if [ -d "/sys/class/net/${iface}" ]; then
            NET_INTERFACE="${iface}"
            log_info "Auto-detected network interface: ${NET_INTERFACE}"
            return
        fi
    done

    # Fallback: first non-lo interface
    for iface in /sys/class/net/*; do
        iface="$(basename "${iface}")"
        if [ "${iface}" != "lo" ]; then
            NET_INTERFACE="${iface}"
            log_info "Auto-detected network interface: ${NET_INTERFACE}"
            return
        fi
    done

    log_warn "Could not auto-detect network interface, defaulting to eth0"
    NET_INTERFACE="eth0"
}

check_ubuntu() {
    if [ -f /etc/os-release ]; then
        . /etc/os-release
        log_info "Detected OS: ${PRETTY_NAME}"
    else
        log_warn "Could not detect OS version"
    fi
}

check_source_files() {
    local missing=0
    for f in rs485_tcp_client.py connection_manager.py; do
        if [ ! -f "${SCRIPT_DIR}/${f}" ]; then
            log_error "Missing required file: ${f}"
            missing=1
        fi
    done
    if [ $missing -eq 1 ]; then
        log_error "Run this script from the tcp_client source directory"
        exit 1
    fi
}

# ---------------------------------------------------------------------------
# Uninstall
# ---------------------------------------------------------------------------
do_uninstall() {
    log_info "Uninstalling ${APP_NAME}..."

    # Stop and disable service
    if systemctl is-active --quiet "${APP_NAME}" 2>/dev/null; then
        log_info "Stopping service..."
        systemctl stop "${APP_NAME}"
    fi
    if systemctl is-enabled --quiet "${APP_NAME}" 2>/dev/null; then
        systemctl disable "${APP_NAME}"
    fi

    # Remove service file
    if [ -f "${SERVICE_FILE}" ]; then
        rm -f "${SERVICE_FILE}"
        systemctl daemon-reload
        log_info "Removed systemd service"
    fi

    # Remove install directory
    if [ -d "${INSTALL_DIR}" ]; then
        rm -rf "${INSTALL_DIR}"
        log_info "Removed ${INSTALL_DIR}"
    fi

    # Remove log directory
    if [ -d "${LOG_DIR}" ]; then
        rm -rf "${LOG_DIR}"
        log_info "Removed ${LOG_DIR}"
    fi

    # Remove PID file
    rm -f "${PID_DIR}/${APP_NAME}.pid"

    log_info "Uninstall complete"
    exit 0
}

# ---------------------------------------------------------------------------
# Status
# ---------------------------------------------------------------------------
do_status() {
    echo "========================================="
    echo " Connection Manager Status"
    echo "========================================="

    if [ -d "${INSTALL_DIR}" ]; then
        echo -e "Install dir:  ${GREEN}${INSTALL_DIR}${NC} (exists)"
    else
        echo -e "Install dir:  ${RED}${INSTALL_DIR}${NC} (not found)"
    fi

    if systemctl is-active --quiet "${APP_NAME}" 2>/dev/null; then
        echo -e "Service:      ${GREEN}running${NC}"
    else
        echo -e "Service:      ${RED}stopped${NC}"
    fi

    if systemctl is-enabled --quiet "${APP_NAME}" 2>/dev/null; then
        echo -e "Auto-start:   ${GREEN}enabled${NC}"
    else
        echo -e "Auto-start:   ${YELLOW}disabled${NC}"
    fi

    if [ -f "${INSTALL_DIR}/client_config.json" ]; then
        echo -e "Config:       ${GREEN}present${NC}"
    else
        echo -e "Config:       ${RED}missing${NC}"
    fi

    echo ""
    systemctl status "${APP_NAME}" --no-pager 2>/dev/null || true
    exit 0
}

# ---------------------------------------------------------------------------
# Install / Update
# ---------------------------------------------------------------------------
do_install() {
    log_info "Installing ${APP_NAME}..."

    # 1. System dependencies
    log_info "Checking system dependencies..."
    apt-get update -qq
    apt-get install -y -qq python3 python3-pip python3-serial socat > /dev/null 2>&1
    log_info "System dependencies OK"

    # 2. Python dependencies
    log_info "Installing Python dependencies..."
    pip3 install --quiet --break-system-packages pyserial 2>/dev/null || \
        pip3 install --quiet pyserial 2>/dev/null || \
        log_warn "pip install pyserial failed (may already be installed via apt)"

    # 3. Create directories
    mkdir -p "${INSTALL_DIR}"
    mkdir -p "${LOG_DIR}"
    log_info "Directories created"

    # 4. Copy application files
    local is_update=0
    if [ -f "${INSTALL_DIR}/connection_manager.py" ]; then
        is_update=1
        log_info "Existing installation detected - updating..."
    fi

    # Resolve real paths to detect if source == install dir
    real_src="$(cd "${SCRIPT_DIR}" && pwd -P)"
    real_dst="$(mkdir -p "${INSTALL_DIR}" && cd "${INSTALL_DIR}" && pwd -P)"

    if [ "${real_src}" = "${real_dst}" ]; then
        log_info "Source and install directories are the same — skipping file copy"
    else
        cp "${SCRIPT_DIR}/rs485_tcp_client.py" "${INSTALL_DIR}/"
        cp "${SCRIPT_DIR}/connection_manager.py" "${INSTALL_DIR}/"
        cp "${SCRIPT_DIR}/requirements.txt" "${INSTALL_DIR}/"

        # Only copy config if it doesn't exist (preserve existing config on update)
        if [ ! -f "${INSTALL_DIR}/client_config.json" ]; then
            if [ -f "${SCRIPT_DIR}/copy_to_client_config.json" ]; then
                cp "${SCRIPT_DIR}/copy_to_client_config.json" "${INSTALL_DIR}/client_config.json"
                log_info "Default config installed"
            elif [ -f "${SCRIPT_DIR}/client_config.json" ]; then
                cp "${SCRIPT_DIR}/client_config.json" "${INSTALL_DIR}/client_config.json"
                log_info "Config copied from source"
            else
                log_warn "No config template found - you must create ${INSTALL_DIR}/client_config.json"
            fi
        else
            log_info "Existing config preserved"
        fi

        # Copy SSH tunnel script if present
        if [ -f "${SCRIPT_DIR}/setup_ssh_tunnel.sh" ]; then
            cp "${SCRIPT_DIR}/setup_ssh_tunnel.sh" "${INSTALL_DIR}/"
            chmod +x "${INSTALL_DIR}/setup_ssh_tunnel.sh"
        fi

        log_info "Application files installed to ${INSTALL_DIR}"
    fi

    chmod +x "${INSTALL_DIR}/connection_manager.py"
    chmod +x "${INSTALL_DIR}/rs485_tcp_client.py"

    # 5. Create systemd service
    log_info "Configuring systemd service..."
    cat > "${SERVICE_FILE}" <<EOF
[Unit]
Description=RS485 TCP Client Connection Manager
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
ExecStart=/usr/bin/python3 ${INSTALL_DIR}/connection_manager.py \\
    --config ${INSTALL_DIR}/client_config.json \\
    --log-file ${LOG_DIR}/connection_manager.log \\
    --pid-file ${PID_DIR}/${APP_NAME}.pid \\
    --interface ${NET_INTERFACE}
WorkingDirectory=${INSTALL_DIR}
Restart=on-failure
RestartSec=5
StartLimitIntervalSec=60
StartLimitBurst=5
StandardOutput=journal
StandardError=journal
SyslogIdentifier=${APP_NAME}

# Hardening
NoNewPrivileges=true
ProtectSystem=strict
ReadWritePaths=${INSTALL_DIR} ${LOG_DIR} ${PID_DIR}
ProtectHome=$(echo "${INSTALL_DIR}" | grep -q '^/home' && echo 'false' || echo 'true')
PrivateTmp=true

[Install]
WantedBy=multi-user.target
EOF

    systemctl daemon-reload
    systemctl enable "${APP_NAME}"
    log_info "Systemd service enabled"

    # 6. Start or restart service
    if [ $is_update -eq 1 ]; then
        systemctl restart "${APP_NAME}"
        log_info "Service restarted"
    else
        systemctl start "${APP_NAME}"
        log_info "Service started"
    fi

    # 7. Wait and verify
    sleep 2
    if systemctl is-active --quiet "${APP_NAME}"; then
        log_info "Service is running"
    else
        log_error "Service failed to start. Check: journalctl -u ${APP_NAME} -n 20"
        exit 1
    fi

    # 8. Summary
    echo ""
    echo "========================================="
    echo " Installation Complete"
    echo "========================================="
    echo ""
    echo "  Install dir:  ${INSTALL_DIR}"
    echo "  Config:       ${INSTALL_DIR}/client_config.json"
    echo "  Log file:     ${LOG_DIR}/connection_manager.log"
    echo "  Interface:    ${NET_INTERFACE}"
    echo "  Service:      ${APP_NAME}"
    echo ""
    echo "  Useful commands:"
    echo "    systemctl status ${APP_NAME}"
    echo "    systemctl restart ${APP_NAME}"
    echo "    systemctl stop ${APP_NAME}"
    echo "    journalctl -u ${APP_NAME} -f"
    echo ""
}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
check_root

# Parse arguments
while [ $# -gt 0 ]; do
    case "$1" in
        --uninstall)
            do_uninstall
            ;;
        --status)
            do_status
            ;;
        --install-dir)
            INSTALL_DIR="$2"
            shift
            ;;
        --install-dir=*)
            INSTALL_DIR="${1#*=}"
            ;;
        --interface)
            NET_INTERFACE="$2"
            shift
            ;;
        --interface=*)
            NET_INTERFACE="${1#*=}"
            ;;
        --help|-h)
            echo "Usage: sudo $0 [OPTIONS]"
            echo ""
            echo "Options:"
            echo "  --install-dir DIR Install to DIR (default: /opt/tcp_client)"
            echo "                    Examples: /projects/tcp_client"
            echo "                              /home/admin/projects/tcp_client"
            echo "  --interface NAME  Network interface for MAC address (e.g. eth0, end0)"
            echo "                    Auto-detected if not specified"
            echo "  --uninstall       Remove connection manager completely"
            echo "  --status          Show current installation status"
            echo "  --help            Show this help"
            exit 0
            ;;
        *)
            log_error "Unknown option: $1"
            echo "Run '$0 --help' for usage"
            exit 1
            ;;
    esac
    shift
done

# Regenerate SERVICE_FILE path in case INSTALL_DIR changed
SERVICE_FILE="/etc/systemd/system/${APP_NAME}.service"
log_info "Install directory: ${INSTALL_DIR}"

check_ubuntu
check_source_files
detect_interface
do_install
