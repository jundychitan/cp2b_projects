"""Execute deployment scripts in a sandbox; package/service commands are mocked."""

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def usable_bash():
    candidates = ([Path(r'C:\Program Files\Git\bin\bash.exe')] if os.name == 'nt' else [])
    discovered = shutil.which('bash')
    if discovered:
        candidates.append(Path(discovered))
    for candidate in candidates:
        if candidate.is_file():
            try:
                result = subprocess.run([str(candidate), '--noprofile', '--norc', '-c', 'exit 0'],
                                        capture_output=True, timeout=10)
                if result.returncode == 0:
                    return str(candidate)
            except (OSError, subprocess.TimeoutExpired):
                pass
    return None


BASH = usable_bash()

MOCK_COMMANDS = r'''
export PATH="/usr/bin:/bin:$PATH"
record() {
    printf '%s' "$1" >> "$TEST_LOG"
    shift
    local arg
    for arg in "$@"; do printf '\t%s' "$arg" >> "$TEST_LOG"; done
    printf '\n' >> "$TEST_LOG"
}
uname() { printf '%s\n' "${TEST_OS:-Linux}"; }
id() { printf '%s\n' "${TEST_UID:-0}"; }
apt-get() { record apt-get "$@"; [[ "${TEST_FAIL:-}" != apt-get ]]; }
systemctl() {
    record systemctl "$@"
    case "$1" in
        is-active)
            if [[ "$2" == --quiet ]]; then [[ "${TEST_RUNNING:-0}" == 1 ]]
            else printf 'active\n'
            fi ;;
        is-enabled) [[ "${TEST_LEGACY:-0}" == 1 ]] ;;
        *) [[ "${TEST_FAIL:-}" != systemctl ]] ;;
    esac
}
python3() {
    record python3 "$@"
    if [[ "$1" == -c ]]; then
        [[ "${TEST_FAIL:-}" != python-version ]]
    else
        "$TEST_PYTHON" "$TEST_DRIVER" venv "$3"
    fi
}
rsync() {
    record rsync "$@"
    [[ "${TEST_FAIL:-}" != rsync ]] || return 1
    "$TEST_PYTHON" "$TEST_DRIVER" rsync "$@"
}
command() {
    if [[ "${TEST_NO_RSYNC:-0}" == 1 && "$1" == -v && "$2" == rsync ]]; then
        return 1
    fi
    builtin command "$@"
}
'''

MOCK_DRIVER = r'''
import fnmatch
import os
import shutil
import sys
from pathlib import Path

mode, *args = sys.argv[1:]
if mode == 'venv':
    directory = Path(args[0]) / 'bin'
    directory.mkdir(parents=True, exist_ok=True)
    pip = directory / 'pip'
    pip.write_text('#!/bin/bash\nrecord pip "$@"\n[[ "${TEST_FAIL:-}" != pip ]]\n')
    pip.chmod(0o755)
    gunicorn = directory / 'gunicorn'
    gunicorn.write_text('#!/bin/bash\nexit 0\n')
    gunicorn.chmod(0o755)
else:
    patterns = [arg.split('=', 1)[1].rstrip('/') for arg in args if arg.startswith('--exclude=')]
    source, target = map(Path, args[-2:])
    def excluded(name):
        return any(fnmatch.fnmatch(name, pattern) for pattern in patterns)
    for parent, directories, files in os.walk(source):
        directories[:] = [name for name in directories if not excluded(name)]
        destination = target / Path(parent).relative_to(source)
        destination.mkdir(parents=True, exist_ok=True)
        for name in files:
            if not excluded(name):
                shutil.copy2(Path(parent) / name, destination / name)
'''


@unittest.skipUnless(BASH, 'A usable Bash interpreter is required')
class DeploymentTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.source = self.root / 'checkout'
        self.app = self.root / 'installed'
        self.units = self.root / 'units'
        self.projects = self.root / 'projects'
        self.log = self.root / 'commands.log'
        self.log.touch()
        for name in (
            'install.sh', 'update.sh', 'scripts/deployment_common.sh',
            'app.py', 'auth.py', 'config.py', 'requirements.txt',
            'amr_gateway_webui.service', 'templates/base.html', 'static/js/app.js',
        ):
            target = self.source / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes((ROOT / name).read_bytes())
        helper = self.source / 'scripts/deployment_common.sh'
        content = helper.read_text()
        for old, new in (
            ('APP_DIR="/opt/amr_gateway_webui"', f'APP_DIR="{self.app.as_posix()}"'),
            ('SYSTEMD_DIR="/etc/systemd/system"', f'SYSTEMD_DIR="{self.units.as_posix()}"'),
            ('PROJECTS_DIR="/home/admin/projects"', f'PROJECTS_DIR="{self.projects.as_posix()}"'),
        ):
            self.assertIn(old, content)
            content = content.replace(old, new)
        helper.write_text(content, newline='\n')
        mock_env = self.root / 'mock_env.sh'
        mock_env.write_text(MOCK_COMMANDS, newline='\n')
        driver = self.root / 'mock_commands.py'
        driver.write_text(MOCK_DRIVER)
        self.env = os.environ.copy()
        self.env.update(
            BASH_ENV=mock_env.as_posix(), TEST_LOG=self.log.as_posix(),
            TEST_PYTHON=Path(sys.executable).as_posix(), TEST_DRIVER=driver.as_posix(),
            TEST_RUNNING='0', TEST_LEGACY='0', TEST_UID='0', TEST_OS='Linux', TEST_FAIL='',
        )

    def run_script(self, name, installed=False, **environment):
        environment = {**self.env, **environment}
        script = (self.app if installed else self.source) / name
        return subprocess.run([BASH, '--noprofile', '--norc', script.as_posix()],
                              env=environment, capture_output=True, text=True, timeout=45)

    def commands(self):
        return [line.split('\t') for line in self.log.read_text().splitlines()]

    def assert_success(self, result):
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def assert_unit_installed(self):
        deployed = self.units / 'amr_gateway_webui.service'
        self.assertEqual(deployed.read_bytes(), (ROOT / 'amr_gateway_webui.service').read_bytes())
        self.assertIn('--worker-class gthread --threads 2', deployed.read_text())
        self.assertIn(['systemctl', 'daemon-reload'], self.commands())

    def test_bash_syntax_and_lf_deployment_files(self):
        for name in ('install.sh', 'update.sh', 'scripts/deployment_common.sh'):
            result = subprocess.run([BASH, '-n', (ROOT / name).as_posix()],
                                    capture_output=True, text=True, timeout=10)
            self.assert_success(result)
            self.assertNotIn(b'\r\n', (ROOT / name).read_bytes())
        self.assertNotIn(b'\r\n', (ROOT / 'amr_gateway_webui.service').read_bytes())

    def test_fresh_install_deploys_unit_and_excludes_local_state(self):
        for name in ('users.json', '.env', '.env.local', 'venv/dev-only', '.venv/dev-only',
                     '__pycache__/cache.pyc', 'runtime.log', '.git/config'):
            source = self.source / name
            source.parent.mkdir(parents=True, exist_ok=True)
            source.write_text('source-only-test-data')
        self.assert_success(self.run_script('install.sh'))
        self.assert_unit_installed()
        for name in ('users.json', '.env', '.env.local', 'venv/dev-only', '.venv/dev-only',
                     '__pycache__', 'runtime.log', '.git'):
            self.assertFalse((self.app / name).exists(), name)
        for name in ('config_file', 'amr/meter', 'amr/upload', 'amr/scripts', 'tcp_client'):
            self.assertTrue((self.projects / name).is_dir())
        commands = self.commands()
        self.assertIn(['systemctl', 'enable', 'amr_gateway_webui.service'], commands)
        self.assertNotIn(['systemctl', 'restart', 'amr_gateway_webui.service'], commands)
        self.assertNotIn(['systemctl', 'enable', 'eth0-apply.service'], commands)
        self.assertFalse((self.units / 'eth0-apply.service').exists())

    def test_active_upgrade_preserves_credentials_env_and_target_only_files(self):
        self.app.mkdir()
        preserved = {
            'users.json': 'test-password-hash-and-session-secret',
            '.env': 'test-local-settings',
            'local.conf': 'target-only',
            'venv/keep.txt': 'keep-virtualenv',
        }
        for name, content in preserved.items():
            target = self.app / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content)
        (self.source / 'users.json').write_text('do-not-copy-source-users')
        self.assert_success(self.run_script('install.sh', TEST_RUNNING='1'))
        for name, content in preserved.items():
            self.assertEqual((self.app / name).read_text(), content)
        self.assert_unit_installed()
        commands = self.commands()
        self.assertLess(commands.index(['systemctl', 'daemon-reload']),
                        commands.index(['systemctl', 'restart', 'amr_gateway_webui.service']))

    def test_quick_update_installs_new_unit_before_restart(self):
        self.assert_success(self.run_script('install.sh'))
        (self.units / 'amr_gateway_webui.service').write_text('old-sync-unit')
        (self.app / 'users.json').write_text('test-preserved-users')
        self.log.write_text('')
        self.assert_success(self.run_script('update.sh'))
        self.assert_unit_installed()
        self.assertEqual((self.app / 'users.json').read_text(), 'test-preserved-users')
        commands = self.commands()
        self.assertLess(commands.index(['systemctl', 'daemon-reload']),
                        commands.index(['systemctl', 'restart', 'amr_gateway_webui.service']))
        self.assertFalse(any(command[0] in ('apt-get', 'pip', 'python3') for command in commands))

    def test_both_scripts_support_in_place_deployment(self):
        self.assert_success(self.run_script('install.sh'))
        for name in ('install.sh', 'update.sh'):
            with self.subTest(name=name):
                self.log.write_text('')
                result = self.run_script(name, installed=True)
                self.assert_success(result)
                self.assertIn('skipping file copy', result.stdout)
                self.assertFalse(any(command[0] == 'rsync' for command in self.commands()))
                self.assert_unit_installed()

    def test_nonroot_and_nonlinux_fail_before_any_changes(self):
        for environment in ({'TEST_UID': '1000'}, {'TEST_OS': 'MINGW64_NT'}):
            for name in ('install.sh', 'update.sh'):
                with self.subTest(environment=environment, name=name):
                    result = self.run_script(name, **environment)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertEqual(self.commands(), [])
                    self.assertFalse(self.app.exists())

    def test_incomplete_source_is_rejected_before_packages_or_services(self):
        (self.source / 'app.py').unlink()
        result = self.run_script('install.sh')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Incomplete source tree', result.stderr)
        self.assertEqual(self.commands(), [])

    def test_update_requires_existing_virtualenv(self):
        result = self.run_script('update.sh')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('full install.sh', result.stderr)
        self.assertEqual(self.commands(), [])
        self.assertFalse(self.app.exists())

    def test_update_requires_rsync_before_copying(self):
        result = self.run_script('update.sh', TEST_NO_RSYNC='1')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('rsync is missing', result.stderr)
        self.assertEqual(self.commands(), [])

    def test_reload_failure_prevents_restart(self):
        self.assert_success(self.run_script('install.sh'))
        self.log.write_text('')
        result = self.run_script('update.sh', TEST_FAIL='systemctl')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(['systemctl', 'daemon-reload'], self.commands())
        self.assertNotIn(['systemctl', 'restart', 'amr_gateway_webui.service'], self.commands())

    def test_package_or_dependency_failure_does_not_install_unit_or_restart(self):
        for failure in ('apt-get', 'python-version', 'rsync', 'pip'):
            with self.subTest(failure=failure):
                result = self.run_script('install.sh', TEST_FAIL=failure, TEST_RUNNING='1')
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse(self.units.exists())
                self.assertNotIn(['systemctl', 'restart', 'amr_gateway_webui.service'], self.commands())

    def test_existing_network_helper_is_warned_about_not_changed(self):
        self.units.mkdir()
        network_unit = self.units / 'eth0-apply.service'
        network_unit.write_text('existing-network-settings')
        result = self.run_script('install.sh', TEST_LEGACY='1')
        self.assert_success(result)
        self.assertIn('left unchanged', result.stdout)
        self.assertEqual(network_unit.read_text(), 'existing-network-settings')
        self.assertFalse(any(command[0] == 'systemctl' and command[1] in ('enable', 'disable', 'restart')
                             and 'eth0-apply.service' in command for command in self.commands()))


if __name__ == '__main__':
    unittest.main()
