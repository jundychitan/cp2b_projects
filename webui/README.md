# AMR Gateway Web UI

Web-based configuration interface for an AMR Gateway running on Orange Pi Zero H3 (Armbian).

## Features

- **LTE Setting** — Configure APN, enable/disable LTE modem with automatic routing via ppp0/end0
- **RTU Setting** — DHCP/Static IP configuration for end0, remote server IP, location
- **AMR Setting** — Auto read scheduling (crontab), meter batch file management, config file editor
- **Connection Manager** — Start/stop/restart the TCP client (`/projects/tcp_client/connection_manager.py`)
- **Authentication** — Role-based access control with two users (admin and amr)

## System Requirements

- Orange Pi Zero H3 with Armbian OS
- Python 3.11.2+
- NetworkManager (nmcli)
- systemd

## Deployment on Orange Pi

### Step 1 — Clone the repository

```bash
ssh root@<device-ip>
git clone <repo-url> /tmp/amr_gateway_webui
cd /tmp/amr_gateway_webui
```

### Step 2 — Run the installer

```bash
sudo bash install.sh
```

The installer will:
- Install Python 3, pip, and venv via apt
- Copy the project to `/opt/amr_gateway_webui`
- Create a Python virtual environment and install dependencies
- Install and enable the `amr_gateway_webui` systemd service
- Create required directories:
  - `/projects/config_file/` — server IP and location config
  - `/projects/amr/meter/` — meter config files (*.cfg)
  - `/projects/amr/upload/` — meter batch CSV
  - `/projects/amr/scripts/` — process_meters.sh script
  - `/projects/tcp_client/` — connection manager script

### Step 3 — Start the service

```bash
sudo systemctl start amr_gateway_webui
```

### Step 4 — Access the UI

Open a browser and navigate to:

```
http://<device-ip>:8080
```

## Default Credentials

| User    | Password   | Access                        |
|---------|------------|-------------------------------|
| `admin` | `admin123` | Full read/write access        |
| `amr`   | `amr123`   | Read-only (view settings only)|

Both users can change their own password via the **Change Password** button in the navbar.

> **Important:** Change the default passwords after first login.

## Service Management

```bash
# Check status
sudo systemctl status amr_gateway_webui

# Start / Stop / Restart
sudo systemctl start amr_gateway_webui
sudo systemctl stop amr_gateway_webui
sudo systemctl restart amr_gateway_webui

# View logs
sudo journalctl -u amr_gateway_webui -f

# Disable autostart
sudo systemctl disable amr_gateway_webui
```

## Updating

To update to a new version:

```bash
cd /tmp/amr_gateway_webui
git pull
sudo bash install.sh
sudo systemctl restart amr_gateway_webui
```

Note: `users.json` (passwords) is preserved across updates since it lives in `/opt/amr_gateway_webui/` and is listed in `.gitignore`.

## Tab Details

### LTE Setting
- **APN** — Access Point Name for the LTE modem. Applied via `nmcli con modify my-ltemodem gsm.apn <value>`.
- **Enable LTE** — When enabled: brings up the LTE connection, enables `ppp0-monitor` service, routes internet through `ppp0`. When disabled: disconnects modem, disables `ppp0-monitor`, routes internet through `end0`. State persists across reboots.

### RTU Setting
- **IP Method** — Toggle between DHCP and Static IP for the `end0` interface.
- **IP Address / Subnet / Gateway** — Only editable when Static is selected. Applied via `nmcli con modify end0`.
- **Remote Server IP** — Saved to `/projects/config_file/server_ip.txt`.
- **Location** — Saved to `/projects/config_file/location.cfg`.

### AMR Setting
- **Auto Read** — Enables/disables a crontab entry that runs `/projects/amr/scripts/process_meters.sh` at the specified interval (in minutes).
- **Meter Batch File** — CRUD table for meter entries (S/N, Config File, Alternate Address). Saved as CSV to `/projects/amr/upload/meter_batch.csv`. Config File dropdown lists `*.cfg` files from `/projects/amr/meter/` (excluding `defaults.cfg`).
- **Config File Editor** — Edit, save, save-as, or delete `*.cfg` files in `/projects/amr/meter/`.

### Connection Manager
- Manages the TCP client script at `/projects/tcp_client/connection_manager.py`.
- Provides Start, Stop, Restart, and Refresh status controls.

## Development

```bash
# Create a virtual environment (on your development machine)
python3 -m venv venv
source venv/bin/activate       # Linux/macOS
# venv\Scripts\activate        # Windows
pip install -r requirements.txt

# Run in development mode
python app.py
```

The dev server runs on `http://localhost:8080` with debug mode enabled.

Note: LTE/RTU/Connection Manager features require `nmcli`/`systemctl` and will not work on a development machine. The AMR config file editor and meter batch features work anywhere with appropriate file paths.

## Project Structure

```
amr_gateway_webui/
├── app.py                     # Flask app factory + blueprint registration
├── auth.py                    # User model, Flask-Login, role decorators
├── config.py                  # Path constants and defaults
├── requirements.txt           # flask, flask-login, gunicorn
├── install.sh                 # Target device installer (bash)
├── amr_gateway_webui.service  # systemd service unit
├── users.json                 # Generated on first run (hashed passwords)
├── blueprints/
│   ├── __init__.py
│   ├── lte.py                 # LTE Setting API routes
│   ├── rtu.py                 # RTU Setting API routes
│   ├── amr.py                 # AMR Setting API routes
│   └── connection.py          # Connection Manager API routes
├── static/
│   ├── css/
│   │   └── bootstrap.min.css  # Bootstrap 5.3.3 (bundled)
│   └── js/
│       ├── bootstrap.bundle.min.js
│       └── app.js             # Frontend logic (AJAX, tabs, role-based UI)
└── templates/
    ├── base.html              # Main layout with Bootstrap tabs + navbar
    ├── login.html             # Login page
    ├── lte.html               # LTE Setting tab content
    ├── rtu.html               # RTU Setting tab content
    ├── amr.html               # AMR Setting tab content
    └── connection.html        # Connection Manager tab content
```

## API Endpoints

| Method | Endpoint                        | Auth   | Description                     |
|--------|---------------------------------|--------|---------------------------------|
| GET    | `/api/lte/config`               | any    | Get APN and LTE status          |
| POST   | `/api/lte/apn`                  | admin  | Set APN                         |
| POST   | `/api/lte/toggle`               | admin  | Enable/disable LTE              |
| GET    | `/api/rtu/config`               | any    | Get network config              |
| POST   | `/api/rtu/network`              | admin  | Set DHCP/static IP              |
| POST   | `/api/rtu/server`               | admin  | Set remote server IP            |
| POST   | `/api/rtu/location`             | admin  | Set location                    |
| GET    | `/api/amr/autoread`             | any    | Get auto read status            |
| POST   | `/api/amr/autoread`             | admin  | Enable/disable auto read        |
| GET    | `/api/amr/meters`               | any    | Get meter batch list            |
| POST   | `/api/amr/meters`               | admin  | Save meter batch list           |
| GET    | `/api/amr/meter-configs`        | any    | List cfg files (excl defaults)  |
| GET    | `/api/amr/configs`              | any    | List all cfg files              |
| GET    | `/api/amr/configs/<file>`       | any    | Read cfg file content           |
| POST   | `/api/amr/configs/<file>`       | admin  | Save cfg file                   |
| POST   | `/api/amr/configs`              | admin  | Save As new cfg file            |
| DELETE | `/api/amr/configs/<file>`       | admin  | Delete cfg file                 |
| GET    | `/api/connection/status`        | any    | Get connection manager status   |
| POST   | `/api/connection/start`         | admin  | Start connection manager        |
| POST   | `/api/connection/stop`          | admin  | Stop connection manager         |
| POST   | `/api/connection/restart`       | admin  | Restart connection manager      |
| POST   | `/api/auth/change-password`     | any    | Change own password             |

## License

Internal use only.
