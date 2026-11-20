#!/usr/bin/env bash
set -euo pipefail

usage() {
    cat <<'USAGE'
Usage: sudo bash install-lte-ecm.sh [OPTIONS]

Install EC200/EC25 ECM boot setup on Debian Bookworm / Ubuntu 24.04 with
NetworkManager already managing the primary Ethernet interface.

  --ethernet NAME       Primary Ethernet device (default: detect eth0 or end0)
  --port PATH           AT device (default: /dev/ttyUSB2)
  --apn NAME            Set PDP context 1 to IPv4 with this APN
                        Default: preserve the module's existing APN/PDP settings
  --dial-command AT...  Firmware-specific data-call command
                        Default: 'AT+QNETDEVCTL=3,1,1'
                        QNETDEVCTL is queried first; connected calls are left alone
  --start               Run LTE setup now and then start the watchdog
                        Stops any old watchdog first; may reset the modem
  --help                Show this help

Existing /etc/lte-ecm.json is preserved on reinstall. Edit that file to change
settings afterwards; configuration options on reinstall are rejected.
No packages are installed and no network manager is enabled/disabled.
Both lte-ecm.service and internet-watchdog.service are installed and enabled.
The watchdog starts after LTE setup, with a 120-second grace period. It keeps
the existing policy of rebooting after three failed connectivity checks.
Prerequisites: python3, network-manager, systemd, iproute2, coreutils,
               util-linux (flock), iputils-ping, curl.
An already-running watchdog requires --start, so the old process can be replaced.

After installation:
  sudo systemctl start lte-ecm.service
  sudo journalctl -u lte-ecm.service -b --no-pager
  nmcli connection show LTE
  ip -4 route show default
  sudo /usr/local/sbin/lte-ecm --routes-only

Existing LTE profile priority is repaired before modem initialization. EC200
uses QNETDEVCTL; detected EC25 modules use their ECM service without that command.
--routes-only repairs existing profiles without accessing the AT port.
If live LTE routes remain stale after reapply, the LTE profile is reactivated
once. This can briefly interrupt LTE sessions; Ethernet is not restarted.

LTE setup runs once per boot, then exits (inactive/dead on success).
Use systemctl restart lte-ecm.service to rerun setup; the separate watchdog
pauses during reruns and remains running afterwards.
IPv4 metrics: Ethernet 200, LTE 100. They prefer LTE while its route exists,
but do not detect an upstream Internet failure. IPv6 preferences are not modified.
USAGE
}

die() { printf 'Error: %s\n' "$*" >&2; exit 1; }
ethernet='' port='/dev/ttyUSB2' apn='' dial_command='AT+QNETDEVCTL=3,1,1' start=0 configured=0
while (($#)); do
    case "$1" in
        --help|-h) usage; exit 0 ;;
        --start) start=1; shift ;;
        --ethernet|--port|--apn|--dial-command)
            (($# >= 2)) || die "$1 requires a value"
            [[ -n "$2" && "$2" != --* ]] || die "$1 requires a value"
            case "$1" in
                --ethernet) ethernet=$2 ;;
                --port) port=$2 ;;
                --apn) apn=$2 ;;
                --dial-command) dial_command=$2 ;;
            esac
            configured=1
            shift 2
            ;;
        *) die "Unknown option: $1 (use --help)" ;;
    esac
done
[[ $EUID -eq 0 ]] || die 'Run with sudo bash install-lte-ecm.sh'
[[ $(uname -s) == Linux && -d /run/systemd/system ]] || die 'Linux with systemd is required'
for tool in python3 nmcli systemctl ip install flock timeout ping curl reboot sync; do
    command -v "$tool" >/dev/null || die "Missing prerequisite: $tool"
done
systemctl is-active --quiet NetworkManager.service ||
    die 'NetworkManager must already be running; do not migrate a remote gateway blindly'

source_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
modem_dir="$source_dir/../modem"
for file in lte-ecm.py lte-ecm.service; do
    [[ -f "$source_dir/$file" ]] || die "Missing $source_dir/$file"
done
for file in internet-watchdog.sh internet-watchdog.service; do
    [[ -f "$modem_dir/$file" ]] || die "Missing $modem_dir/$file"
done
watchdog_load=$(systemctl show internet-watchdog.service --property=LoadState --value) ||
    die 'Cannot determine whether a watchdog is installed'
watchdog_state=$(systemctl show internet-watchdog.service --property=ActiveState --value) ||
    die 'Cannot determine watchdog state'
if [[ "$watchdog_state" != inactive && "$watchdog_state" != failed && -n "$watchdog_state" && $start -eq 0 ]]; then
    die 'Watchdog is running or changing state; use --start to replace it safely'
fi
if [[ -e /etc/lte-ecm.json && $configured -eq 1 ]]; then
    die 'Configuration exists; edit /etc/lte-ecm.json instead of supplying configuration options'
fi

# Validate before installing files; JSON serialization avoids shell interpolation.
python3 - "$ethernet" "$port" "$apn" "$dial_command" <<'PY'
import json
from pathlib import Path
import re
import sys

ethernet, port, apn, dial = sys.argv[1:]
if not port.startswith("/dev/") or "\n" in port:
    sys.exit("AT port must be an absolute /dev/ path")
if apn and not re.fullmatch(r"[A-Za-z0-9._-]{1,100}", apn):
    sys.exit("Invalid APN")
if dial and (not dial.startswith("AT") or not dial.isascii() or "\r" in dial or "\n" in dial):
    sys.exit("Data-call command must be a single ASCII AT command")
path = Path("/etc/lte-ecm.json")
if not path.exists():
    data = {"ethernet": ethernet, "port": port, "apn": apn, "dial_command": dial,
            "device_timeout": 90, "activation_timeout": 60}
    # Exclusive creation preserves an existing installation's configuration.
    import os
    with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w") as stream:
        json.dump(data, stream, indent=2)
        stream.write("\n")
PY

if ((start)); then
    # Stop even an activating/restarting legacy watchdog before changing setup.
    systemctl stop internet-watchdog.service 2>/dev/null ||
        [[ "$watchdog_load" == not-found ]] ||
        die 'Could not stop the existing watchdog'
fi

install_file() {
    local mode="$1" src="$2" dst="$3"
    # /projects may itself be the repository checkout or a symlink to it.
    if [[ "$src" -ef "$dst" ]]; then
        chmod "$mode" "$dst"
    else
        install -m "$mode" "$src" "$dst"
    fi
}

install_file 0755 "$source_dir/lte-ecm.py" /usr/local/sbin/lte-ecm
install_file 0644 "$source_dir/lte-ecm.service" /etc/systemd/system/lte-ecm.service
install -d -m 0755 /projects/modem
install_file 0755 "$modem_dir/internet-watchdog.sh" /projects/modem/internet-watchdog.sh
install_file 0644 "$modem_dir/internet-watchdog.service" /etc/systemd/system/internet-watchdog.service
systemctl daemon-reload
systemctl enable lte-ecm.service internet-watchdog.service
printf '%s\n' 'Installed and enabled LTE setup and watchdog; configuration: /etc/lte-ecm.json'
if ((start)); then
    # Wants=/After= pulls in exactly one setup attempt before watchdog startup.
    # A separate explicit LTE start here would run the terminating oneshot twice.
    systemctl start internet-watchdog.service
    setup_result=$(systemctl show lte-ecm.service --property=Result --value)
    if [[ "$setup_result" != success ]]; then
        printf 'LTE setup result: %s; watchdog remains running for Ethernet monitoring.\n' "$setup_result" >&2
        exit 1
    fi
else
    printf '%s\n' 'Both services run at next boot. To run now, reinstall with --start.'
fi
