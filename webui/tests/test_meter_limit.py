"""Meter CSV limits with temporary files; no actual meter configuration is edited."""

import csv
import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest.mock import ANY, Mock, call, patch

from flask import Flask

import auth
from blueprints import amr


class MeterLimitTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.path = self.root / 'upload' / 'meter_batch.csv'
        for patcher in (
            patch.object(amr, 'METER_BATCH_FILE', str(self.path)),
            patch.object(amr, 'AMR_UPLOAD_DIR', str(self.path.parent)),
            patch.object(auth, '_load_users', return_value={
                role: {'role': role} for role in ('admin', 'dec', 'amr')
            }),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)
        app = Flask(__name__)
        app.config.update(TESTING=True, SECRET_KEY='test-only')
        auth.login_manager.init_app(app)
        app.register_blueprint(auth.auth_bp)
        app.register_blueprint(amr.amr_bp)
        self.client = app.test_client()
        self.login('admin')

    def login(self, role):
        with self.client.session_transaction() as session:
            session.clear()
            if role:
                session['_user_id'] = role
                session['_fresh'] = True

    def write(self, rows):
        self.path.parent.mkdir(exist_ok=True)
        with self.path.open('w', newline='') as f:
            csv.writer(f).writerows(rows)

    def rows(self):
        with self.path.open(newline='') as f:
            return list(csv.reader(f))

    def test_missing_file_is_not_created(self):
        response = self.client.get('/api/amr/meters')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json(), {'meters': [], 'truncated': False})
        self.assertFalse(self.path.parent.exists())

    def test_empty_small_and_exact_limit_files_are_not_rewritten(self):
        for count in (0, 1, 31, 32):
            with self.subTest(count=count):
                self.write([[str(i), 'meter.cfg'] for i in range(count)])
                before = self.path.read_bytes()
                with patch.object(amr, '_write_meter_rows') as write:
                    response = self.client.get('/api/amr/meters')
                self.assertEqual(len(response.get_json()['meters']), count)
                self.assertFalse(response.get_json()['truncated'])
                self.assertEqual(self.path.read_bytes(), before)
                write.assert_not_called()

    def test_oversized_files_keep_exactly_first_32_and_report_trimming_once(self):
        for count in (33, 100):
            with self.subTest(count=count):
                rows = [[f'SN{i}', 'meter.cfg', f'Alt{i}'] for i in range(count)]
                self.write(rows)
                response = self.client.get('/api/amr/meters')
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.headers['Cache-Control'], 'no-store')
                data = response.get_json()
                self.assertTrue(data['truncated'])
                self.assertEqual(len(data['meters']), 32)
                self.assertEqual(data['meters'][-1]['meter_sn'], 'SN31')
                self.assertEqual(self.rows(), rows[:32])
                self.assertFalse(self.client.get('/api/amr/meters').get_json()['truncated'])

    def test_valid_row_count_ignores_blanks_and_preserves_retained_csv_fields(self):
        retained = [[' SN0 ', 'cfg,with,commas.cfg', 'Alt\nAddress', 'extra-column']]
        retained.extend([[str(i), 'two-fields.cfg'] for i in range(1, 32)])
        self.write([[], ['invalid']] + retained[:5] + [[]] + retained[5:] + [['33', 'excess.cfg']])
        response = self.client.get('/api/amr/meters')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.rows(), retained)
        self.assertEqual(response.get_json()['meters'][0], {
            'meter_sn': 'SN0', 'config_file': 'cfg,with,commas.cfg', 'alt_address': 'Alt\nAddress',
        })
        self.assertEqual(response.get_json()['meters'][1]['alt_address'], '')

    def test_small_files_retain_even_ignored_rows_and_original_format(self):
        self.write([[], ['ignored'], [' SN ', 'cfg', ' alt ', 'extra']])
        before = self.path.read_bytes()
        data = self.client.get('/api/amr/meters').get_json()
        self.assertEqual(len(data['meters']), 1)
        self.assertEqual(self.path.read_bytes(), before)

    def test_all_signed_in_roles_receive_capped_file_anonymous_cannot_modify(self):
        for role in ('admin', 'dec', 'amr', None):
            self.write([[str(i), 'meter.cfg'] for i in range(33)])
            self.login(role)
            response = self.client.get('/api/amr/meters')
            self.assertEqual(response.status_code, 200 if role else 302)
            self.assertEqual(len(self.rows()), 32 if role else 33)

    def test_atomic_trim_failure_preserves_original_and_cleans_temporary_file(self):
        self.write([[str(i), 'meter.cfg'] for i in range(33)])
        before = self.path.read_bytes()
        with patch.object(amr.os, 'replace', side_effect=PermissionError('test-only')):
            response = self.client.get('/api/amr/meters')
        self.assertEqual(response.status_code, 500)
        self.assertIn('Unable to load or trim', response.get_json()['error'])
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual({p.name for p in self.path.parent.iterdir()},
                         {'meter_batch.csv', 'meter_batch.csv.lock'})

    @unittest.skipIf(os.name == 'nt', 'POSIX file modes are not available on Windows')
    def test_trimming_preserves_file_mode_and_owner(self):
        self.write([[str(i), 'meter.cfg'] for i in range(33)])
        self.path.chmod(0o640)
        before = self.path.stat()
        self.assertEqual(self.client.get('/api/amr/meters').status_code, 200)
        after = self.path.stat()
        self.assertEqual(stat.S_IMODE(after.st_mode), stat.S_IMODE(before.st_mode))
        self.assertEqual((after.st_uid, after.st_gid), (before.st_uid, before.st_gid))

    def test_save_limit_and_permissions_still_enforced(self):
        self.write([['preserve', 'meter.cfg']])
        before = self.path.read_bytes()
        response = self.client.post('/api/amr/meters', json={'meters': [
            {'meter_sn': str(i), 'config_file': 'meter.cfg'} for i in range(33)
        ]})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.path.read_bytes(), before)
        self.login('amr')
        self.assertEqual(self.client.post('/api/amr/meters', json={'meters': []}).status_code, 403)
        self.assertEqual(self.path.read_bytes(), before)
        self.login('dec')
        self.assertEqual(self.client.post('/api/amr/meters', json={'meters': [
            {'meter_sn': str(i), 'config_file': 'meter.cfg'} for i in range(32)
        ]}).status_code, 200)
        self.assertEqual(len(self.rows()), 32)

    def test_lock_uses_stable_sidecar_and_releases_posix_lock(self):
        flock = Mock(LOCK_EX=2, LOCK_UN=8)
        with patch.dict('sys.modules', {'fcntl': flock}):
            with amr._meter_batch_lock():
                self.assertTrue(Path(str(self.path) + '.lock').exists())
            self.assertEqual(flock.flock.call_args_list, [call(ANY, 2), call(ANY, 8)])

    def test_atomic_writer_preserves_posix_owner_when_running_as_root(self):
        self.write([[str(i), 'meter.cfg'] for i in range(33)])
        before = self.path.stat()
        with patch.object(amr.os, 'geteuid', return_value=0, create=True):
            with patch.object(amr.os, 'chown', create=True) as chown:
                self.assertEqual(self.client.get('/api/amr/meters').status_code, 200)
        chown.assert_called_once_with(ANY, before.st_uid, before.st_gid)

    def test_save_failure_preserves_existing_file(self):
        self.write([['preserve', 'meter.cfg']])
        before = self.path.read_bytes()
        with patch.object(amr.os, 'replace', side_effect=PermissionError('test-only')):
            response = self.client.post('/api/amr/meters', json={'meters': []})
        self.assertEqual(response.status_code, 500)
        self.assertEqual(self.path.read_bytes(), before)


if __name__ == '__main__':
    unittest.main()
