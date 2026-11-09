# AMR Gateway Web UI

Web-based configuration interface for the CP2B AMR Gateway running Ubuntu 24.04.4 LTS.

## Features

- **LTE Status** — Conditional modem status tab with SIM/IP, carrier, signal, Refresh, and modem reboot
- **RTU Setting** — Default tab with MAC Address, Network Setting, AMR Server Setting, and Internet Watchdog cards
- **AMR Setting** — Auto read scheduling (crontab), meter batch file management, config file editor
- **Connection Manager** — Admin-only runtime and boot-start control of the installed `connection-manager.service`
- **Authentication** — Admin, operational `dec`, and read-only `amr` roles, with backend enforcement
- **Reboot** — Confirmed, CSRF-protected reboot, logout, and return to login after the gateway returns

This eMMC deployment does not include machine binding or clone-protection controls.

## System Requirements

- CP2B gateway with Ubuntu 24.04.4 LTS and sudo access
- Python 3.11.2+
- NetworkManager (nmcli)
- systemd, including `systemd-run`

The web UI service intentionally runs as root for NetworkManager, scheduling,
service control, modem access, and reboot. Run installation/update commands
through sudo; no passwordless-sudo rule is added. Application data stays under
`/home/admin/projects`, with network profiles `ETH0`, `ETH1`, and `LTE`.

## Deployment on CP2B

### Step 1 — Clone the repository

```bash
ssh admin@<device-ip>
git clone <repo-url> /tmp/cp2b_projects
cd /tmp/cp2b_projects/webui
```

### Step 2 — Run the installer

```bash
sudo bash install.sh
```

The installer will:
- Install Python 3, pip, venv, and rsync via apt; require Python 3.11+
- Copy the project to `/opt/amr_gateway_webui`
- Create a Python virtual environment and install dependencies
- Install and enable the `amr_gateway_webui` systemd service
- Create required directories:
  - `/home/admin/projects/config_file/` — server IP and location config
  - `/home/admin/projects/amr/meter/` — meter config files (*.cfg)
  - `/home/admin/projects/amr/upload/` — meter batch CSV
  - `/home/admin/projects/amr/scripts/` — process_meters.sh script
  - `/home/admin/projects/tcp_client/` — connection manager script
- Preserve installed `users.json`, `.env`/`.env.*`, virtual environments, and target-only files
- Restart an already running web UI after installing the current service unit;
  fresh or stopped installations require the explicit start below

Deploy the complete `webui` tree, including `scripts/deployment_common.sh`.
AMR, modem/watchdog, and TCP client services must be installed separately.
The installer does not install the TCP service or change its boot state.
It no longer installs/enables the legacy `eth0-apply.service`; existing enabled
copies are left unchanged with a warning. Review networking before changing one.

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
| `dec`   | `1234`    | Operational settings and reboot; no Connection Manager access |
| `amr`   | `amr123`   | Read-only (view settings only)|

All users can change their own password via the **Change Password** button in the navbar.
Startup adds `dec` when absent, or migrates its role while preserving its existing
password. Other accounts and the session secret are retained.
All signed-in users can Refresh LTE and run LTE diagnostics when hardware is detected.

> **Important:** Change the default passwords after first login.

### Admin password reset

Sign in as **admin**, select **Reset User Password** in the navbar, choose `dec`
or `amr`, and confirm **Reset to 1234**. Both accounts reset to `1234`, including
`amr` (whose fresh-install password remains `amr123`). Ask the user to change it
after signing in. Reset affects only the selected password hash; roles, other
accounts, and the session secret are preserved. Existing signed-in sessions are
not logged out. Non-admin users cannot view the controls or use the reset API.
The POST request requires a session token in `X-CSRF-Token`.

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
cd /tmp/cp2b_projects/webui
git pull
sudo bash update.sh
```

Use `sudo bash install.sh` instead when dependencies change or for a fresh install.
Both scripts preserve installed credentials and local environment files; the
updater requires an existing Gunicorn virtual environment and rsync.
Both install the current service unit and reload systemd before restarting.
The unit runs two `gthread` workers with two threads each; merely copying Python
files does not update an older installed unit. Verify with
`systemctl show amr_gateway_webui -p ExecStart --no-pager` and
`sudo journalctl -u amr_gateway_webui -n 20 --no-pager`.

## Tab Details

The navbar shows **Logged in as: &lt;username&gt;** without repeating the role.
**Device time** displays the gateway's local date/time and timezone, ticking
every second. It resynchronizes with the device every 30 seconds, pauses when
the page is hidden, and reports Unavailable if no fresh sample exists for 90
seconds. It does not use the browser's timezone or set the device clock.

### LTE Status
- Shown only when the configured modem device is detected, using the same
  presence rule as SMS Messages. This is hardware presence, not internet
  connectivity: detected but disconnected/unresponsive modems remain visible.
  Checks run on startup, tab navigation, and return to the page. If the modem
  disappears or presence cannot be checked, both modem tabs hide and an active
  LTE/SMS view returns to RTU. Hidden LTE Status does not send status AT polling.
- **SIM/IP** — Retains CP2B's SIM status and assigned modem IP display.
- **Status** — Retains `AT+QPING` internet testing against the first watchdog target.
- **Availability** — Missing `/dev/ttyUSB2` returns Not installed without AT queries.
  A failed first status query stops the sequence and reports Unavailable.
- **Refresh** — Available to every signed-in user; shares any in-flight status
  request and bypasses the scheduled delay. Polling waits five seconds after a
  response, or one minute when unavailable, and stops when leaving the LTE tab.
- **LTE Reboot** — Admin/dec only; disabled while unavailable.
- **Diagnostic LTE Check** — Gated by device-node presence, rechecked before use;
  a detected but disconnected modem can still be checked.

### SMS Messages
- Shown next to LTE Status only when the configured LTE serial device is present.
- All signed-in users can read received SMS. Opening the tab or clicking
  **Refresh** loads sender, modem timestamp, unread/read status, and message text.
  Loading marks unread SMS as read on the modem; the displayed status is from
  before that read. There is no background SMS polling, sending, or browser persistence.
- **Delete All** — Admin/dec only, with explicit confirmation and a session CSRF
  token. Deletes every message type (including unread, sent and unsent messages
  not displayed in the received-message list) from the modem's currently selected
  read/delete storage. Other storage areas are not selected or cleared.
  Uses `AT+CMGD=1,4` in one exclusive serial session and verifies storage usage
  afterward. Uncertain or incomplete results require manual Refresh; no automatic
  deletion retry occurs. Reading and deleting share a UI busy guard.
- **Columns** — Sender is sized for about 15 monospace characters; longer values
  wrap without losing data. DateTime is the stored SMS timestamp, not device time.
- Reads the modem's currently selected SMS storage (`AT+CPMS?`) and reports
  used/total slots. It does not switch storage or change message routing.
  Long messages appear as numbered parts; only stored received messages are shown.
  GSM-7 and UCS2 text are decoded; binary/unsupported encodings show hexadecimal data.
- Requires EC200A firmware supporting SMS PDU mode and `AT+CMGL=4`.
  The temporary SMS display format is restored before releasing the exclusive serial port.
  SIM/PIN, unsupported firmware, busy-port and timeout errors are reported.
  If another modem operation is active, wait for it to finish and retry.
- `GET /api/lte/sms` requires login and returns uncached message data.
- `POST /api/lte/sms/delete-all` requires admin/dec, `X-CSRF-Token`, and
  JSON `{"confirm": true}`. All SMS responses are uncached.
- Hardware-mocked tests: `python -m unittest discover -s tests -p "test_sms.py" -v`.
  Frontend checks: `node tests/test_sms_ui.js`.

### RTU Setting
RTU is the default tab. Its cards, in order, are:
- **MAC Address** — Live interface MAC. CP2B has no binding/reset controls.
- **Network Setting** — IP method, address, subnet, gateway, and DNS; Save Network
  remains in this card's header.
- **AMR Server Setting** — Remote Server IP and Location, with their separate save
  buttons in the card header.
- **Internet Watchdog** — Target list, Add Target, and Save. Available independently
  of LTE hardware; its script is `/home/admin/projects/modem/internet-watchdog.sh`.

The Watchdog card is rendered by `templates/watchdog.html`; deploy the complete
template tree when updating.

- **IP Method** — Toggle between DHCP and Static IP for the `eth0` interface.
- **IP Address / Subnet / Gateway** — Only editable when Static is selected. Applied to `ETH0`; cleanup preserves `ETH1` and `LTE`.
- **Remote Server IP** — Saved to `/home/admin/projects/config_file/server_ip.txt`.
- **Location** — Saved to `/home/admin/projects/config_file/location.cfg` as `location=<value>` (for example, `location=Building A`).

### AMR Setting
- **Upload / Download** — Meter Batch File has Upload (admin/dec) and Download
  (all signed-in users) controls. Upload accepts any filename/extension and
  always saves to `/home/admin/projects/amr/upload/meter_batch.csv`.
  Use UTF-8 CSV (optional BOM), up to 1 MiB, without a header. Every nonblank
  record must be `serial_number,config_file.cfg,modbus_id`: exactly three fields,
  digit-only serial/Modbus ID, and a `.cfg` basename with no directory path or
  control characters. Leading zeros are retained; no Modbus ID range is imposed.
  Config filenames need not be installed; unknown names remain selected in the
  table and are marked "(not installed)".
  The browser validates before sending, and the server validates every entry
  again before replacement. Invalid uploads leave the saved file unchanged.
  Replacement requires confirmation and a session CSRF token; unsaved table
  edits are replaced. For more than 32 valid rows, all are validated and only
  the first 32 are saved, with a warning.
  Download copies the saved file, not unsaved table edits. A Save As picker lets
  you choose the host PC destination where supported (typically HTTPS). Otherwise
  the browser uses its normal download behavior; enable "Ask where to save each
  file" in browser settings to choose a location for every download.
- **Meter limit** — Loading `meter_batch.csv` keeps only the first 32 valid
  entries. Oversized files are automatically and permanently trimmed to those
  entries, with a UI warning. Files within the limit are not rewritten. Blank
  or incomplete rows are not counted; existing two-column entries are supported.
  CSV writes use atomic replacement and a `meter_batch.csv.lock` sidecar to
  serialize web UI workers. The existing save/Add limit remains 32.
- **Sync Server Time** — Checkbox saved immediately. Checked creates an empty
  `/home/admin/projects/config_file/sync_server`; unchecked removes it. Existing
  marker state is loaded when opening AMR Setting. Admin/dec can change it;
  `amr` can only view it. This controls the flag used by the meter script; the
  web UI does not directly change the clock or run time synchronization.
- **Auto Read** — Enables/disables a crontab entry that runs `/home/admin/projects/amr/scripts/process_meters.sh` at the specified interval (in minutes).
- **Meter Batch File** — CRUD table for meter entries (S/N, Config File, Alternate Address). Saved as CSV to `/home/admin/projects/amr/upload/meter_batch.csv`. Config File dropdown lists `*.cfg` files from `/home/admin/projects/amr/meter/` (excluding `defaults.cfg`).
- **Config File Editor** — Edit, save, save-as, or delete `*.cfg` files in `/home/admin/projects/amr/meter/`.

### Connection Manager
- Visible and accessible only to admin; operates the existing systemd service,
  retaining its installation path, arguments, and `client_config.json`.
- Enable starts now and enables boot startup; Disable stops now and disables boot
  startup. Refresh reads actual runtime and boot state without background polling.
- Toggle requires a session CSRF token and a JSON boolean. Disable requires
  confirmation because TCP bridging, check-ins, and tunnel access will stop.
- Removed unmanaged start/stop/restart API routes. Manually launched copies and
  alternative schedulers are outside this control.

### Device Reboot
- Admin/dec confirmation sends a session CSRF token and schedules reboot after
  five seconds using fixed `systemd-run` arguments.
- Success clears this browser's session and remember-me cookie and opens a
  standalone Rebooting page, ending old polling. Other browsers are not logged out.
- Readiness checks wait for offline then online before returning to `/login`.
  Checks omit credentials, time out after three seconds, and stop after five
  minutes with a manual link. If the IP changes, open the new address manually.
- Scheduling failures leave the session intact and report the error.

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
| GET    | `/api/lte/status`               | any    | Status, availability, SIM/IP    |
| GET    | `/api/lte/presence`             | any    | Lightweight modem presence      |
| POST   | `/api/lte/reboot`               | admin/dec | Reboot detected modem        |
| GET    | `/api/lte/watchdog`             | any    | Read watchdog targets           |
| POST   | `/api/lte/watchdog`             | admin/dec | Save watchdog targets        |
| GET    | `/api/rtu/config`               | any    | Get network config              |
| POST   | `/api/rtu/network`              | admin/dec | Set DHCP/static IP           |
| POST   | `/api/rtu/server`               | admin/dec | Set remote server IP         |
| POST   | `/api/rtu/location`             | admin/dec | Set location                 |
| GET    | `/api/amr/autoread`             | any    | Get auto read status            |
| POST   | `/api/amr/autoread`             | admin/dec | Enable/disable auto read     |
| GET    | `/api/amr/sync-time`            | any    | Read sync_server marker state   |
| POST   | `/api/amr/sync-time`            | admin/dec + CSRF | Create/remove empty marker |
| GET    | `/api/amr/meters`               | any    | Get meter batch list            |
| POST   | `/api/amr/meters`               | admin/dec | Save meter batch list        |
| POST   | `/api/amr/meters/upload`        | admin/dec + CSRF | Validate uploaded file and replace fixed CSV |
| GET    | `/api/amr/meters/download`      | any    | Download saved meter_batch.csv  |
| GET    | `/api/amr/meter-configs`        | any    | List cfg files (excl defaults)  |
| GET    | `/api/amr/configs`              | any    | List all cfg files              |
| GET    | `/api/amr/configs/<file>`       | any    | Read cfg file content           |
| POST   | `/api/amr/configs/<file>`       | admin/dec | Save cfg file                |
| POST   | `/api/amr/configs`              | admin/dec | Save As new cfg file         |
| DELETE | `/api/amr/configs/<file>`       | admin/dec | Delete cfg file              |
| GET    | `/api/connection/status`        | admin  | Get connection manager status   |
| POST   | `/api/connection/toggle`        | admin + CSRF | Enable/start or disable/stop |
| POST   | `/api/diagnostic/reboot`        | admin/dec + CSRF | Schedule reboot and log out |
| POST   | `/api/diagnostic/lte-check`     | any    | Query detected modem            |
| GET    | `/rebooting`                    | public | Standalone reboot monitor       |
| GET    | `/api/diagnostic/ready`         | public | Uncached readiness response     |
| GET    | `/api/diagnostic/time`          | any    | Uncached device time and timezone |
| POST   | `/api/auth/change-password`     | any    | Change own password             |
| POST   | `/api/auth/reset-password`      | admin + CSRF | Reset dec/amr to 1234       |

Reboot, Connection Manager toggle, admin password reset, Sync Server Time, CSV upload, and SMS deletion use session CSRF tokens;
this does not imply CSRF coverage for every legacy endpoint.

## Validation

From the `webui` source directory with development dependencies installed:

```bash
python -m unittest discover -s tests -v
node tests/test_connection_ui.js
node tests/test_reboot_ui.js
node tests/test_login_ui.js
node tests/test_diagnostic_ui.js
node tests/test_password_reset_ui.js
node tests/test_sync_server_time_ui.js
node tests/test_meter_limit_ui.js
node tests/test_meter_transfer_ui.js
node tests/test_machine_clock_ui.js
```

Tests use temporary state and mocked hardware, package, and service operations.
They do not reboot a gateway, install device packages, or control live systemd.
After deployment, verify all roles, credentials/configuration preservation,
LTE presence/absence, service boot/runtime state, and reboot return-to-login.
Test Connection Manager shutdown only in a maintenance window with local access.

## License

Internal use only.
