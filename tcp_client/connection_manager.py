#!/usr/bin/env python3
"""
Connection Manager Web UI for RS485 TCP Client.

Provides a web-based interface to:
  - View and edit client_config.json
  - Control the RS485TcpBridge (connect / disconnect / status)
  - Periodically check in to a remote server and react to responses

Compatible with Python 3.4+.
"""

import argparse
import json
import logging
import logging.handlers
import os
import socket
import struct
import sys
import threading
import time

try:
    from http.server import HTTPServer, BaseHTTPRequestHandler
except ImportError:
    from BaseHTTPServer import HTTPServer, BaseHTTPRequestHandler

try:
    from urllib.request import Request, urlopen
    from urllib.error import URLError, HTTPError
except ImportError:
    from urllib2 import Request, urlopen, URLError, HTTPError

# ---------------------------------------------------------------------------
# Path setup – import from rs485_tcp_client in the same directory
# ---------------------------------------------------------------------------
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)

from rs485_tcp_client import RS485TcpBridge, load_config

CONFIG_PATH = os.path.join(SCRIPT_DIR, "client_config.json")
WEB_PORT = 5000
CHECKIN_INTERVAL = 30  # seconds

logger = logging.getLogger("connection_manager")


# ---------------------------------------------------------------------------
# Utility: get end0 MAC address (Linux)
# ---------------------------------------------------------------------------
def get_mac_address(interface="end0"):
    """Return the MAC address of *interface* as a colon-separated string."""
    try:
        path = "/sys/class/net/%s/address" % interface
        with open(path) as fh:
            return fh.read().strip().upper()
    except (IOError, OSError):
        pass
    # Fallback: try fcntl/ioctl
    try:
        import fcntl
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        info = fcntl.ioctl(
            sock.fileno(),
            0x8927,  # SIOCGIFHWADDR
            struct.pack("256s", interface.encode("utf-8")[:15]),
        )
        sock.close()
        mac_bytes = info[18:24]
        return ":".join(["%02X" % b for b in bytearray(mac_bytes)])
    except Exception:
        return "00:00:00:00:00:00"


# ---------------------------------------------------------------------------
# Application state (module-level singletons)
# ---------------------------------------------------------------------------
class AppState(object):
    def __init__(self, interface="eth0"):
        self.lock = threading.Lock()
        self.config = {}
        self.bridge = None
        self.mac_address = get_mac_address(interface)
        # Check-in bookkeeping
        self.checkin_running = False
        self.last_checkin_time = None
        self.last_checkin_response = None
        self.last_checkin_error = None
        self.config_version = 0
        # Event log (ring buffer)
        self._log = []
        self._log_max = 200

    # -- config helpers -----------------------------------------------------
    def load_config(self):
        self.config = load_config(CONFIG_PATH)

    def save_config(self, new_cfg):
        with open(CONFIG_PATH, "w") as fh:
            json.dump(new_cfg, fh, indent=2)
        self.config = new_cfg
        self.config_version += 1

    # -- bridge helpers -----------------------------------------------------
    def init_bridge(self):
        if self.bridge is not None:
            try:
                self.bridge.stop()
            except Exception:
                pass
        self.bridge = RS485TcpBridge(self.config)

    def bridge_status_dict(self):
        """Return a JSON-serialisable dict of bridge + app state."""
        connected = False
        status_text = "No bridge initialised"
        if self.bridge is not None:
            status_text = self.bridge.status()
            connected = getattr(self.bridge, "_connected", False)
        return {
            "connected": connected,
            "status_text": status_text,
            "mac_address": self.mac_address,
            "checkin_running": self.checkin_running,
            "last_checkin_time": self.last_checkin_time,
            "last_checkin_response": self.last_checkin_response,
            "last_checkin_error": self.last_checkin_error,
            "config_version": self.config_version,
        }

    # -- event log ----------------------------------------------------------
    def log_event(self, msg):
        ts = time.strftime("%Y-%m-%d %H:%M:%S")
        entry = "[%s] %s" % (ts, msg)
        logger.info(msg)
        with self.lock:
            self._log.append(entry)
            if len(self._log) > self._log_max:
                self._log = self._log[-self._log_max:]

    def get_log(self):
        with self.lock:
            return list(self._log)


APP = None  # Initialised in main()


# ---------------------------------------------------------------------------
# Check-in thread
# ---------------------------------------------------------------------------
def _do_checkin():
    """Execute a single check-in request and handle the response."""
    host = APP.config.get("server", {}).get("host", "")
    if not host:
        APP.last_checkin_error = "No server host configured"
        APP.log_event("Check-in skipped: no server host configured")
        return

    url = "http://%s:5000/api/device/checkin" % host
    body = json.dumps({"mac_address": APP.mac_address}).encode("utf-8")

    req = Request(url, data=body, headers={"Content-Type": "application/json"})
    try:
        resp = urlopen(req, timeout=10)
        raw = resp.read().decode("utf-8")
        APP.last_checkin_time = time.strftime("%Y-%m-%d %H:%M:%S")
        APP.last_checkin_error = None
        APP.last_checkin_response = raw
        APP.log_event("Check-in OK: %s" % raw[:200])

        # Parse response
        try:
            data = json.loads(raw)
        except (ValueError, TypeError):
            APP.log_event("Check-in response is not valid JSON")
            return

        # Case 1: "status" == "OK" or "ok" -> disconnect
        status_val = data.get("status", "") if isinstance(data, dict) else ""
        if isinstance(status_val, str) and status_val.upper() == "OK":
            APP.log_event("Server returned status OK -> disconnecting bridge")
            if APP.bridge is not None:
                try:
                    APP.bridge.disconnect()
                except Exception as exc:
                    APP.log_event("Disconnect error: %s" % exc)
            return

        # Case 2: complete config -> update and connect (only if not already connected)
        if isinstance(data, dict) and ("server" in data or "serial" in data):
            # Check both _connected AND whether the bridge thread is alive.
            # _connected can be briefly False during socat/serial reconnect
            # cycles even though the bridge thread is still actively managing
            # its connection.  We must NOT tear down the bridge in that case.
            bridge_active = False
            if APP.bridge is not None:
                bt = getattr(APP.bridge, "_bridge_thread", None)
                if getattr(APP.bridge, "_connected", False):
                    bridge_active = True
                elif bt is not None and bt.is_alive():
                    # Bridge thread is alive but _connected is False —
                    # bridge is in a reconnect cycle.  Leave it alone.
                    bridge_active = True

            # If the bridge is active (connected or reconnecting), do NOT
            # reinitialize — just save config silently if it changed.
            if bridge_active:
                config_changed = (data != APP.config)
                if config_changed:
                    APP.save_config(data)
                    APP.log_event("Bridge active — config saved (no reconnect)")
                return

            # Bridge is truly idle — apply config and connect.
            try:
                config_changed = (data != APP.config)
                if config_changed:
                    APP.log_event("Received new config from server")
                    APP.save_config(data)
                APP.init_bridge()
                APP.bridge.connect()
                APP.log_event("Bridge connected%s" % (" with new config" if config_changed else ""))
            except Exception as exc:
                APP.log_event("Error applying config: %s" % exc)
            return

    except (URLError, HTTPError) as exc:
        APP.last_checkin_error = str(exc)
        APP.last_checkin_time = time.strftime("%Y-%m-%d %H:%M:%S")
        APP.log_event("Check-in failed: %s" % exc)
    except Exception as exc:
        APP.last_checkin_error = str(exc)
        APP.log_event("Check-in error: %s" % exc)


def checkin_loop(stop_event):
    """Background loop that checks in every CHECKIN_INTERVAL seconds."""
    APP.checkin_running = True
    APP.log_event("Check-in thread started (interval=%ds)" % CHECKIN_INTERVAL)
    while not stop_event.is_set():
        _do_checkin()
        stop_event.wait(CHECKIN_INTERVAL)
    APP.checkin_running = False
    APP.log_event("Check-in thread stopped")


# ---------------------------------------------------------------------------
# Embedded HTML / JS / CSS
# ---------------------------------------------------------------------------
HTML_PAGE = r"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Connection Manager</title>
<style>
* { box-sizing: border-box; margin: 0; padding: 0; }
body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
       background: #0f1117; color: #e1e4e8; padding: 16px; }
h1 { font-size: 1.4em; margin-bottom: 12px; color: #58a6ff; }
h2 { font-size: 1.1em; margin-bottom: 8px; color: #79c0ff; border-bottom: 1px solid #21262d;
     padding-bottom: 4px; }
.grid { display: flex; flex-wrap: wrap; gap: 12px; }
.panel { background: #161b22; border: 1px solid #30363d; border-radius: 8px;
         padding: 14px; flex: 1 1 340px; min-width: 320px; }
label { display: block; font-size: 0.85em; color: #8b949e; margin: 6px 0 2px; }
input, select { width: 100%; padding: 6px 8px; border: 1px solid #30363d; border-radius: 4px;
                background: #0d1117; color: #e1e4e8; font-size: 0.9em; }
input:focus, select:focus { border-color: #58a6ff; outline: none; }
.btn { display: inline-block; padding: 7px 16px; border: none; border-radius: 4px;
       cursor: pointer; font-size: 0.9em; margin: 4px 4px 4px 0; color: #fff; }
.btn-primary { background: #238636; }
.btn-primary:hover { background: #2ea043; }
.btn-danger  { background: #da3633; }
.btn-danger:hover  { background: #f85149; }
.btn-info    { background: #1f6feb; }
.btn-info:hover    { background: #388bfd; }
.btn-warn    { background: #9e6a03; }
.btn-warn:hover    { background: #bb8009; }
.status-dot { display: inline-block; width: 10px; height: 10px; border-radius: 50%;
              margin-right: 6px; vertical-align: middle; }
.dot-green  { background: #3fb950; }
.dot-red    { background: #f85149; }
.dot-yellow { background: #d29922; }
#log-area { background: #0d1117; border: 1px solid #30363d; border-radius: 4px;
            padding: 8px; height: 200px; overflow-y: auto; font-family: monospace;
            font-size: 0.8em; line-height: 1.5; white-space: pre-wrap; margin-top: 8px; }
.field-row { display: flex; gap: 8px; }
.field-row > div { flex: 1; }
.toast { position: fixed; top: 16px; right: 16px; padding: 10px 18px; border-radius: 6px;
         color: #fff; font-size: 0.9em; z-index: 999; opacity: 0; transition: opacity 0.3s; }
.toast.show { opacity: 1; }
.toast-ok   { background: #238636; }
.toast-err  { background: #da3633; }
#status-text { margin: 8px 0; padding: 10px; background: #0d1117; border-radius: 4px;
               font-family: monospace; font-size: 0.85em; white-space: pre-wrap;
               border: 1px solid #30363d; }
.checkin-info { font-size: 0.85em; color: #8b949e; margin: 4px 0; }
.checkin-info span { color: #e1e4e8; }
</style>
</head>
<body>

<h1>&#x1F50C; Connection Manager</h1>

<div id="toast" class="toast"></div>

<div class="grid">

  <!-- Config Panel -->
  <div class="panel" style="flex: 2 1 500px;">
    <h2>Configuration</h2>
    <label>Local Mode</label>
    <select id="cfg-local_mode">
      <option value="serial">serial</option>
      <option value="tcp">tcp</option>
      <option value="web">web</option>
    </select>

    <h2 style="margin-top:12px;">Serial</h2>
    <div class="field-row">
      <div><label>Port</label><input id="cfg-serial-port" placeholder="/dev/ttyS2"></div>
      <div><label>Baudrate</label><input id="cfg-serial-baudrate" type="number" placeholder="9600"></div>
    </div>
    <div class="field-row">
      <div><label>Bytesize</label>
        <select id="cfg-serial-bytesize"><option value="5">5</option><option value="6">6</option>
        <option value="7">7</option><option value="8" selected>8</option></select></div>
      <div><label>Parity</label>
        <select id="cfg-serial-parity"><option value="N">N</option><option value="E">E</option>
        <option value="O">O</option></select></div>
      <div><label>Stopbits</label>
        <select id="cfg-serial-stopbits"><option value="1">1</option><option value="1.5">1.5</option>
        <option value="2">2</option></select></div>
      <div><label>Timeout</label><input id="cfg-serial-timeout" type="number" step="0.01" placeholder="0.1"></div>
    </div>

    <h2 style="margin-top:12px;">Local TCP</h2>
    <div class="field-row">
      <div><label>Host</label><input id="cfg-local_tcp-host" placeholder="127.0.0.1"></div>
      <div><label>Port</label><input id="cfg-local_tcp-port" type="number" placeholder="22"></div>
    </div>

    <h2 style="margin-top:12px;">Web UI</h2>
    <div class="field-row">
      <div><label>Host</label><input id="cfg-web-host" placeholder="127.0.0.1"></div>
      <div><label>Port</label><input id="cfg-web-port" type="number" placeholder="8080"></div>
    </div>

    <h2 style="margin-top:12px;">Server</h2>
    <div class="field-row">
      <div><label>Host</label><input id="cfg-server-host" placeholder="139.162.31.224"></div>
      <div><label>Port</label><input id="cfg-server-port" type="number" placeholder="8001"></div>
    </div>

    <h2 style="margin-top:12px;">Options</h2>
    <div class="field-row">
      <div><label>Auto Connect</label>
        <select id="cfg-auto_connect"><option value="true">true</option><option value="false">false</option></select></div>
      <div><label>Reconnect Interval</label><input id="cfg-reconnect_interval" type="number" placeholder="5"></div>
      <div><label>Buffer Size</label><input id="cfg-buffer_size" type="number" placeholder="4096"></div>
    </div>
    <div class="field-row">
      <div><label>Use Socat</label>
        <select id="cfg-use_socat"><option value="true">true</option><option value="false">false</option></select></div>
      <div><label>Use Socat Serial</label>
        <select id="cfg-use_socat_serial"><option value="true">true</option><option value="false">false</option></select></div>
      <div><label>Log Level</label>
        <select id="cfg-log_level"><option value="DEBUG">DEBUG</option><option value="INFO">INFO</option>
        <option value="WARNING">WARNING</option><option value="ERROR">ERROR</option></select></div>
    </div>
    <div style="margin-top:10px;">
      <button class="btn btn-primary" onclick="saveConfig()">&#x1F4BE; Save Configuration</button>
    </div>
  </div>

  <!-- Control + Check-in Panel -->
  <div class="panel">
    <h2>Bridge Control</h2>
    <div>
      <span id="status-dot" class="status-dot dot-red"></span>
      <span id="status-label">Disconnected</span>
    </div>
    <div id="status-text">Loading...</div>
    <div>
      <button class="btn btn-primary" onclick="doConnect()">&#x25B6; Connect</button>
      <button class="btn btn-danger" onclick="doDisconnect()">&#x23F9; Disconnect</button>
    </div>

    <h2 style="margin-top:16px;">Server Check-in</h2>
    <div class="checkin-info">MAC Address: <span id="ci-mac">--</span></div>
    <div class="checkin-info">Last Check-in: <span id="ci-time">--</span></div>
    <div class="checkin-info">Last Response: <span id="ci-resp">--</span></div>
    <div class="checkin-info">Error: <span id="ci-err">--</span></div>
    <div style="margin-top:8px;">
      <button class="btn btn-warn" onclick="doCheckin()">&#x1F4E1; Check In Now</button>
    </div>

    <h2 style="margin-top:16px;">Event Log</h2>
    <div id="log-area"></div>
  </div>

</div>

<script>
function xhr(method, url, body, cb) {
    var x = new XMLHttpRequest();
    x.open(method, url, true);
    if (body !== null) x.setRequestHeader("Content-Type", "application/json");
    x.onreadystatechange = function() {
        if (x.readyState === 4) cb(x.status, x.responseText);
    };
    x.send(body !== null ? JSON.stringify(body) : null);
}

function toast(msg, ok) {
    var t = document.getElementById("toast");
    t.textContent = msg;
    t.className = "toast show " + (ok ? "toast-ok" : "toast-err");
    setTimeout(function() { t.className = "toast"; }, 2500);
}

/* ---------- Config ---------- */
function loadConfig() {
    xhr("GET", "/api/config", null, function(s, r) {
        if (s !== 200) return;
        var c = JSON.parse(r);
        document.getElementById("cfg-local_mode").value = c.local_mode || "serial";
        var ser = c.serial || {};
        document.getElementById("cfg-serial-port").value = ser.port || "";
        document.getElementById("cfg-serial-baudrate").value = ser.baudrate || "";
        document.getElementById("cfg-serial-bytesize").value = String(ser.bytesize || 8);
        document.getElementById("cfg-serial-parity").value = ser.parity || "N";
        document.getElementById("cfg-serial-stopbits").value = String(ser.stopbits || 1);
        document.getElementById("cfg-serial-timeout").value = ser.timeout || "";
        var lt = c.local_tcp || {};
        document.getElementById("cfg-local_tcp-host").value = lt.host || "";
        document.getElementById("cfg-local_tcp-port").value = lt.port || "";
        var wu = c.web || {};
        document.getElementById("cfg-web-host").value = wu.host || "127.0.0.1";
        document.getElementById("cfg-web-port").value = wu.port || 8080;
        var sv = c.server || {};
        document.getElementById("cfg-server-host").value = sv.host || "";
        document.getElementById("cfg-server-port").value = sv.port || "";
        document.getElementById("cfg-auto_connect").value = String(c.auto_connect === true);
        document.getElementById("cfg-reconnect_interval").value = c.reconnect_interval || "";
        document.getElementById("cfg-buffer_size").value = c.buffer_size || "";
        document.getElementById("cfg-use_socat").value = String(c.use_socat === true);
        document.getElementById("cfg-use_socat_serial").value = String(c.use_socat_serial === true);
        document.getElementById("cfg-log_level").value = c.log_level || "DEBUG";
    });
}

function buildConfig() {
    return {
        local_mode: document.getElementById("cfg-local_mode").value,
        serial: {
            port: document.getElementById("cfg-serial-port").value,
            baudrate: parseInt(document.getElementById("cfg-serial-baudrate").value) || 9600,
            bytesize: parseInt(document.getElementById("cfg-serial-bytesize").value) || 8,
            parity: document.getElementById("cfg-serial-parity").value,
            stopbits: parseFloat(document.getElementById("cfg-serial-stopbits").value) || 1,
            timeout: parseFloat(document.getElementById("cfg-serial-timeout").value) || 0.1
        },
        local_tcp: {
            host: document.getElementById("cfg-local_tcp-host").value,
            port: parseInt(document.getElementById("cfg-local_tcp-port").value) || 22
        },
        web: {
            host: document.getElementById("cfg-web-host").value || "127.0.0.1",
            port: parseInt(document.getElementById("cfg-web-port").value) || 8080
        },
        server: {
            host: document.getElementById("cfg-server-host").value,
            port: parseInt(document.getElementById("cfg-server-port").value) || 8001
        },
        auto_connect: document.getElementById("cfg-auto_connect").value === "true",
        reconnect_interval: parseInt(document.getElementById("cfg-reconnect_interval").value) || 5,
        buffer_size: parseInt(document.getElementById("cfg-buffer_size").value) || 4096,
        use_socat: document.getElementById("cfg-use_socat").value === "true",
        use_socat_serial: document.getElementById("cfg-use_socat_serial").value === "true",
        log_level: document.getElementById("cfg-log_level").value
    };
}

function saveConfig() {
    xhr("POST", "/api/config", buildConfig(), function(s, r) {
        if (s === 200) toast("Configuration saved", true);
        else toast("Save failed: " + r, false);
    });
}

/* ---------- Bridge control ---------- */
function doConnect() {
    xhr("POST", "/api/connect", {}, function(s, r) {
        if (s === 200) toast("Connect initiated", true);
        else toast("Connect failed: " + r, false);
    });
}
function doDisconnect() {
    xhr("POST", "/api/disconnect", {}, function(s, r) {
        if (s === 200) toast("Disconnect initiated", true);
        else toast("Disconnect failed: " + r, false);
    });
}
function doCheckin() {
    xhr("POST", "/api/checkin", {}, function(s, r) {
        if (s === 200) toast("Check-in done", true);
        else toast("Check-in failed: " + r, false);
        refreshStatus();
    });
}

/* ---------- Status polling ---------- */
function refreshStatus() {
    xhr("GET", "/api/status", null, function(s, r) {
        if (s !== 200) return;
        var d = JSON.parse(r);
        var dot = document.getElementById("status-dot");
        var lbl = document.getElementById("status-label");
        if (d.connected) {
            dot.className = "status-dot dot-green";
            lbl.textContent = "Connected";
        } else {
            dot.className = "status-dot dot-red";
            lbl.textContent = "Disconnected";
        }
        document.getElementById("status-text").textContent = d.status_text || "--";
        document.getElementById("ci-mac").textContent = d.mac_address || "--";
        document.getElementById("ci-time").textContent = d.last_checkin_time || "--";
        document.getElementById("ci-resp").textContent = (d.last_checkin_response || "--").substring(0, 300);
        document.getElementById("ci-err").textContent = d.last_checkin_error || "None";
        var cv = d.config_version || 0;
        if (_lastConfigVersion >= 0 && cv !== _lastConfigVersion) {
            loadConfig();
            toast("Config updated by server", true);
        }
        _lastConfigVersion = cv;
    });
    xhr("GET", "/api/log", null, function(s, r) {
        if (s !== 200) return;
        var lines = JSON.parse(r);
        var el = document.getElementById("log-area");
        el.textContent = lines.join("\n");
        el.scrollTop = el.scrollHeight;
    });
}

var _lastConfigVersion = -1;

loadConfig();
refreshStatus();
setInterval(refreshStatus, 5000);
</script>
</body>
</html>"""


# ---------------------------------------------------------------------------
# HTTP request handler
# ---------------------------------------------------------------------------
class Handler(BaseHTTPRequestHandler):

    def log_message(self, fmt, *args):
        logger.debug(fmt % args)

    def _send_json(self, code, obj):
        body = json.dumps(obj).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_html(self, html):
        body = html.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_body(self):
        length = int(self.headers.get("Content-Length", 0))
        if length == 0:
            return b""
        return self.rfile.read(length)

    # -- GET ----------------------------------------------------------------
    def do_GET(self):
        if self.path == "/":
            self._send_html(HTML_PAGE)

        elif self.path == "/api/config":
            APP.load_config()
            self._send_json(200, APP.config)

        elif self.path == "/api/status":
            self._send_json(200, APP.bridge_status_dict())

        elif self.path == "/api/log":
            self._send_json(200, APP.get_log())

        else:
            self.send_error(404)

    # -- POST ---------------------------------------------------------------
    def do_POST(self):
        if self.path == "/api/config":
            try:
                raw = self._read_body()
                new_cfg = json.loads(raw.decode("utf-8"))
                # Merge: start from existing config, update with UI fields
                merged = dict(APP.config)
                for key, val in new_cfg.items():
                    if isinstance(val, dict) and isinstance(merged.get(key), dict):
                        merged[key] = dict(merged[key])
                        merged[key].update(val)
                    else:
                        merged[key] = val
                APP.save_config(merged)
                APP.init_bridge()
                APP.log_event("Configuration updated via web UI")
                self._send_json(200, {"ok": True})
            except Exception as exc:
                self._send_json(400, {"error": str(exc)})

        elif self.path == "/api/connect":
            try:
                if APP.bridge is None:
                    APP.init_bridge()
                APP.bridge.connect()
                APP.log_event("Bridge connect requested via web UI")
                self._send_json(200, {"ok": True})
            except Exception as exc:
                self._send_json(500, {"error": str(exc)})

        elif self.path == "/api/disconnect":
            try:
                if APP.bridge is not None:
                    APP.bridge.disconnect()
                    APP.log_event("Bridge disconnect requested via web UI")
                self._send_json(200, {"ok": True})
            except Exception as exc:
                self._send_json(500, {"error": str(exc)})

        elif self.path == "/api/checkin":
            try:
                _do_checkin()
                self._send_json(200, {"ok": True})
            except Exception as exc:
                self._send_json(500, {"error": str(exc)})

        else:
            self.send_error(404)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    global APP

    parser = argparse.ArgumentParser(
        description="Connection Manager Web UI for RS485 TCP Client")
    parser.add_argument("--port", type=int, default=None,
                        help="Web UI port (default: from WEB_PORT)")
    parser.add_argument("--config", default=None,
                        help="Path to client_config.json")
    parser.add_argument("--checkin-interval", type=int, default=None,
                        help="Check-in interval in seconds (default: 30)")
    parser.add_argument("--interface", default="eth0",
                        help="Network interface for MAC address (default: eth0)")
    parser.add_argument("--log-file", default=None,
                        help="Log file path (optional)")
    parser.add_argument("--pid-file", default=None,
                        help="PID file path (optional)")
    args = parser.parse_args()

    # Apply overrides
    global CONFIG_PATH, WEB_PORT, CHECKIN_INTERVAL
    if args.config:
        CONFIG_PATH = args.config
    if args.port:
        WEB_PORT = args.port
    if args.checkin_interval:
        CHECKIN_INTERVAL = args.checkin_interval

    # Create application state with the correct interface
    APP = AppState(interface=args.interface)

    # Load config first so we can use its log_level
    APP.load_config()
    log_level = getattr(logging, APP.config.get("log_level", "INFO").upper(), logging.INFO)
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    # Set up file logging if requested
    if args.log_file:
        try:
            fh = logging.handlers.RotatingFileHandler(
                args.log_file, maxBytes=5 * 1024 * 1024, backupCount=3)
            fh.setLevel(logging.DEBUG)
            fh.setFormatter(logging.Formatter(
                "%(asctime)s [%(levelname)s] %(name)s: %(message)s"))
            logging.getLogger().addHandler(fh)
        except (IOError, OSError) as exc:
            logger.warning("Could not open log file %s: %s", args.log_file, exc)

    APP.init_bridge()
    APP.log_event("Connection Manager started")
    APP.log_event("MAC address: %s" % APP.mac_address)
    APP.log_event("Interface: %s" % args.interface)

    # Auto-connect if configured
    if APP.config.get("auto_connect", False):
        APP.log_event("Auto-connect enabled, connecting bridge...")
        try:
            APP.bridge.connect()
            APP.log_event("Bridge connected (auto)")
        except Exception as exc:
            APP.log_event("Auto-connect failed: %s" % exc)

    # Start check-in background thread
    checkin_stop = threading.Event()
    checkin_thread = threading.Thread(target=checkin_loop, args=(checkin_stop,))
    checkin_thread.daemon = True
    checkin_thread.start()

    # Start HTTP server
    server = HTTPServer(("0.0.0.0", WEB_PORT), Handler)
    APP.log_event("Web UI listening on 0.0.0.0:%d" % WEB_PORT)
    logger.info("Serving on http://0.0.0.0:%d", WEB_PORT)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        APP.log_event("Shutting down...")
        checkin_stop.set()
        if APP.bridge is not None:
            APP.bridge.stop()
        server.server_close()
        logger.info("Stopped.")


if __name__ == "__main__":
    main()
