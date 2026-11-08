# RS485-to-TCP Bridge — Client & Server

A pair of Python scripts that allow remote access to RS485 energy meters (or other local endpoints) over TCP.

## CP2B deployment on Ubuntu 24.04.4 LTS

This directory is the client source; run installation and maintenance commands
with sudo. The Ethernet default remains `eth0`, with `--interface` overrides
supported. Existing `client_config.json` is preserved by the installer; the
configuration template is not rewritten by this port.

For the first deployment of these changes, rerun the installer from this source
directory, specifying the **currently installed** directory and interface:

```bash
# First inspect the installed path and arguments.
systemctl show connection-manager.service -p WorkingDirectory -p ExecStart --no-pager
# Example only: substitute the path/interface shown above.
sudo bash install.sh --install-dir /home/admin/projects/tcp_client --interface eth0
sudo systemctl status connection-manager.service --no-pager
```

Do not choose a different installation path inadvertently. Running the installer
enables and starts/restarts the service; if it should remain disabled, restore
that state explicitly with `sudo systemctl disable --now connection-manager`.
This requires a maintenance window because existing tunnels are interrupted.
The web UI's admin-only Connection Manager controls operate this same unit
regardless of its installed path.

The installer includes OpenSSH client tools. The systemd service runs as root
and now permits reading `/root/.ssh` for web-mode key authentication.
For installations outside `/home`, home directories remain read-only in the
service sandbox; `/home` installations retain the existing writable application
exception. Other systemd hardening remains enabled.

An admin user's SSH key is not automatically the service's root key. If key
authentication has not already been configured, run
`sudo -H bash setup_ssh_tunnel.sh <server-ip>` separately during provisioning.
It may prompt for the remote root password; no keys are generated/copied by
the installer. Existing SSH host-key verification behavior is unchanged by this port.

Web-mode status stays **CONNECTING** until OpenSSH confirms remote forwarding;
authentication alone is not treated as a successful tunnel. SSH uses batch mode,
a connection timeout, and reports forwarding/authentication failures. Check-in
and auto-connect logs describe a connection request, not guaranteed success.

For later code-only updates, the existing `sudo bash update.sh` still detects
the installed directory and preserves configuration. It does **not** regenerate
the service unit or install dependencies, so it is insufficient for this first
service-sandbox/OpenSSH update.

Run the isolated regression tests from this directory with Python and pyserial:

```bash
python -m unittest discover -s . -p 'test_*.py' -v
```

## Architecture

```
                         RELAY SERVER
                   ┌──────────────────────┐
Energy Meters      │                      │      Modbus Master / HMI
  ↕ RS485          │  port_x ←──→ port_y  │           ↕ RS485
┌────────┐  TCP    │  (9001)       (9002)  │    TCP  ┌────────┐
│Client 1│────────→│     channel "site_A"  │←───────│Client 2│
└────────┘         │                      │         └────────┘
                   │  port_x ←──→ port_y  │
                   │  (9003)       (9004)  │
                   │     channel "site_B"  │
                   └──────────────────────┘
```

Each **channel** on the server pairs two TCP ports. When both sides are connected, bytes flow transparently between them. The server supports any number of channels for multiple client pairs.

Each **client** bridges a local endpoint to the remote server. The local endpoint can be:
- **RS485 serial port** (e.g. COM3) — for energy meter access
- **Local TCP port** (e.g. localhost:22) — for tunneling services like SSH

## Requirements

- Python 3.10+
- **Server:** No external dependencies (stdlib only)
- **Client:** `pyserial >= 3.5`

## Directory Structure

```
tcp_server/
├── README.md
├── server/
│   ├── tcp_relay_server.py
│   ├── server_config.json
│   └── requirements.txt
└── client/
    ├── rs485_tcp_client.py
    ├── client_config.json
    └── requirements.txt
```

---

## Server Setup

### 1. Configuration

Edit `server/server_config.json`:

```json
{
  "channels": [
    {
      "name": "site_A",
      "port_x": {"host": "0.0.0.0", "port": 9001},
      "port_y": {"host": "0.0.0.0", "port": 9002}
    },
    {
      "name": "site_B",
      "port_x": {"host": "0.0.0.0", "port": 9003},
      "port_y": {"host": "0.0.0.0", "port": 9004}
    }
  ],
  "buffer_size": 4096,
  "log_level": "INFO"
}
```

- **channels** — list of relay channels. Each channel has a unique `name` and two port endpoints (`port_x`, `port_y`). Add as many channels as needed.
- **host** — bind address. Use `0.0.0.0` to accept connections from any interface.
- **buffer_size** — TCP read buffer size in bytes.
- **log_level** — `DEBUG`, `INFO`, `WARNING`, `ERROR`, or `CRITICAL`.

### 2. Run

```
python server/tcp_relay_server.py
```

To use a custom config path:

```
python server/tcp_relay_server.py --config path/to/my_config.json
```

### 3. Behavior

- Each port accepts **one** TCP connection at a time. If a new connection arrives while one is active, the old connection is evicted.
- When both sides of a channel are connected, data flows bidirectionally.
- When only one side is connected, incoming data is discarded with a warning log.
- TCP keepalive is enabled on all connections.
- Press **Ctrl+C** to shut down gracefully.

---

## Client Setup

### 1. Install Dependencies

```
pip install -r client/requirements.txt
```

### 2. Configuration

Edit `client/client_config.json`:

```json
{
  "local_mode": "serial",

  "serial": {
    "port": "COM3",
    "baudrate": 9600,
    "bytesize": 8,
    "parity": "N",
    "stopbits": 1,
    "timeout": 0.1
  },

  "local_tcp": {
    "host": "127.0.0.1",
    "port": 22
  },

  "server": {
    "host": "192.168.1.100",
    "port": 9001
  },
  "auto_connect": true,
  "reconnect_interval": 5,
  "buffer_size": 4096,
  "log_level": "INFO"
}
```

#### Key settings

- **local_mode** — `"serial"` for RS485 COM port, `"tcp"` for a local TCP port.
- **serial** — RS485 serial port settings (used when `local_mode` is `"serial"`).
  - `port` — COM port name (e.g. `COM3`, `COM4`).
  - `baudrate` — baud rate (e.g. `9600`, `19200`, `115200`).
  - `parity` — `N` (None), `E` (Even), `O` (Odd), `M` (Mark), `S` (Space).
  - `stopbits` — `1`, `1.5`, or `2`.
- **local_tcp** — local TCP endpoint settings (used when `local_mode` is `"tcp"`).
  - `host` / `port` — the local service to connect to (e.g. `127.0.0.1:22` for SSH).
- **server** — remote relay server address and port.
- **auto_connect** — `true` to connect automatically on startup, `false` to start disconnected.
- **reconnect_interval** — seconds to wait before retrying after a connection loss.

### 3. Run

```
python client/rs485_tcp_client.py
```

To use a custom config path:

```
python client/rs485_tcp_client.py --config path/to/my_config.json
```

### 4. Interactive Commands

Once running, the client presents an interactive prompt:

```
RS485-to-TCP Bridge Client
Commands: connect | disconnect | status | quit

bridge>
```

| Command      | Description                                                        |
|------------- |--------------------------------------------------------------------|
| `connect`    | Open the local endpoint and TCP connection, start forwarding data. |
| `disconnect` | Stop forwarding, close TCP and release the local endpoint.         |
| `status`     | Display current bridge, local endpoint, and TCP connection state.  |
| `quit`       | Disconnect (if connected) and exit the program.                    |

---

## Example: RS485 Energy Meter Access

Two client devices at different sites, each with RS485 energy meters, connecting through a central relay server.

**Server** (on a cloud/VPS machine):
```json
{
  "channels": [
    {
      "name": "site_A",
      "port_x": {"host": "0.0.0.0", "port": 9001},
      "port_y": {"host": "0.0.0.0", "port": 9002}
    }
  ]
}
```

**Client 1** (at the energy meter site — connects to port_x):
```json
{
  "local_mode": "serial",
  "serial": {"port": "COM3", "baudrate": 9600},
  "server": {"host": "server-ip", "port": 9001}
}
```

**Client 2** (at the monitoring station — connects to port_y):
```json
{
  "local_mode": "serial",
  "serial": {"port": "COM5", "baudrate": 9600},
  "server": {"host": "server-ip", "port": 9002}
}
```

Modbus requests from Client 2's RS485 port travel through the relay server and arrive at Client 1's RS485 port (and the energy meters), with responses following the reverse path.

## Example: SSH Tunnel via Local TCP Mode

Forward a local SSH service through the relay server to a remote client.

**Client at SSH server site:**
```json
{
  "local_mode": "tcp",
  "local_tcp": {"host": "127.0.0.1", "port": 22},
  "server": {"host": "server-ip", "port": 9001}
}
```

**Client at remote access site:**
```json
{
  "local_mode": "tcp",
  "local_tcp": {"host": "127.0.0.1", "port": 2222},
  "server": {"host": "server-ip", "port": 9002}
}
```

In this setup the remote client would run a local TCP listener on port 2222 that the bridge forwards through the server to the SSH service. (Note: for the remote-access side to *listen* rather than *connect*, you would point an SSH client at the relay server port directly, or use a local port-forwarding tool.)

---

## Firewall Notes

Ensure the following ports are open:

- **Server machine:** all channel ports (e.g. 9001, 9002, 9003, 9004).
- **Client machines:** outbound TCP to the server's IP and channel port.
