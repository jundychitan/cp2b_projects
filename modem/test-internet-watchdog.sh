#!/usr/bin/env bash
# Run on Linux: bash modem/test-internet-watchdog.sh (no sudo required).
# All reboot, serial, systemd and network behavior is mocked.
set -uo pipefail
script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
source "$script_dir/internet-watchdog.sh"

assert_eq() {
    if [[ "$1" != "$2" ]]; then
        printf 'FAIL: expected <%s>, got <%s>\n' "$2" "$1" >&2
        exit 1
    fi
}

log() { :; }
timeout() { shift; "$@"; }
systemctl() { printf '%s\n' "$service_state"; return "$systemctl_status"; }
flock() {
    if [[ "$1" == -u ]]; then
        unlocks=$((unlocks + 1))
        return 0
    fi
    return "$lock_busy"
}
sync() { syncs=$((syncs + 1)); }
reboot() { reboots=$((reboots + 1)); return "$reboot_status"; }
check_internet() { checks=$((checks + 1)); return "$internet_status"; }

reset_state() {
    service_state=inactive systemctl_status=0 lock_busy=0
    unlocks=0 syncs=0 reboots=0 reboot_status=0
    internet_status=1 checks=0 fail_count=0 paused=0
    setup_lock_fd=99 SECONDS=1000 grace_until=0
    SETUP_STAMP="$temp_dir/unused-stamp" last_generation=none
}

test_activating_pauses() {
    reset_state
    service_state=activating fail_count=2
    monitor_once
    assert_eq "$checks" 0
    assert_eq "$fail_count" 0
    assert_eq "$paused" 1
    assert_eq "$reboots" 0
}

test_lock_pauses_direct_setup() {
    reset_state
    lock_busy=1 fail_count=2
    monitor_once
    assert_eq "$checks" 0
    assert_eq "$fail_count" 0
    assert_eq "$paused" 1
}

test_setup_failure_does_not_disable_monitoring() {
    reset_state
    service_state=failed internet_status=0
    monitor_once
    assert_eq "$checks" 1
    assert_eq "$fail_count" 0
}

test_unknown_service_state_pauses() {
    reset_state
    systemctl_status=1
    monitor_once
    assert_eq "$checks" 0
    assert_eq "$reboots" 0
}

test_grace_after_setup() {
    reset_state
    paused=1 fail_count=0
    monitor_once
    assert_eq "$checks" 0
    assert_eq "$paused" 0
    [[ "$grace_until" -ge 1120 ]] || exit 1
}

test_startup_grace() {
    reset_state
    grace_until=2000 fail_count=0
    monitor_once
    assert_eq "$checks" 0
    assert_eq "$reboots" 0
}

test_success_clears_failures() {
    reset_state
    fail_count=2 internet_status=0
    monitor_once
    assert_eq "$fail_count" 0
    assert_eq "$reboots" 0
}

test_three_failures_request_host_reboot() {
    reset_state
    monitor_once
    monitor_once
    assert_eq "$reboots" 0
    monitor_once
    assert_eq "$reboots" 1
    assert_eq "$syncs" 1
    assert_eq "$fail_count" 0
    [[ "$grace_until" -gt "$SECONDS" ]] || exit 1
}

test_recheck_after_connectivity_probe() {
    reset_state
    fail_count=2
    check_internet() { checks=$((checks + 1)); lock_busy=1; return 1; }
    monitor_once
    assert_eq "$checks" 1
    assert_eq "$reboots" 0
    assert_eq "$fail_count" 0
    assert_eq "$paused" 1
}

test_recovery_refuses_busy_lock() {
    reset_state
    lock_busy=1
    request_reboot
    assert_eq "$reboots" 0
    assert_eq "$syncs" 0
}

test_recovery_rechecks_systemd_state() {
    reset_state
    service_state=activating
    request_reboot
    assert_eq "$reboots" 0
    assert_eq "$unlocks" 1
}

test_failed_reboot_releases_lock() {
    reset_state
    reboot_status=1
    request_reboot
    assert_eq "$reboots" 1
    assert_eq "$unlocks" 1
}

test_missing_targets_uses_ip_defaults() {
    reset_state
    TARGETS_FILE="$temp_dir/missing"
    load_targets
    assert_eq "${TARGETS[*]}" "${DEFAULT_TARGETS[*]}"
}

test_empty_targets_uses_ip_defaults() {
    reset_state
    TARGETS_FILE="$temp_dir/targets"
    printf '# comment\r\n  \r\n' > "$TARGETS_FILE"
    load_targets
    assert_eq "${TARGETS[*]}" "${DEFAULT_TARGETS[*]}"
}

test_configured_targets_handle_crlf_and_no_final_newline() {
    reset_state
    TARGETS_FILE="$temp_dir/targets"
    printf '  # comment\r\n1.2.3.4\r\nhttps://example.com/health' > "$TARGETS_FILE"
    load_targets
    assert_eq "${#TARGETS[@]}" 2
    assert_eq "${TARGETS[0]}" 1.2.3.4
    assert_eq "${TARGETS[1]}" https://example.com/health
}

test_completed_setup_between_polls_gets_grace() {
    reset_state
    SETUP_STAMP="$temp_dir/stamp"
    printf 'new-generation\n' > "$SETUP_STAMP"
    fail_count=2
    monitor_once
    assert_eq "$checks" 0
    assert_eq "$fail_count" 0
    assert_eq "$paused" 1
    monitor_once
    assert_eq "$checks" 0
    assert_eq "$paused" 0
    [[ "$grace_until" -gt "$SECONDS" ]] || exit 1
}

test_recovery_detects_new_setup_generation() {
    reset_state
    SETUP_STAMP="$temp_dir/stamp"
    printf 'new-generation\n' > "$SETUP_STAMP"
    request_reboot
    assert_eq "$reboots" 0
    assert_eq "$unlocks" 1
}

test_unit_orders_after_lte_without_requiring_success() {
    grep -Fq 'After=network-online.target lte-ecm.service' "$script_dir/internet-watchdog.service" || exit 1
    grep -Fq 'Wants=network-online.target lte-ecm.service' "$script_dir/internet-watchdog.service" || exit 1
    if grep -q '^Requires=.*lte-ecm' "$script_dir/internet-watchdog.service"; then
        exit 1
    fi
}

test_no_direct_serial_access() {
    if grep -Eq '/dev/ttyUSB|AT[+]CFUN|stty ' "$script_dir/internet-watchdog.sh"; then
        exit 1
    fi
}

test_probes_have_wall_clock_timeouts() {
    reset_state
    timeout() { probe_args="$*"; return 0; }
    check_target example.com
    assert_eq "$probe_args" '12 ping -c 2 -W 5 -- example.com'
    check_target https://example.com/health
    assert_eq "$probe_args" '12 curl -sf --max-time 10 -o /dev/null -- https://example.com/health'
}

temp_dir=$(mktemp -d)
trap 'rm -f "$temp_dir/targets" "$temp_dir/stamp"; rmdir "$temp_dir"' EXIT
tests=(
    test_activating_pauses
    test_lock_pauses_direct_setup
    test_setup_failure_does_not_disable_monitoring
    test_unknown_service_state_pauses
    test_grace_after_setup
    test_startup_grace
    test_success_clears_failures
    test_three_failures_request_host_reboot
    test_recheck_after_connectivity_probe
    test_recovery_refuses_busy_lock
    test_recovery_rechecks_systemd_state
    test_failed_reboot_releases_lock
    test_missing_targets_uses_ip_defaults
    test_empty_targets_uses_ip_defaults
    test_configured_targets_handle_crlf_and_no_final_newline
    test_completed_setup_between_polls_gets_grace
    test_recovery_detects_new_setup_generation
    test_unit_orders_after_lte_without_requiring_success
    test_no_direct_serial_access
    test_probes_have_wall_clock_timeouts
)
for test in "${tests[@]}"; do
    ( "$test" ) || exit 1
    printf 'PASS: %s\n' "$test"
done
printf '%s watchdog tests passed.\n' "${#tests[@]}"
