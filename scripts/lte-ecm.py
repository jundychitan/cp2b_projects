#!/usr/bin/env python3
"""One-shot Quectel EC200/EC25 ECM setup for Linux/NetworkManager."""

import argparse
import contextlib
import glob
import json
import logging
import os
from pathlib import Path
import re
import select
import subprocess
import sys
import time

LOG = logging.getLogger("lte-ecm")
DEFAULTS = {
    "port": "/dev/ttyUSB2",
    "ethernet": "",
    "apn": "",
    "dial_command": "AT+QNETDEVCTL=3,1,1",
    "device_timeout": 90,
    "activation_timeout": 60,
}


def wait_for(probe, seconds, description):
    deadline = time.monotonic() + seconds
    while True:
        result = probe()
        if result:
            return result
        if time.monotonic() >= deadline:
            raise RuntimeError("Timed out waiting for " + description)
        time.sleep(1)


class SerialAT:
    def __init__(self, path):
        import fcntl
        import termios
        import tty

        self.fd = os.open(path, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
        self.old = None
        self.exclusive = False
        try:
            fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            fcntl.ioctl(self.fd, termios.TIOCEXCL)
            self.exclusive = True
            self.old = termios.tcgetattr(self.fd)
            tty.setraw(self.fd)
            settings = termios.tcgetattr(self.fd)
            settings[4] = settings[5] = termios.B115200
            settings[2] |= termios.CLOCAL | termios.CREAD
            settings[2] &= ~getattr(termios, "CRTSCTS", 0)
            termios.tcsetattr(self.fd, termios.TCSANOW, settings)
        except Exception:
            self.close()
            raise

    def close(self):
        import fcntl
        import termios

        try:
            if self.old is not None:
                termios.tcsetattr(self.fd, termios.TCSANOW, self.old)
            if self.exclusive:
                fcntl.ioctl(self.fd, termios.TIOCNXCL)
        except OSError:
            pass  # Expected when the modem has just reset.
        finally:
            os.close(self.fd)

    def command(self, command, timeout=8, reset=False):
        import termios

        if "\r" in command or "\n" in command or not command.startswith("AT"):
            raise RuntimeError("Invalid AT command")
        termios.tcflush(self.fd, termios.TCIFLUSH)
        payload = (command + "\r").encode("ascii")
        deadline = time.monotonic() + timeout
        while payload:
            if time.monotonic() >= deadline:
                raise RuntimeError("Timed out writing AT command")
            _, writable, _ = select.select([], [self.fd], [], 0.2)
            if writable:
                payload = payload[os.write(self.fd, payload):]
        data = b""
        while time.monotonic() < deadline:
            ready, _, _ = select.select([self.fd], [], [], 0.2)
            if not ready:
                continue
            try:
                chunk = os.read(self.fd, 4096)
            except OSError:
                if reset:
                    return ""
                raise
            if not chunk:
                if reset:
                    return ""
                raise RuntimeError("AT port disconnected")
            data += chunk
            if len(data) > 65536:
                raise RuntimeError("Excessive response from AT port")
            text = data.decode("ascii", errors="replace")
            lines = [line.strip() for line in text.splitlines()]
            if any(line == "ERROR" or line.startswith(("+CME ERROR", "+CMS ERROR"))
                   for line in lines):
                raise RuntimeError("Modem rejected " + command.split("=")[0])
            if "OK" in lines:
                return text
        if reset:
            return ""  # A reset can disconnect before sending OK.
        raise RuntimeError("No final response to " + command.split("=")[0])


@contextlib.contextmanager
def at_port(path):
    port = SerialAT(path)
    try:
        yield port
    finally:
        port.close()


def usb_parent(device):
    resolved = Path(device).resolve()
    for parent in (resolved, *resolved.parents):
        if (parent / "idVendor").is_file() and (parent / "idProduct").is_file():
            return parent
    raise RuntimeError("Cannot identify modem USB parent")


def modem_usb(port):
    parent = usb_parent(Path("/sys/class/tty") / Path(port).resolve().name / "device")
    if (parent / "idVendor").read_text().strip().lower() != "2c7c":
        raise RuntimeError("AT port is not attached to a Quectel USB device")
    return parent


def usbnet_mode(response):
    match = re.search(r'\+QCFG:\s*"usbnet"\s*,\s*(\d+)', response, re.I)
    if not match:
        raise RuntimeError("Cannot parse modem USB network mode")
    return int(match.group(1))


def data_call_connected(response, cid):
    entries = re.findall(
        r"^\s*\+QNETDEVCTL:\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*$",
        response, re.M | re.I,
    )
    if not entries:
        raise RuntimeError("Cannot parse AT+QNETDEVCTL? connection state")
    return any(int(context) == cid and int(state) == 1
               for _, context, _, state in entries)


def activate_data_call(port, command, timeout):
    match = re.fullmatch(r"AT\+QNETDEVCTL=([13]),(\d+),[01]", command, re.I)
    if not match:
        LOG.info("Sending custom data-call command")
        port.command(command, timeout=45)
        return
    cid = int(match.group(2))
    if data_call_connected(port.command("AT+QNETDEVCTL?"), cid):
        LOG.info("Modem data call for context %d is already connected", cid)
        return
    LOG.info("Activating modem data call for context %d", cid)
    port.command(command, timeout=45)
    wait_for(lambda: data_call_connected(port.command("AT+QNETDEVCTL?"), cid),
             timeout, f"modem data call for context {cid}")


def model_from_response(response):
    # Accept model-only replies and common prefixes, not firmware-version substrings.
    pattern = (
        r'(?:(?:\+?(?:CGMM|GMM)|MODEL)\s*:\s*)?'
        r'(?:QUECTEL\s+)?"?(EC200[A-Z0-9_-]*|EC25[A-Z0-9_-]*)"?'
    )
    for line in response.splitlines():
        match = re.fullmatch(pattern, line.strip(), re.I)
        if match:
            return match.group(1).upper()
    return None


def identify_modem(port, path):
    # USB VID has already established Quectel ownership in modem_usb().
    # Some firmware returns only the model, or incomplete ATI output at boot.
    for attempt in range(2):
        for command in ("AT+CGMM", "AT+GMM", "ATI"):
            try:
                response = port.command(command, timeout=3)
            except RuntimeError:
                continue  # Unsupported query or incomplete response; try the fallback.
            model = model_from_response(response)
            if model:
                LOG.info("Detected Quectel model %s on %s using %s", model, path, command)
                return model
        if attempt == 0:
            time.sleep(1)
    raise RuntimeError(
        f"No supported EC200/EC25 model identified on {path} using CGMM/GMM/ATI; "
        "check the AT port and firmware model response. No modem settings were changed"
    )


def prepare_modem(config):
    path = config["port"]
    wait_for(lambda: Path(path).is_char_device(), config["device_timeout"], path)
    parent = modem_usb(path)
    old_devnum = (parent / "devnum").read_text().strip()
    with at_port(path) as port:
        wait_for(lambda: modem_ready(port), 30, "modem AT readiness")
        model = identify_modem(port, path)
        mode = usbnet_mode(port.command('AT+QCFG="usbnet"'))
        changed = mode != 1
        if changed:
            LOG.info("Switching USB network mode %d to ECM; restarting modem", mode)
            port.command('AT+QCFG="usbnet",1')
            port.command("AT+CFUN=1,1", reset=True)
        else:
            LOG.info("Modem is already in ECM mode")
    if changed:
        # A fast reset may finish before we observe tty removal.
        def reset_observed():
            try:
                return (not Path(path).exists()
                        or (parent / "devnum").read_text().strip() != old_devnum)
            except FileNotFoundError:
                return True

        wait_for(reset_observed, 30, "modem USB reset")
        wait_for(lambda: Path(path).is_char_device(), config["device_timeout"], path)
        if modem_usb(path) != parent:
            raise RuntimeError("Modem returned on an unexpected USB path")
        with at_port(path) as port:
            wait_for(lambda: modem_ready(port), 30, "AT readiness after reset")
            if usbnet_mode(port.command('AT+QCFG="usbnet"')) != 1:
                raise RuntimeError("Modem did not retain ECM mode")
    dial_command = config["dial_command"] or DEFAULTS["dial_command"]
    if config["apn"] or dial_command:
        with at_port(path) as port:
            if config["apn"]:
                apn = config["apn"]
                if not re.fullmatch(r"[A-Za-z0-9._-]{1,100}", apn):
                    raise RuntimeError("APN contains unsupported characters")
                contexts = port.command("AT+CGDCONT?")
                desired = f'+CGDCONT: 1,"IP","{apn}"'
                normalized = re.sub(r"\s+", "", contexts)
                if desired.replace(" ", "") not in normalized:
                    port.command(f'AT+CGDCONT=1,"IP","{apn}"')
            if model.startswith("EC25") and re.match(r"AT\+QNETDEVCTL", dial_command, re.I):
                LOG.info("EC25 ECM: skipping EC200-only QNETDEVCTL; using modem's ECM data service")
            else:
                activate_data_call(port, dial_command, config["device_timeout"])
    return parent


def modem_ready(port):
    try:
        port.command("AT", timeout=2)
        return True
    except (OSError, RuntimeError):
        return False


def ecm_interface(parent):
    matches = []
    for name in glob.glob("/sys/class/net/*"):
        device = Path(name) / "device"
        try:
            if usb_parent(device) != parent:
                continue
            if (device / "driver").resolve().name == "cdc_ether":
                matches.append(Path(name).name)
        except (OSError, RuntimeError):
            continue
    if len(matches) > 1:
        raise RuntimeError("Multiple ECM interfaces found for modem")
    return matches[0] if matches else None


def nm(*args, timeout=20):
    result = subprocess.run(
        ["nmcli", *args], capture_output=True, text=True, timeout=timeout,
        env={**os.environ, "LC_ALL": "C"},
    )
    if result.returncode:
        raise RuntimeError("nmcli failed: " + result.stderr.strip())
    return result.stdout.strip()


def get_profile(uuid, field):
    return nm("-g", field, "connection", "show", "uuid", uuid)


def set_properties(uuid, desired):
    changed = []
    for key, value in desired.items():
        if get_profile(uuid, key) != value:
            changed.extend((key, value))
    if changed:
        nm("connection", "modify", "uuid", uuid, *changed)
    return bool(changed)


def profiles():
    return nm("-g", "UUID", "connection", "show").splitlines()


def verify_routes(interface, metric, required=False):
    result = subprocess.run(
        ["ip", "-j", "-4", "route", "show", "table", "all", "default", "dev", interface],
        capture_output=True, text=True, check=True, timeout=10,
    )
    routes = json.loads(result.stdout)
    if required and not routes:
        raise RuntimeError("No IPv4 default route on " + interface)
    if any(route.get("metric", 0) != metric for route in routes):
        raise RuntimeError(
            f"{interface} still has a default route with a metric other than {metric}; "
            "check explicit ipv4.routes metrics or another network manager"
        )


def configure_ethernet(interface):
    if nm("-g", "GENERAL.NM-MANAGED", "device", "show", interface) != "yes":
        raise RuntimeError(interface + " is not managed by NetworkManager; not migrating it")
    active = nm("-g", "GENERAL.CON-UUID", "device", "show", interface)
    selected = [
        uuid for uuid in profiles()
        if get_profile(uuid, "connection.type") == "802-3-ethernet"
        and (uuid == active or get_profile(uuid, "connection.interface-name") == interface)
    ]
    if not selected:
        # Default wired profiles are often unbound, especially with no cable.
        mac = nm("-g", "GENERAL.HWADDR", "device", "show", interface).lower()
        selected = [
            uuid for uuid in profiles()
            if get_profile(uuid, "connection.type") == "802-3-ethernet"
            and get_profile(uuid, "connection.id") != "LTE"
            and get_profile(uuid, "connection.interface-name") in ("", "--")
            and get_profile(uuid, "802-3-ethernet.mac-address").lower() in ("", "--", mac)
        ]
        if len(selected) != 1:
            raise RuntimeError("Cannot choose Ethernet profile for " + interface
                               + "; bind its connection.interface-name explicitly")
    changed = False
    for uuid in selected:
        changed |= set_properties(uuid, {"ipv4.route-metric": "200"})
    if active and active != "--":
        # Reapply even if persistent settings already match: the live route may not.
        nm("device", "reapply", interface)
        verify_routes(interface, 200)
    LOG.info("Ethernet %s: IPv4 metric 200%s", interface, " (updated)" if changed else "")


def existing_lte_profile():
    matches = [uuid for uuid in profiles() if get_profile(uuid, "connection.id") == "LTE"]
    if len(matches) > 1:
        raise RuntimeError("Multiple connections named LTE; remove ambiguity first")
    if not matches:
        return None
    uuid = matches[0]
    if get_profile(uuid, "connection.type") != "802-3-ethernet":
        raise RuntimeError("Existing LTE connection is not an Ethernet/ECM profile")
    return uuid


def configure_existing_lte_metric(primary_interface):
    """Repair saved/live LTE priority without depending on AT commands or DHCP."""
    uuid = existing_lte_profile()
    if uuid is None:
        LOG.info("No existing LTE profile; it will be created after ECM discovery")
        return False
    active_device = get_profile(uuid, "GENERAL.DEVICES")
    bound = get_profile(uuid, "connection.interface-name")
    if primary_interface in (active_device, bound):
        raise RuntimeError("LTE profile points to primary Ethernet; refusing to modify it")
    if active_device not in ("", "--") and not re.fullmatch(r"[A-Za-z0-9_.-]+", active_device):
        raise RuntimeError("Cannot choose a single active device for the LTE profile")
    set_properties(uuid, {"ipv4.route-metric": "100"})
    LOG.info("Saved LTE profile IPv4 metric: %s", get_profile(uuid, "ipv4.route-metric"))
    if active_device not in ("", "--"):
        if nm("-g", "GENERAL.CON-UUID", "device", "show", active_device) != uuid:
            raise RuntimeError("LTE active device changed while applying metric; rerun setup")
        nm("device", "reapply", active_device)
        verify_routes(active_device, 100)
        LOG.info("Applied LTE IPv4 metric 100 to active device %s", active_device)
    return True


def configure_lte(interface, activation_timeout):
    uuid = existing_lte_profile()
    if uuid is not None:
        devices = get_profile(uuid, "GENERAL.DEVICES")
        if devices not in ("", "--", interface):
            raise RuntimeError("Existing LTE profile is active on another interface: " + devices)
        bound = get_profile(uuid, "connection.interface-name")
        if bound not in ("", "--", interface) and Path("/sys/class/net", bound).exists():
            raise RuntimeError("Existing LTE profile belongs to another present interface: " + bound)
    else:
        nm("connection", "add", "type", "ethernet", "ifname", interface,
           "con-name", "LTE", "connection.autoconnect", "yes",
           "ipv4.method", "auto", "ipv4.route-metric", "100")
        uuid = nm("-g", "connection.uuid", "connection", "show", "id", "LTE")
    # No MAC binding: some modem firmware changes its MAC after power cycling.
    set_properties(uuid, {
        "connection.interface-name": interface,
        "connection.autoconnect": "yes",
        "connection.autoconnect-priority": "100",
        "connection.autoconnect-retries": "0",
        "ipv4.method": "auto",
        "ipv4.may-fail": "no",
        "ipv4.route-metric": "100",
        "ipv4.never-default": "no",
        "ipv4.ignore-auto-routes": "no",
    })
    if nm("-g", "GENERAL.CON-UUID", "device", "show", interface) == uuid:
        nm("device", "reapply", interface)
    else:
        nm("--wait", str(activation_timeout), "connection", "up", "uuid", uuid,
           "ifname", interface, timeout=activation_timeout + 10)
    routes = nm("-g", "IP4.GATEWAY", "device", "show", interface)
    if not routes or routes == "--":
        raise RuntimeError("LTE has no IPv4 gateway; check SIM, APN and data-call command")
    verify_routes(interface, 100, required=True)
    LOG.info("LTE connection on %s: DHCP, autoconnect, IPv4 metric 100", interface)


def run(config, routes_only=False):
    interface = config["ethernet"]
    if not interface:
        candidates = [name for name in ("eth0", "end0")
                      if Path("/sys/class/net", name).exists()]
        if len(candidates) != 1:
            raise RuntimeError("Specify --ethernet at install time (cannot choose eth0/end0)")
        interface = candidates[0]
    errors = []
    failures = (OSError, RuntimeError, ValueError, subprocess.SubprocessError)
    # Neither route stage may depend on successful serial initialization.
    try:
        found = configure_existing_lte_metric(interface)
        if routes_only and not found:
            raise RuntimeError("No existing LTE profile to repair; run full setup first")
    except failures as error:
        LOG.error("LTE route metric stage: %s", error)
        errors.append(f"LTE route metric: {error}")
    try:
        configure_ethernet(interface)
    except failures as error:
        LOG.error("Ethernet route metric stage: %s", error)
        errors.append(f"Ethernet route metric: {error}")
    if not routes_only:
        try:
            parent = prepare_modem(config)
            lte_interface = wait_for(lambda: ecm_interface(parent), config["device_timeout"],
                                     "modem CDC ECM interface (cdc_ether driver)")
            configure_lte(lte_interface, config["activation_timeout"])
        except failures as error:
            LOG.error("Modem/ECM stage: %s", error)
            errors.append(f"modem/ECM: {error}")
    if errors:
        raise RuntimeError("Setup incomplete: " + "; ".join(errors))
    LOG.info("IPv4 route priority configured: LTE 100, Ethernet 200")


def main():
    import fcntl

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="/etc/lte-ecm.json")
    parser.add_argument("--routes-only", action="store_true",
                        help="Repair existing LTE/Ethernet metrics without modem commands")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    if os.geteuid() != 0:
        parser.error("Run as root")
    config = dict(DEFAULTS)
    config.update(json.loads(Path(args.config).read_text()))
    for key in ("device_timeout", "activation_timeout"):
        if not isinstance(config[key], int) or not 1 <= config[key] <= 120:
            parser.error(key + " must be an integer between 1 and 120")
    with open("/run/lte-ecm.lock", "w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError("Another lte-ecm instance is already running")
        # The watchdog also detects setup attempts that finish between its polls.
        Path("/run/lte-ecm.generation").write_text(f"{time.monotonic_ns()}\n")
        run(config, routes_only=args.routes_only)


if __name__ == "__main__":
    try:
        main()
    except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as error:
        LOG.error("%s", error)
        LOG.error("Successful route changes are retained. "
                  "See journalctl -u lte-ecm.service -b for the failing stage")
        sys.exit(1)
