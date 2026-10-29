"""
RS485-to-TCP Bridge Client

Bridges a local endpoint to a remote TCP server, forwarding data
bidirectionally in real time.

Local endpoint modes (set via "local_mode" in config):
  "serial" - RS485 / COM port  (default) — uses pyserial for bidirectional
             transparent forwarding.
  "tcp"    - Local TCP port (e.g. localhost:22 for SSH) — uses socat when
             available for correct handshake-based protocol support.
  "web"  - Local web UI port (e.g. localhost:8080) — bridges HTTP traffic
             to the remote TCP server.

Architecture:
  - Main thread: interactive command prompt (connect / disconnect / status / quit).
  - Bridge thread: manages connection lifecycle and auto-reconnection.
  - Local→Remote thread: reads bytes from local endpoint, sends to remote server.
  - Remote→Local thread: reads bytes from remote server, writes to local endpoint.

Interactive commands:
  connect    - Open the local endpoint and remote TCP connection, start forwarding.
  disconnect - Stop forwarding, close remote TCP and release the local endpoint.
  status     - Show current connection state.
  quit       - Disconnect (if connected) and exit.

Usage:
  python rs485_tcp_client.py [--config path/to/client_config.json]
"""

import argparse
import json
import logging
import os
import shutil
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import serial  # pyserial


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

DEFAULT_CONFIG_PATH = Path(__file__).parent / "client_config.json"

PARITY_MAP = {
    "N": serial.PARITY_NONE,
    "E": serial.PARITY_EVEN,
    "O": serial.PARITY_ODD,
    "M": serial.PARITY_MARK,
    "S": serial.PARITY_SPACE,
}

STOPBITS_MAP = {
    1: serial.STOPBITS_ONE,
    1.5: serial.STOPBITS_ONE_POINT_FIVE,
    2: serial.STOPBITS_TWO,
}


def load_config(path) -> dict:
    """Load and return the JSON configuration file."""
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError("Config file not found: %s" % path)
    with open(str(path), "r", encoding="utf-8") as fh:
        return json.load(fh)


# ---------------------------------------------------------------------------
# Bridge
# ---------------------------------------------------------------------------

class RS485TcpBridge:
    """Bidirectional bridge between a local endpoint (serial or TCP) and a remote TCP server."""

    def __init__(self, config: dict) -> None:
        self.local_mode = config.get("local_mode", "serial")  # "serial", "tcp", or "web"
        self.serial_cfg = config.get("serial", {})
        self.local_tcp_cfg = config.get("local_tcp", {})
        self._config = config  # Keep full config for channel settings etc.
        self.web_cfg = config.get("local_web", config.get("web", {"host": "127.0.0.1", "port": 8080}))
        self.server_host = config["server"]["host"]
        self.server_port = config["server"]["port"]
        self.reconnect_interval = config.get("reconnect_interval", 5)
        self.buffer_size = config.get("buffer_size", 4096)
        self.auto_connect = config.get("auto_connect", True)

        self._ser = None
        self._local_sock = None   # local TCP endpoint
        self._sock = None         # remote server socket

        # Event signals forwarding threads to stop gracefully.
        self._stop_event = threading.Event()
        # Event used to request a manual disconnect from the command prompt.
        self._disconnect_event = threading.Event()
        # Tracks whether the bridge loop is actively running.
        self._bridge_thread = None
        self._connected = False

        self._threads = []
        self._socat_proc = None      # subprocess.Popen when socat is active (TCP mode)
        self._use_socat = config.get("use_socat", True)
        self._serial_wait_timeout = config.get("serial_wait_timeout", 30)
        # socat_debug: add -d -d -x flags (connection events + hex packet dumps)
        # and route all socat stderr to the logger at DEBUG level.
        # Defaults to True when log_level is DEBUG.
        _log_is_debug = config.get("log_level", "INFO").upper() == "DEBUG"
        self._socat_debug = config.get("socat_debug", _log_is_debug)

        self.logger = logging.getLogger("rs485_bridge")

    # -- Serial helpers -----------------------------------------------------

    def _port_in_use_by_others(self, port):
        """Check if *port* is held open by another process (Linux only).

        Returns a list of PIDs that have the device open, excluding our own.
        Uses ``fuser`` if available, otherwise falls back to scanning
        ``/proc/[pid]/fd/``.
        """
        my_pid = os.getpid()

        # Method 1: fuser (fast, common on most Linux distros)
        try:
            out = subprocess.check_output(
                ["fuser", port], stderr=subprocess.DEVNULL
            ).decode("utf-8", errors="replace").strip()
            if out:
                pids = [int(p) for p in out.split() if p.strip().rstrip("m").isdigit()]
                return [p for p in pids if p != my_pid]
        except (OSError, subprocess.CalledProcessError):
            pass  # fuser not installed or port not in use

        # Method 2: scan /proc (fallback)
        try:
            real_port = os.path.realpath(port)
            pids = []
            for entry in os.listdir("/proc"):
                if not entry.isdigit():
                    continue
                pid = int(entry)
                if pid == my_pid:
                    continue
                fd_dir = "/proc/%d/fd" % pid
                try:
                    for fd in os.listdir(fd_dir):
                        try:
                            target = os.readlink(os.path.join(fd_dir, fd))
                            if target == real_port:
                                pids.append(pid)
                                break
                        except OSError:
                            pass
                except OSError:
                    pass
            return pids
        except OSError:
            pass

        return []  # Cannot determine — assume free

    def _wait_for_serial_port(self, port):
        """Block until the serial port device exists and is not held by another process.

        Polls every 2 seconds up to ``serial_wait_timeout`` (default 30 s).
        Respects ``_stop_event`` / ``_disconnect_event`` so the wait can be
        interrupted.  Raises ``serial.SerialException`` if the timeout
        expires while the port is still busy.
        """
        # If the device file doesn't exist at all, fail immediately.
        if not os.path.exists(port):
            raise serial.SerialException("Device %s not found" % port)

        deadline = time.time() + self._serial_wait_timeout
        logged_waiting = False

        while time.time() < deadline:
            if self._stop_event.is_set() or self._disconnect_event.is_set():
                raise serial.SerialException("Wait interrupted by stop/disconnect")

            holders = self._port_in_use_by_others(port)
            if not holders:
                if logged_waiting:
                    self.logger.info("%s appears free, rechecking in 2s...", port)
                # Wait 2 seconds and verify it's still free
                self._stop_event.wait(2)
                if self._stop_event.is_set() or self._disconnect_event.is_set():
                    raise serial.SerialException("Wait interrupted by stop/disconnect")
                recheck = self._port_in_use_by_others(port)
                if not recheck:
                    if logged_waiting:
                        self.logger.info("%s confirmed free", port)
                    return  # Port is available
                self.logger.info(
                    "%s grabbed again by PID %s, continuing to wait...",
                    port, ", ".join(str(p) for p in recheck),
                )
                logged_waiting = True
                continue

            if not logged_waiting:
                self.logger.info(
                    "Waiting for %s to become free (held by PID %s)...",
                    port, ", ".join(str(p) for p in holders),
                )
                logged_waiting = True
            self._stop_event.wait(2)

        raise serial.SerialException(
            "Timeout waiting for %s to become free (waited %ds, held by PID %s)"
            % (port, self._serial_wait_timeout,
               ", ".join(str(p) for p in self._port_in_use_by_others(port)))
        )

    def _open_serial(self) -> serial.Serial:
        """Open the RS485 serial port using the configured parameters.

        Waits for the port to be free before opening.
        """
        cfg = self.serial_cfg
        port = cfg["port"]
        parity = PARITY_MAP.get(cfg.get("parity", "N"), serial.PARITY_NONE)
        stopbits = STOPBITS_MAP.get(cfg.get("stopbits", 1), serial.STOPBITS_ONE)

        # Wait for the port to be free (e.g. released by a previous socat)
        self._wait_for_serial_port(port)

        ser = serial.Serial(
            port=port,
            baudrate=cfg.get("baudrate", 9600),
            bytesize=cfg.get("bytesize", 8),
            parity=parity,
            stopbits=stopbits,
            timeout=cfg.get("timeout", 0.1),
        )
        self.logger.info("Serial port opened: %s @ %d baud", ser.port, ser.baudrate)
        return ser

    def _close_serial(self) -> None:
        """Close the serial port if open."""
        if self._ser is not None:
            try:
                self._ser.close()
            except Exception:
                pass
            self._ser = None

    # -- Local TCP helpers --------------------------------------------------

    def _connect_local_tcp(self) -> socket.socket:
        """Connect to the local TCP endpoint (e.g. localhost:22) or web UI."""
        if self.local_mode == "web":
            host = self.web_cfg.get("host", "127.0.0.1")
            port = self.web_cfg.get("port", 8080)
        else:
            host = self.local_tcp_cfg["host"]
            port = self.local_tcp_cfg["port"]
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(10)
        sock.connect((host, port))
        sock.settimeout(0.5)
        self.logger.info("Local TCP connected to %s:%d", host, port)
        return sock

    def _close_local_tcp(self) -> None:
        """Close the local TCP socket if open."""
        if self._local_sock is not None:
            try:
                self._local_sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                self._local_sock.close()
            except Exception:
                pass
            self._local_sock = None

    # -- Unified local endpoint helpers -------------------------------------

    def _open_local(self) -> None:
        """Open the local endpoint (serial, TCP, or web) based on local_mode."""
        if self.local_mode in ("tcp", "web"):
            self._local_sock = self._connect_local_tcp()
        else:
            self._ser = self._open_serial()

    def _close_local(self) -> None:
        """Close the local endpoint (serial, TCP, or web)."""
        if self.local_mode in ("tcp", "web"):
            self._close_local_tcp()
        else:
            self._close_serial()

    def _local_is_open(self) -> bool:
        """Check if the local endpoint is currently open."""
        if self.local_mode in ("tcp", "web"):
            return self._local_sock is not None
        else:
            return self._ser is not None and self._ser.is_open

    def _socat_available(self) -> bool:
        """Return True if socat is found on PATH."""
        return shutil.which("socat") is not None

    def _drain_socat_stderr(self, proc, label, last_lines):
        """Background thread: read socat stderr and route to the logger.

        In normal mode each line is logged at WARNING so errors are always
        visible.  In socat_debug mode (which also adds -d -d -x to the
        command) every line is logged at DEBUG — that includes connection
        events and a full hex-dump of every forwarded packet.

        *last_lines* is a list shared with the caller; up to 20 recent
        lines are kept so they can be re-logged at WARNING if socat exits
        with a non-zero code.
        """
        level = logging.DEBUG if self._socat_debug else logging.WARNING
        try:
            for raw in proc.stderr:
                line = raw.decode("utf-8", errors="replace").rstrip("\r\n")
                if line:
                    self.logger.log(level, "[socat/%s] %s", label, line)
                    if len(last_lines) < 20:
                        last_lines.append(line)
        except Exception:
            pass

    # -- Remote TCP helpers -------------------------------------------------

    def _connect_tcp(self) -> socket.socket:
        """Establish a TCP connection to the remote server."""
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(10)  # connection timeout
        sock.connect((self.server_host, self.server_port))
        # After connection, use a short timeout so recv() doesn't block forever
        # and the thread can check the stop event periodically.
        sock.settimeout(0.5)

        # Enable TCP keepalive to detect silently broken connections
        # (NAT timeout, router reboot, etc.) instead of waiting for
        # a read/write to fail after an indefinite hang.
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
        try:
            # Linux: start probes after 60s idle, every 10s, fail after 5 misses
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPIDLE, 60)
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPINTVL, 10)
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPCNT, 5)
        except (AttributeError, OSError):
            pass  # Not available on this platform — keepalive still active

        self.logger.info(
            "Remote TCP connected to %s:%d", self.server_host, self.server_port
        )
        return sock

    def _close_tcp(self) -> None:
        """Close the remote TCP socket if open."""
        if self._sock is not None:
            try:
                self._sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                self._sock.close()
            except Exception:
                pass
            self._sock = None

    # -- Data-forwarding threads -------------------------------------------

    def _local_read(self, size: int) -> bytes:
        """Read up to *size* bytes from the local endpoint."""
        if self.local_mode in ("tcp", "web"):
            return self._local_sock.recv(size)
        else:
            ser = self._ser
            if ser is None or not ser.is_open:
                raise serial.SerialException("Serial port closed")
            return ser.read(size)

    def _local_write(self, data: bytes) -> None:
        """Write *data* to the local endpoint."""
        if self.local_mode in ("tcp", "web"):
            self._local_sock.sendall(data)
        else:
            ser = self._ser
            if ser is None or not ser.is_open:
                raise serial.SerialException("Serial port closed")
            ser.write(data)

    def _local_to_remote(self) -> None:
        """Read data from local endpoint and forward to remote TCP socket."""
        label = {"tcp": "local-tcp", "web": "web", "serial": "serial"}.get(self.local_mode, "serial")
        self.logger.debug("%s→remote thread started", label)
        while not self._stop_event.is_set():
            try:
                data = self._local_read(self.buffer_size)
                if not data:
                    if self.local_mode in ("tcp", "web"):
                        self.logger.warning("Local %s connection closed", label)
                        break
                    # serial.read() returns b"" on timeout — that's normal
                    continue
                self._sock.sendall(data)
            except (serial.SerialException, TypeError, AttributeError) as exc:
                if not self._stop_event.is_set():
                    self.logger.error("Serial read error: %s", exc)
                break
            except socket.timeout:
                continue
            except (OSError, socket.error) as exc:
                if not self._stop_event.is_set():
                    self.logger.error("Send error: %s", exc)
                break
        self.logger.debug("%s→remote thread exiting", label)

    def _remote_to_local(self) -> None:
        """Read data from remote TCP socket and forward to local endpoint."""
        label = {"tcp": "local-tcp", "web": "web", "serial": "serial"}.get(self.local_mode, "serial")
        self.logger.debug("remote→%s thread started", label)
        while not self._stop_event.is_set():
            try:
                data = self._sock.recv(self.buffer_size)
                if not data:
                    self.logger.warning("Remote TCP connection closed by server")
                    break
                self._local_write(data)
            except socket.timeout:
                continue
            except (serial.SerialException, TypeError, AttributeError) as exc:
                if not self._stop_event.is_set():
                    self.logger.error("Serial write error: %s", exc)
                break
            except (OSError, socket.error) as exc:
                if not self._stop_event.is_set():
                    self.logger.error("Recv/write error: %s", exc)
                break
        self.logger.debug("remote→%s thread exiting", label)

    # -- Thread management --------------------------------------------------

    def _start_forwarding_threads(self) -> None:
        """Spawn the two data-forwarding threads."""
        self._threads = [
            threading.Thread(
                target=self._local_to_remote, name="local→remote", daemon=True
            ),
            threading.Thread(
                target=self._remote_to_local, name="remote→local", daemon=True
            ),
        ]
        for t in self._threads:
            t.start()

    def _wait_for_threads(self) -> None:
        """Wait for forwarding threads to finish (with timeout)."""
        for t in self._threads:
            t.join(timeout=3)
        self._threads.clear()

    # -- SSH reverse tunnel (web mode) ----------------------------------------

    def _run_ssh_tunnel(self) -> None:
        """Web UI bridge using SSH reverse port forwarding.

        Runs:
            ssh -N -o StrictHostKeyChecking=no -o ServerAliveInterval=15 \
                -R <port_y>:<local_host>:<local_port> root@<server_host>

        SSH multiplexes all browser connections through a single tunnel,
        solving the problem where browsers open many parallel HTTP
        connections that the raw TCP relay can't handle (it only allows
        one connection per port, so each new browser connection evicts
        the previous one).

        The channel's port_y is read from the device config's channel
        settings.  Automatically restarts on failure.
        """
        local_host = self.web_cfg.get("host", "127.0.0.1")
        local_port = self.web_cfg.get("port", 8080)
        # port_y comes from the device config's channel settings
        channel_cfg = self._config.get("channel", {})
        remote_port = channel_cfg.get("port_y_port", self.server_port + 1)

        cmd = [
            "ssh", "-N",
            "-o", "StrictHostKeyChecking=no",
            "-o", "UserKnownHostsFile=/dev/null",
            "-o", "ServerAliveInterval=15",
            "-o", "ServerAliveCountMax=3",
            "-o", "ExitOnForwardFailure=yes",
            "-R", "%d:%s:%d" % (remote_port, local_host, local_port),
            "root@%s" % self.server_host,
        ]
        self.logger.info("SSH tunnel: %s", " ".join(cmd))

        while not self._stop_event.is_set() and not self._disconnect_event.is_set():
            try:
                proc = subprocess.Popen(
                    cmd,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.PIPE,
                )
            except OSError as exc:
                self.logger.error(
                    "Failed to start SSH tunnel: %s — retrying in %ds",
                    exc, self.reconnect_interval,
                )
                self._stop_event.wait(self.reconnect_interval)
                continue

            last_lines = []
            stderr_t = threading.Thread(
                target=self._drain_socat_stderr,
                args=(proc, "ssh", last_lines),
                daemon=True,
            )
            stderr_t.start()

            self._socat_proc = proc
            self._connected = True
            self.logger.info("SSH tunnel started (pid=%d) — remote port %d → %s:%d",
                             proc.pid, remote_port, local_host, local_port)

            while not self._stop_event.is_set() and not self._disconnect_event.is_set():
                if proc.poll() is not None:
                    break
                self._stop_event.wait(0.5)

            self._connected = False

            if proc.poll() is None:
                self.logger.info("Terminating SSH tunnel (pid=%d)", proc.pid)
                proc.terminate()
                try:
                    proc.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait()
            stderr_t.join(timeout=2)

            rc = proc.returncode
            if rc not in (0, -15, 255):  # 255 = SSH connection closed
                self.logger.warning("SSH tunnel exited unexpectedly (code=%d)", rc)
                if last_lines:
                    for ln in last_lines[-5:]:
                        self.logger.warning("[ssh] %s", ln)
            else:
                self.logger.info("SSH tunnel exited (code=%d)", rc)
            self._socat_proc = None

            if self._stop_event.is_set() or self._disconnect_event.is_set():
                break

            self.logger.info("Reconnecting SSH tunnel in %ds...", self.reconnect_interval)
            self._stop_event.wait(self.reconnect_interval)

        self.logger.info("SSH tunnel stopped")

    # -- socat bridge (TCP mode) --------------------------------------------

    def _run_socat(self) -> None:
        """
        TCP bridge loop using a socat subprocess.

        Runs:
            socat TCP:<relay_host>:<relay_port> TCP:<local_host>:<local_port>

        socat connects both sides before any data flows, eliminating the
        handshake race conditions that break SSH/TLS through the Python
        threading bridge.  Automatically restarts on failure until
        stop/disconnect is requested.
        """
        if self.local_mode == "web":
            local_host = self.web_cfg.get("host", "127.0.0.1")
            local_port = self.web_cfg.get("port", 8080)
        else:
            local_host = self.local_tcp_cfg["host"]
            local_port = self.local_tcp_cfg["port"]
        debug_flags = ["-d", "-d", "-x"] if self._socat_debug else []
        cmd = ["socat"] + debug_flags + [
            "TCP:%s:%d" % (self.server_host, self.server_port),
            "TCP:%s:%d" % (local_host, local_port),
        ]
        self.logger.info("socat bridge: %s", " ".join(cmd))

        while not self._stop_event.is_set() and not self._disconnect_event.is_set():
            last_lines = []
            try:
                proc = subprocess.Popen(
                    cmd,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.PIPE,
                )
            except OSError as exc:
                self.logger.error(
                    "Failed to start socat: %s — retrying in %ds",
                    exc, self.reconnect_interval,
                )
                self._stop_event.wait(self.reconnect_interval)
                continue

            stderr_t = threading.Thread(
                target=self._drain_socat_stderr,
                args=(proc, "tcp", last_lines),
                daemon=True,
            )
            stderr_t.start()

            self._socat_proc = proc
            self._connected = True
            self.logger.info("socat started (pid=%d)", proc.pid)

            # Block until socat exits or we are asked to stop.
            while not self._stop_event.is_set() and not self._disconnect_event.is_set():
                if proc.poll() is not None:
                    break
                self._stop_event.wait(0.5)

            self._connected = False

            # Terminate socat if still running (stop/disconnect was requested).
            if proc.poll() is None:
                self.logger.info("Terminating socat (pid=%d)", proc.pid)
                proc.terminate()
                try:
                    proc.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait()
            stderr_t.join(timeout=2)

            rc = proc.returncode
            if rc not in (0, -15):   # -15 = SIGTERM (our own terminate)
                self.logger.warning("socat exited unexpectedly (code=%d)", rc)
                if not self._socat_debug and last_lines:
                    for ln in last_lines[-5:]:
                        self.logger.warning("[socat/tcp] %s", ln)
            else:
                self.logger.info("socat exited (code=%d)", rc)
            self._socat_proc = None

            if self._stop_event.is_set() or self._disconnect_event.is_set():
                break

            self.logger.info("Reconnecting in %ds...", self.reconnect_interval)
            self._stop_event.wait(self.reconnect_interval)

        self.logger.info("socat bridge stopped")

    # -- Bridge loop (runs in its own thread) --------------------------------

    def _bridge_loop(self) -> None:
        """Connection loop: open local endpoint, connect remote TCP, forward, reconnect."""
        self.logger.info("Bridge starting (local_mode=%s)", self.local_mode)

        # For web mode, use SSH reverse port forwarding — it handles the
        # multiple concurrent HTTP connections that browsers require.
        # The raw TCP relay only allows 1 connection per port, so each
        # new browser connection evicts the previous one.
        if self.local_mode == "web":
            if shutil.which("ssh"):
                self._run_ssh_tunnel()
                return
            self.logger.warning(
                "Web mode requires SSH for reverse tunnel, but ssh not found; "
                "falling back to socat (may not work for web pages)"
            )

        # For TCP (SSH) mode, prefer socat — it handles handshake-based
        # protocols correctly by establishing both connections before
        # any data flows.
        if self.local_mode in ("tcp", "web") and self._use_socat:
            if self._socat_available():
                self._run_socat()
                return
            self.logger.warning(
                "use_socat=True but socat not found on PATH; "
                "falling back to Python TCP bridge"
            )

        while not self._stop_event.is_set() and not self._disconnect_event.is_set():
            # --- Open local endpoint ---
            if not self._local_is_open():
                try:
                    self._open_local()
                except (serial.SerialException, OSError, socket.error) as exc:
                    self.logger.error(
                        "Failed to open local endpoint: %s — retrying in %ds",
                        exc,
                        self.reconnect_interval,
                    )
                    self._stop_event.wait(self.reconnect_interval)
                    continue

            # --- Connect remote TCP ---
            try:
                self._sock = self._connect_tcp()
            except (OSError, socket.error) as exc:
                self.logger.error(
                    "Remote TCP connection failed: %s — retrying in %ds",
                    exc,
                    self.reconnect_interval,
                )
                self._stop_event.wait(self.reconnect_interval)
                continue

            # --- Start forwarding ---
            self._connected = True
            self._start_forwarding_threads()

            # Block until a thread exits or disconnect is requested.
            while not self._stop_event.is_set() and not self._disconnect_event.is_set():
                alive = [t for t in self._threads if t.is_alive()]
                if len(alive) < len(self._threads):
                    break
                self._stop_event.wait(0.5)

            self._connected = False

            if self._stop_event.is_set() or self._disconnect_event.is_set():
                break

            # --- Cleanup before reconnect ---
            self.logger.info("Connection lost — cleaning up before reconnect")
            self._stop_event.set()      # Signal threads to stop
            self._close_tcp()           # Unblock socket operations
            self._close_local()         # Unblock serial operations
            self._wait_for_threads()
            self._stop_event.clear()    # Reset for reconnect loop

            if self._disconnect_event.is_set():
                break

            self.logger.info(
                "Reconnecting in %d seconds...", self.reconnect_interval
            )
            self._disconnect_event.wait(self.reconnect_interval)
            if self._disconnect_event.is_set():
                break

        # Final cleanup — close everything and free the local endpoint.
        self._close_tcp()
        self._close_local()
        self._wait_for_threads()
        self._connected = False
        self.logger.info("Bridge stopped")

    # -- Public connect / disconnect / status --------------------------------

    def connect(self) -> None:
        """Start the bridge (open serial + TCP and begin forwarding)."""
        if self._bridge_thread is not None and self._bridge_thread.is_alive():
            self.logger.warning("Bridge is already running")
            return

        self._stop_event.clear()
        self._disconnect_event.clear()
        self._bridge_thread = threading.Thread(
            target=self._bridge_loop, name="bridge-loop", daemon=True
        )
        self._bridge_thread.start()
        self.logger.info("Connect requested — bridge thread started")

    def disconnect(self) -> None:
        """Stop the bridge, close remote TCP and release the local endpoint."""
        if self._bridge_thread is None or not self._bridge_thread.is_alive():
            self.logger.warning("Bridge is not running")
            return

        self.logger.info("Disconnect requested")
        self._disconnect_event.set()
        self._stop_event.set()
        # Force-close sockets/serial so blocked reads unblock immediately.
        self._close_tcp()
        self._close_local()
        # Terminate socat process if running.
        if self._socat_proc is not None and self._socat_proc.poll() is None:
            self._socat_proc.terminate()
        self._bridge_thread.join(timeout=5)
        self._wait_for_threads()
        self._connected = False
        self.logger.info("Disconnected — local endpoint released")

    def test_serial(self) -> str:
        """Open the serial port, send a test byte, and read back for up to 1 s.

        Returns a multi-line diagnostic string.  Useful for verifying that
        the serial port is readable and writable without needing a loopback
        device (TX must be shorted to RX for the echo check to work).
        """
        import binascii
        port = self.serial_cfg.get("port", "")
        baud = self.serial_cfg.get("baudrate", 9600)
        lines = []
        lines.append("  Port      : %s @ %d baud" % (port, baud))

        try:
            import serial as _serial
            cfg = self.serial_cfg
            parity = PARITY_MAP.get(cfg.get("parity", "N"), _serial.PARITY_NONE)
            stopbits = STOPBITS_MAP.get(cfg.get("stopbits", 1), _serial.STOPBITS_ONE)
            ser = _serial.Serial(
                port=port,
                baudrate=baud,
                bytesize=cfg.get("bytesize", 8),
                parity=parity,
                stopbits=stopbits,
                timeout=1.0,
            )
            lines.append("  Open      : OK")
        except Exception as exc:
            lines.append("  Open      : FAILED — %s" % exc)
            return "\n".join(lines)

        # Write a test byte and attempt to read it back (requires TX-RX loopback).
        TEST_BYTE = b"\xAA"
        try:
            ser.reset_input_buffer()
            ser.write(TEST_BYTE)
            ser.flush()
            lines.append("  Write 0xAA: OK")
        except Exception as exc:
            lines.append("  Write 0xAA: FAILED — %s" % exc)
            ser.close()
            return "\n".join(lines)

        echo = ser.read(1)
        if echo == TEST_BYTE:
            lines.append("  Read echo : OK (0x%s) — TX and RX are looped" % binascii.hexlify(echo).decode())
        elif echo:
            lines.append("  Read echo : Got 0x%s (not our byte — RX is alive but not looped)" % binascii.hexlify(echo).decode())
        else:
            lines.append("  Read echo : TIMEOUT — no data received from serial port")
            lines.append("  Hint: short TX to RX to test loopback, or check RS485 direction control (DE/RE pins)")

        ser.close()
        return "\n".join(lines)

    def status(self) -> str:
        """Return a human-readable status string."""
        if self.local_mode in ("tcp", "web"):
            local_label = "Web UI" if self.local_mode == "web" else "Local TCP"
            if self._socat_proc is not None:
                if self._socat_proc.poll() is None:
                    local_status = "CONNECTED via socat (pid=%d)" % self._socat_proc.pid
                else:
                    local_status = "DISCONNECTED (socat exited, code=%s)" % self._socat_proc.returncode
            elif self._local_sock is not None:
                try:
                    peer = self._local_sock.getpeername()
                    local_status = "CONNECTED (%s:%s)" % (peer[0], peer[1])
                except OSError:
                    local_status = "DISCONNECTED"
            else:
                local_status = "DISCONNECTED"
        else:
            local_label = "Serial"
            if self._ser is not None and self._ser.is_open:
                local_status = "OPEN (%s @ %s)" % (self._ser.port, self._ser.baudrate)
            else:
                local_status = "CLOSED"

        remote_status = "DISCONNECTED"
        if self._sock is not None:
            try:
                self._sock.getpeername()
                remote_status = "CONNECTED (%s:%s)" % (self.server_host, self.server_port)
            except OSError:
                remote_status = "DISCONNECTED"

        bridge_status = "RUNNING" if self._connected else "STOPPED"
        return (
            "  Bridge    : %s\n"
            "  Mode      : %s\n"
            "  %-10s: %s\n"
            "  Remote TCP: %s"
        ) % (bridge_status, self.local_mode, local_label, local_status, remote_status)

    def stop(self) -> None:
        """Signal the bridge to shut down gracefully (used on quit)."""
        self.logger.info("Shutdown requested")
        self._stop_event.set()
        self._disconnect_event.set()
        self._close_tcp()
        self._close_local()
        # Terminate socat process if running.
        if self._socat_proc is not None and self._socat_proc.poll() is None:
            self._socat_proc.terminate()
        if self._bridge_thread is not None:
            self._bridge_thread.join(timeout=5)
        self._wait_for_threads()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def setup_logging(level_name: str) -> None:
    """Configure root logger with timestamp format."""
    level = getattr(logging, level_name.upper(), logging.INFO)
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="RS485-to-TCP bridge client"
    )
    parser.add_argument(
        "--config",
        type=str,
        default=str(DEFAULT_CONFIG_PATH),
        help="Path to client_config.json (default: %(default)s)",
    )
    args = parser.parse_args()

    config = load_config(args.config)
    setup_logging(config.get("log_level", "INFO"))

    bridge = RS485TcpBridge(config)

    # Auto-connect on startup if configured.
    if bridge.auto_connect:
        bridge.connect()

    print("\nRS485-to-TCP Bridge Client")
    print("Commands: connect | disconnect | status | test | quit\n")

    try:
        while True:
            try:
                cmd = input("bridge> ").strip().lower()
            except EOFError:
                break

            if cmd == "connect":
                bridge.connect()
            elif cmd == "disconnect":
                bridge.disconnect()
            elif cmd == "status":
                print(bridge.status())
            elif cmd == "test":
                print(bridge.test_serial())
            elif cmd in ("quit", "exit"):
                break
            elif cmd == "":
                continue
            else:
                print("Unknown command: %s" % cmd)
                print("Commands: connect | disconnect | status | test | quit")
    except KeyboardInterrupt:
        pass

    bridge.stop()
    logging.getLogger("rs485_bridge").info("Exited")


if __name__ == "__main__":
    main()
