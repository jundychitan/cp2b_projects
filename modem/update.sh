#!/bin/bash
# update.sh — Deploy/update the internet-watchdog on this system.
#
# Installs the latest internet-watchdog.sh (now with rotating, timestamped
# logging) and its systemd unit, backs up whatever is currently installed,
# then reloads and restarts the service so the changes take effect.
#
# Usage: sudo ./update.sh

set -euo pipefail

# --- Configuration ---------------------------------------------------------
# Directory containing this script and the source files (the repo checkout).
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Deployment targets (must match ExecStart= in internet-watchdog.service).
INSTALL_DIR="/projects/modem"
SERVICE_NAME="internet-watchdog.service"
SERVICE_DIR="/etc/systemd/system"
LOG_FILE="/var/log/internet-watchdog.log"

SRC_SCRIPT="$SCRIPT_DIR/internet-watchdog.sh"
SRC_SERVICE="$SCRIPT_DIR/$SERVICE_NAME"
DST_SCRIPT="$INSTALL_DIR/internet-watchdog.sh"
DST_SERVICE="$SERVICE_DIR/$SERVICE_NAME"

BACKUP_SUFFIX=".bak-$(date '+%Y%m%d-%H%M%S')"

log()  { echo "[update] $*"; }
die()  { echo "[update] ERROR: $*" >&2; exit 1; }

# Install src -> dst with the given mode. Line endings are normalized to LF so
# scripts edited on Windows still run on the Linux target; an existing, differing
# dst is backed up first, and unchanged files are left untouched.
install_file() {
    local src="$1" dst="$2" mode="$3"
    local tmp
    tmp="$(mktemp)"
    tr -d '\r' < "$src" > "$tmp"

    if [ -f "$dst" ] && cmp -s "$tmp" "$dst"; then
        log "Unchanged: $dst"
        rm -f "$tmp"
        return 0
    fi
    if [ -f "$dst" ]; then
        log "Backing up existing $dst -> ${dst}${BACKUP_SUFFIX}"
        cp -p "$dst" "${dst}${BACKUP_SUFFIX}"
    fi

    cp "$tmp" "$dst"
    chmod "$mode" "$dst"
    rm -f "$tmp"
}

# --- Pre-flight checks -----------------------------------------------------
# Must run as root to write to /projects, /etc/systemd, and manage services.
[ "$(id -u)" -eq 0 ]                    || die "This script must be run as root (try: sudo $0)"
[ -f "$SRC_SCRIPT" ]                    || die "Missing source script: $SRC_SCRIPT"
[ -f "$SRC_SERVICE" ]                   || die "Missing source service unit: $SRC_SERVICE"
command -v systemctl >/dev/null 2>&1    || die "systemctl not found; this system is not systemd-based"

# --- Deploy ----------------------------------------------------------------
log "Ensuring install directory exists -> $INSTALL_DIR"
mkdir -p "$INSTALL_DIR"

log "Installing watchdog script -> $DST_SCRIPT"
install_file "$SRC_SCRIPT" "$DST_SCRIPT" 0755

log "Installing systemd unit -> $DST_SERVICE"
install_file "$SRC_SERVICE" "$DST_SERVICE" 0644

# Ensure the log directory exists (the script also does this at runtime).
log "Ensuring log directory exists -> $(dirname "$LOG_FILE")"
mkdir -p "$(dirname "$LOG_FILE")"

# --- Apply -----------------------------------------------------------------
log "Reloading systemd unit definitions..."
systemctl daemon-reload

log "Enabling $SERVICE_NAME (start on boot)..."
systemctl enable "$SERVICE_NAME" >/dev/null

log "Restarting $SERVICE_NAME to apply changes..."
systemctl restart "$SERVICE_NAME"

# --- Report ----------------------------------------------------------------
log "Done. Current status:"
systemctl --no-pager --full status "$SERVICE_NAME" || true

log "Follow service output: journalctl -u $SERVICE_NAME -f"
log "Watchdog log file:     $LOG_FILE (rotates at 10 MB, keeps 5 copies)"
