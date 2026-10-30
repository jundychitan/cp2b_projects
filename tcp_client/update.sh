#!/bin/bash
# ============================================================================
# Connection Manager Updater
# ============================================================================
# Updates only changed files and restarts the service if needed.
#
# Usage:
#   sudo ./update.sh                                     # Auto-detect install dir
#   sudo ./update.sh --install-dir /projects/tcp_client   # Custom directory
#   sudo ./update.sh --dry-run                            # Show what would change
# ============================================================================

APP_NAME="connection-manager"
INSTALL_DIR=""
SERVICE_FILE="/etc/systemd/system/${APP_NAME}.service"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
DRY_RUN=0

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

log_info()  { echo -e "${GREEN}[INFO]${NC}  $1"; }
log_warn()  { echo -e "${YELLOW}[WARN]${NC}  $1"; }
log_error() { echo -e "${RED}[ERROR]${NC} $1"; }

# ---------------------------------------------------------------------------
# Parse arguments
# ---------------------------------------------------------------------------
while [ $# -gt 0 ]; do
    case "$1" in
        --install-dir)
            INSTALL_DIR="$2"
            shift
            ;;
        --install-dir=*)
            INSTALL_DIR="${1#*=}"
            ;;
        --dry-run)
            DRY_RUN=1
            ;;
        --help|-h)
            echo "Usage: sudo $0 [OPTIONS]"
            echo ""
            echo "Options:"
            echo "  --install-dir DIR  Target install directory (auto-detected if omitted)"
            echo "  --dry-run          Show what would be updated without making changes"
            echo "  --help             Show this help"
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

# ---------------------------------------------------------------------------
# Pre-flight checks
# ---------------------------------------------------------------------------
if [ "$(id -u)" -ne 0 ] && [ $DRY_RUN -eq 0 ]; then
    log_error "This script must be run as root (use sudo), or use --dry-run"
    exit 1
fi

# Auto-detect install directory from systemd service file
if [ -z "${INSTALL_DIR}" ]; then
    if [ -f "${SERVICE_FILE}" ]; then
        INSTALL_DIR="$(grep '^WorkingDirectory=' "${SERVICE_FILE}" | cut -d= -f2)"
    fi
    if [ -z "${INSTALL_DIR}" ]; then
        INSTALL_DIR="/opt/tcp_client"
    fi
fi

if [ ! -d "${INSTALL_DIR}" ]; then
    log_error "Install directory not found: ${INSTALL_DIR}"
    log_error "Run install.sh first, or specify --install-dir"
    exit 1
fi

# Check source files exist
for f in rs485_tcp_client.py connection_manager.py; do
    if [ ! -f "${SCRIPT_DIR}/${f}" ]; then
        log_error "Missing source file: ${SCRIPT_DIR}/${f}"
        exit 1
    fi
done

# Skip if source == install dir
real_src="$(cd "${SCRIPT_DIR}" && pwd -P)"
real_dst="$(cd "${INSTALL_DIR}" && pwd -P)"
if [ "${real_src}" = "${real_dst}" ]; then
    log_info "Source and install directories are the same — nothing to update"
    exit 0
fi

# ---------------------------------------------------------------------------
# Compare and update
# ---------------------------------------------------------------------------
FILES_TO_CHECK="rs485_tcp_client.py connection_manager.py requirements.txt setup_ssh_tunnel.sh"
UPDATED=0
UPDATED_LIST=""

for f in ${FILES_TO_CHECK}; do
    src="${SCRIPT_DIR}/${f}"
    dst="${INSTALL_DIR}/${f}"

    # Skip if source file doesn't exist
    [ ! -f "${src}" ] && continue

    # New file or changed file
    if [ ! -f "${dst}" ]; then
        if [ $DRY_RUN -eq 1 ]; then
            log_info "[DRY-RUN] Would add: ${f}"
        else
            cp "${src}" "${dst}"
            log_info "Added: ${f}"
        fi
        UPDATED=1
        UPDATED_LIST="${UPDATED_LIST} ${f}"
    elif ! cmp -s "${src}" "${dst}"; then
        if [ $DRY_RUN -eq 1 ]; then
            log_info "[DRY-RUN] Would update: ${f}"
            diff --brief "${dst}" "${src}" 2>/dev/null || true
        else
            cp "${src}" "${dst}"
            log_info "Updated: ${f}"
        fi
        UPDATED=1
        UPDATED_LIST="${UPDATED_LIST} ${f}"
    fi
done

# ---------------------------------------------------------------------------
# Restart service if needed
# ---------------------------------------------------------------------------
if [ $UPDATED -eq 1 ]; then
    if [ $DRY_RUN -eq 1 ]; then
        log_info "[DRY-RUN] Would restart ${APP_NAME} service"
        echo ""
        log_info "Changed files:${UPDATED_LIST}"
    else
        echo ""
        log_info "Changed files:${UPDATED_LIST}"

        # Restart service if it exists and is active
        if systemctl is-enabled --quiet "${APP_NAME}" 2>/dev/null; then
            systemctl restart "${APP_NAME}"
            sleep 2
            if systemctl is-active --quiet "${APP_NAME}"; then
                log_info "Service restarted successfully"
            else
                log_error "Service failed to start. Check: journalctl -u ${APP_NAME} -n 20"
                exit 1
            fi
        else
            log_warn "Service ${APP_NAME} is not enabled — files updated but service not restarted"
        fi
    fi
else
    log_info "All files are up to date — no changes needed"
fi
