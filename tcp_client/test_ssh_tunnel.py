import io
import os
from pathlib import Path
import shutil
import subprocess
import threading
import unittest
from unittest import mock

from rs485_tcp_client import RS485TcpBridge


class SshTunnelTests(unittest.TestCase):
    def setUp(self):
        self.bridge = RS485TcpBridge({
            "local_mode": "web",
            "server": {"host": "example.invalid", "port": 8006},
            "channel": {"port_y_port": 8007},
        })
        self.bridge.logger = mock.Mock()
        self.proc = mock.Mock(pid=123, returncode=None)
        self.proc.poll.side_effect = lambda: self.proc.returncode
        self.proc.terminate.side_effect = lambda: setattr(
            self.proc, "returncode", -15)

    def test_diagnostics_require_forward_success_not_authentication(self):
        for message, expected in [
                (b"Authenticated to example.invalid.\n", False),
                (b"debug1: remote forward failure for: listen 8007\n", False),
                (b"debug1: remote forward success for: listen 8007, "
                 b"connect 127.0.0.1:8080\n", True)]:
            with self.subTest(message=message):
                ready = threading.Event()
                self.proc.stderr = io.BytesIO(message)
                lines = []
                self.bridge._drain_ssh_stderr(self.proc, ready, lines)
                self.assertEqual(ready.is_set(), expected)
                self.assertTrue(self.proc.stderr.closed)
                self.assertEqual(len(lines), 1)

    def test_waits_for_confirmation_before_reporting_connected(self):
        waits = []

        def wait(seconds):
            ready = thread.call_args.kwargs["args"][1]
            waits.append(seconds)
            if len(waits) == 1:
                self.assertFalse(self.bridge._connected)
                self.assertIn("CONNECTING via SSH tunnel",
                              self.bridge.status())
                ready.set()
            else:
                self.assertTrue(self.bridge._connected)
                self.assertIn("CONNECTED via SSH tunnel",
                              self.bridge.status())
                self.bridge._stop_event.set()

        with mock.patch("rs485_tcp_client.subprocess.Popen",
                        return_value=self.proc) as popen:
            with mock.patch("rs485_tcp_client.threading.Thread") as thread:
                with mock.patch.object(self.bridge._stop_event, "wait",
                                       side_effect=wait):
                    self.bridge._run_ssh_tunnel()
        self.assertEqual(waits, [0.5, 0.5])
        self.assertFalse(self.bridge._connected)
        self.assertIsNone(self.bridge._socat_proc)
        self.proc.terminate.assert_called_once()
        command = popen.call_args.args[0]
        self.assertIn("-v", command)
        self.assertIn("ExitOnForwardFailure=yes", command)
        self.assertIn("BatchMode=yes", command)

    def test_early_ssh_failure_never_reports_connected(self):
        self.proc.returncode = 255
        self.proc.stderr = io.BytesIO(b"Permission denied (publickey).\n")

        def start_reader(**kwargs):
            kwargs["target"](*kwargs["args"])
            return mock.Mock()

        def wait(seconds):
            self.assertFalse(self.bridge._connected)
            self.bridge._stop_event.set()

        with mock.patch("rs485_tcp_client.subprocess.Popen",
                        return_value=self.proc):
            with mock.patch("rs485_tcp_client.threading.Thread",
                            side_effect=start_reader):
                with mock.patch.object(self.bridge._stop_event, "wait",
                                       side_effect=wait):
                    self.bridge._run_ssh_tunnel()
        self.bridge.logger.error.assert_any_call(
            "SSH tunnel exited unexpectedly (code=%d)", 255)
        self.bridge.logger.error.assert_any_call(
            "[ssh] %s", "Permission denied (publickey).")
        for call in self.bridge.logger.info.call_args_list:
            self.assertFalse(call.args[0].startswith("SSH tunnel connected"))
        self.assertFalse(self.bridge._connected)

    def test_service_allows_access_to_home_ssh_keys(self):
        installer = Path(__file__).with_name("install.sh").read_text(
            encoding="utf-8")
        self.assertIn("printf 'read-only'", installer)
        self.assertIn('/home/*)', installer)
        self.assertNotIn("grep -q '^/home'", installer)

    def test_generated_service_preserves_path_and_allows_ssh_key_reads(self):
        bash = (r'C:\Program Files\Git\bin\bash.exe'
                if os.name == 'nt' else shutil.which('bash'))
        if not bash or not Path(bash).is_file():
            self.skipTest('A usable Bash interpreter is required')
        installer = Path(__file__).with_name('install.sh').read_text(encoding='utf-8')
        template = installer.split('cat > "${SERVICE_FILE}" <<EOF\n', 1)[1].split('\nEOF', 1)[0]
        self.assertIn('socat openssh-client', installer)
        for directory, protection in (
            ('/opt/tcp_client', 'read-only'),
            ('/home/admin/projects/tcp_client', 'false'),
        ):
            with self.subTest(directory=directory):
                env = os.environ.copy()
                env.update(INSTALL_DIR=directory, APP_NAME='connection-manager',
                           LOG_DIR='/var/log/tcp_client', PID_DIR='/var/run',
                           NET_INTERFACE='eth0')
                result = subprocess.run(
                    [bash, '--noprofile', '--norc', '-c', 'cat <<EOF\n' + template + '\nEOF\n'],
                    env=env, capture_output=True, text=True, timeout=10,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn('WorkingDirectory=' + directory, result.stdout)
                self.assertIn('--interface eth0', result.stdout)
                self.assertIn('ProtectHome=' + protection + '\n', result.stdout)
                self.assertIn('ProtectSystem=strict', result.stdout)
                self.assertIn('NoNewPrivileges=true', result.stdout)

    def test_disconnect_while_waiting_never_reports_connected(self):
        def wait(seconds):
            self.assertFalse(self.bridge._connected)
            self.bridge._disconnect_event.set()

        with mock.patch("rs485_tcp_client.subprocess.Popen",
                        return_value=self.proc):
            with mock.patch("rs485_tcp_client.threading.Thread"):
                with mock.patch.object(self.bridge._stop_event, "wait",
                                       side_effect=wait):
                    self.bridge._run_ssh_tunnel()
        self.proc.terminate.assert_called_once()
        self.assertFalse(self.bridge._connected)
        self.assertIsNone(self.bridge._socat_proc)

    def test_confirmation_from_exited_process_is_not_connected(self):
        self.proc.returncode = 255

        def start_reader(**kwargs):
            # The reader may deliver confirmation after SSH has already exited.
            kwargs["args"][1].set()
            return mock.Mock()

        with mock.patch("rs485_tcp_client.subprocess.Popen",
                        return_value=self.proc):
            with mock.patch("rs485_tcp_client.threading.Thread",
                            side_effect=start_reader):
                with mock.patch.object(
                        self.bridge._stop_event, "wait",
                        side_effect=lambda seconds: self.bridge._stop_event.set()):
                    self.bridge._run_ssh_tunnel()
        for call in self.bridge.logger.info.call_args_list:
            self.assertFalse(call.args[0].startswith("SSH tunnel connected"))
        self.assertFalse(self.bridge._connected)


if __name__ == "__main__":
    unittest.main()
