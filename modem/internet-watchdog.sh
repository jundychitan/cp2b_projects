#!/bin/bash
# internet-watchdog.sh — Reboot if internet connectivity is lost.
# Supports IP addresses, hostnames, and URLs as targets.

# Default targets (overridden by config file if it exists)
DEFAULT_TARGETS=("8.8.8.8" "1.1.1.1" "9.9.9.9")
TARGETS_FILE="/projects/config_file/watchdog_targets.cfg"

# Load targets from config file, fall back to defaults
if [ -f "$TARGETS_FILE" ]; then
    mapfile -t TARGETS < <(grep -v '^#' "$TARGETS_FILE" | grep -v '^$')
    if [ ${#TARGETS[@]} -eq 0 ]; then
TARGETS=("google.com" "yahoo.com")
    fi
else
TARGETS=("google.com" "yahoo.com")
fi

PING_COUNT=2
PING_TIMEOUT=5
CURL_TIMEOUT=10         # seconds for URL checks
CHECK_INTERVAL=30       # seconds between checks
FAIL_THRESHOLD=3        # consecutive failed checks before reboot
STARTUP_DELAY=120       # seconds to wait after boot before monitoring

# Logging configuration
LOG_FILE="${LOG_FILE:-/var/log/internet-watchdog.log}"
MAX_LOG_SIZE=$((10 * 1024 * 1024))   # rotate once the log reaches 10 MB
LOG_KEEP=5                           # number of rotated log files to keep

fail_count=0

# Rotate the log when it reaches MAX_LOG_SIZE, keeping LOG_KEEP old copies.
rotate_log() {
    [ -f "$LOG_FILE" ] || return 0
    local size
    size=$(stat -c %s "$LOG_FILE" 2>/dev/null \
        || stat -f %z "$LOG_FILE" 2>/dev/null \
        || wc -c < "$LOG_FILE" 2>/dev/null)
    [ "${size:-0}" -lt "$MAX_LOG_SIZE" ] && return 0

    local i
    for (( i = LOG_KEEP - 1; i >= 1; i-- )); do
        [ -f "${LOG_FILE}.$i" ] && mv -f "${LOG_FILE}.$i" "${LOG_FILE}.$((i + 1))"
    done
    mv -f "$LOG_FILE" "${LOG_FILE}.1"
}

# Print a timestamped message to stdout and append it to the rotating log file.
log() {
    local line="[$(date '+%Y-%m-%d %H:%M:%S')] $1"
    echo "$line"
    rotate_log
    echo "$line" >> "$LOG_FILE"
}

mkdir -p "$(dirname "$LOG_FILE")" 2>/dev/null

log "internet-watchdog started. Waiting ${STARTUP_DELAY}s for system to settle..."
sleep "$STARTUP_DELAY"
log "Monitoring started."

check_target() {
    local target="$1"
    # If target looks like a URL (starts with http:// or https://), use curl
    if [[ "$target" == http://* ]] || [[ "$target" == https://* ]]; then
        curl -sf --max-time "$CURL_TIMEOUT" -o /dev/null "$target" &>/dev/null
        return $?
    fi
    # Otherwise use ping (works for IPs and hostnames)
    ping -c "$PING_COUNT" -W "$PING_TIMEOUT" "$target" &>/dev/null
    return $?
}

check_internet() {
    for target in "${TARGETS[@]}"; do
        if check_target "$target"; then
            return 0
        fi
    done
    return 1
}

while true; do
    if check_internet; then
        if [ "$fail_count" -gt 0 ]; then
            log "Internet restored after $fail_count failed check(s)."
        fi
        fail_count=0
    else
        fail_count=$((fail_count + 1))
        log "Internet check failed ($fail_count/$FAIL_THRESHOLD)."

        if [ "$fail_count" -ge "$FAIL_THRESHOLD" ]; then
            log "FAIL_THRESHOLD ($FAIL_THRESHOLD) reached after $fail_count consecutive failed checks. Rebooting device..."
            stty 115200
            echo -e "AT+CFUN=1,1\r\n"> /dev/ttyUSB2
            sync
            reboot
        fi
    fi

    sleep "$CHECK_INTERVAL"
done
