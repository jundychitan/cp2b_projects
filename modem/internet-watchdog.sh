#!/bin/bash
# internet-watchdog.sh — Reboot if internet connectivity is lost.
# Supports IP addresses, hostnames, and URLs as targets.

# Default targets (overridden by config file if it exists)
DEFAULT_TARGETS=("8.8.8.8" "1.1.1.1" "9.9.9.9")
TARGETS_FILE="${TARGETS_FILE:-/projects/config_file/watchdog_targets.cfg}"
LTE_SERVICE="lte-ecm.service"
SETUP_LOCK="/run/lte-ecm.lock"
SETUP_STAMP="/run/lte-ecm.generation"

load_targets() {
TARGETS=("google.com")
    local line
    if [ -f "$TARGETS_FILE" ]; then
        while IFS= read -r line || [[ -n "$line" ]]; do
            line="${line%$'\r'}"
            [[ "$line" =~ ^[[:space:]]*$ ]] && continue
            [[ "$line" =~ ^[[:space:]]*\# ]] && continue
            TARGETS+=("$line")
        done < "$TARGETS_FILE"
    fi
    if [ ${#TARGETS[@]} -eq 0 ]; then
TARGETS=("google.com")
    fi
}

PING_COUNT=2
PING_TIMEOUT=5
CURL_TIMEOUT=10         # seconds for URL checks
CHECK_INTERVAL=30       # seconds between checks
FAIL_THRESHOLD=3        # consecutive failed checks before reboot
STARTUP_DELAY=120       # grace period after startup or any LTE setup activity

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

check_target() {
    local target="$1"
    # If target looks like a URL (starts with http:// or https://), use curl
    if [[ "$target" == http://* ]] || [[ "$target" == https://* ]]; then
        timeout "$((CURL_TIMEOUT + 2))" curl -sf --max-time "$CURL_TIMEOUT" \
            -o /dev/null -- "$target" &>/dev/null
        return $?
    fi
    # Otherwise use ping (works for IPs and hostnames)
    timeout "$((PING_COUNT * PING_TIMEOUT + 2))" \
        ping -c "$PING_COUNT" -W "$PING_TIMEOUT" -- "$target" &>/dev/null
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

# Return success when setup is busy or its state cannot be safely determined.
setup_service_busy() {
    local state
    state=$(timeout 5 systemctl show "$LTE_SERVICE" --property=ActiveState --value) ||
        return 0
    case "$state" in
        inactive|failed|active) return 1 ;;
        *) return 0 ;;
    esac
}

setup_generation_changed() {
    local generation=none
    if [[ -r "$SETUP_STAMP" ]]; then
        IFS= read -r generation < "$SETUP_STAMP" || true
    fi
    if [[ "$generation" != "$last_generation" ]]; then
        last_generation="$generation"
        return 0
    fi
    return 1
}

setup_busy() {
    setup_service_busy && return 0
    # Linux flock(1) interoperates with the Python setup's fcntl.flock().
    if flock -n "$setup_lock_fd"; then
        flock -u "$setup_lock_fd"
        return 1
    fi
    return 0
}

request_reboot() {
    # Recheck under the same lock used by setup, immediately before recovery.
    if ! flock -n "$setup_lock_fd"; then
        log "LTE setup holds the lock; postponing recovery."
        return
    fi
    if setup_service_busy || setup_generation_changed; then
        log "LTE setup is starting or stopping; postponing recovery."
        flock -u "$setup_lock_fd"
        return
    fi
    log "FAIL_THRESHOLD ($FAIL_THRESHOLD) reached. Requesting host reboot."
    # Do not write AT commands here: the setup executable owns modem access.
    sync
    if ! reboot; then
        log "Host reboot request failed; monitoring will continue."
    fi
    flock -u "$setup_lock_fd"
}

monitor_once() {
    if setup_generation_changed || setup_busy; then
        if [ "$paused" -eq 0 ]; then
            log "LTE setup is in progress; pausing monitoring."
        fi
        paused=1
        fail_count=0
        grace_until=$((SECONDS + STARTUP_DELAY))
        return
    fi
    if [ "$paused" -eq 1 ]; then
        paused=0
        grace_until=$((SECONDS + STARTUP_DELAY))
        log "LTE setup finished; allowing ${STARTUP_DELAY}s to settle."
    fi
    [ "$SECONDS" -lt "$grace_until" ] && return

    local connected=0
    check_internet && connected=1
    # A manual setup may have started or finished while probes were running.
    if setup_generation_changed || setup_busy; then
        paused=1
        fail_count=0
        grace_until=$((SECONDS + STARTUP_DELAY))
        return
    fi
    if [ "$connected" -eq 1 ]; then
        if [ "$fail_count" -gt 0 ]; then
            log "Internet restored after $fail_count failed check(s)."
        fi
        fail_count=0
    else
        fail_count=$((fail_count + 1))
        log "Internet check failed ($fail_count/$FAIL_THRESHOLD)."
        if [ "$fail_count" -ge "$FAIL_THRESHOLD" ]; then
            request_reboot
            fail_count=0
            grace_until=$((SECONDS + STARTUP_DELAY))
        fi
    fi
}

main() {
    local tool
    for tool in systemctl flock timeout ping curl reboot sync; do
        command -v "$tool" >/dev/null || {
            printf 'Missing watchdog prerequisite: %s\n' "$tool" >&2
            return 1
        }
    done
    load_targets
    mkdir -p "$(dirname "$LOG_FILE")" || return 1
    exec {setup_lock_fd}>"$SETUP_LOCK" || return 1
    last_generation=none
    paused=0
    grace_until=$((SECONDS + STARTUP_DELAY))
    log "internet-watchdog started; allowing ${STARTUP_DELAY}s after LTE setup."
    while true; do
        monitor_once
        sleep "$CHECK_INTERVAL"
    done
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
    main "$@"
fi
