"""Path constants and application configuration."""

import os

# Base paths on the target Orange Pi device
WORKING_DIR = '/home/admin/projects'
CONFIG_FILE_DIR = os.path.join(WORKING_DIR, 'config_file')
CP_CLIENT_DIR = os.path.join(WORKING_DIR, 'tcp_client')
AMR_BASE_DIR = os.path.join(WORKING_DIR,'amr')
AMR_METER_DIR = os.path.join(AMR_BASE_DIR, 'meter')
AMR_UPLOAD_DIR = os.path.join(AMR_BASE_DIR, 'upload')
AMR_SCRIPTS_DIR = os.path.join(AMR_BASE_DIR, 'scripts')

# File paths
SERVER_IP_FILE = os.path.join(CONFIG_FILE_DIR, 'server_ip.txt')
LOCATION_FILE = os.path.join(CONFIG_FILE_DIR, 'location.cfg')
METER_BATCH_FILE = os.path.join(AMR_UPLOAD_DIR, 'meter_batch.csv')
PROCESS_METERS_SCRIPT = os.path.join(AMR_SCRIPTS_DIR, 'process_meters.sh')

# NetworkManager connection IDs
ETH_CONNECTION_ID = 'ETH0'
ETH1_CONNECTION_ID = 'ETH1'
LTE_CONNECTION_ID = 'LTE'
ETH_DEVICE_NAME = 'eth0'  # Physical ethernet interface name

# LTE modem serial port (EC200A AT commands)
LTE_SERIAL_PORT = '/dev/ttyUSB2'
LTE_SERIAL_BAUD = 115200

# Internet watchdog
WATCHDOG_SCRIPT = '/projects/modem/internet-watchdog.sh'
WATCHDOG_TARGETS_FILE = os.path.join(CONFIG_FILE_DIR, 'watchdog_targets.cfg')

# Users file (relative to app working directory)
USERS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'users.json')
