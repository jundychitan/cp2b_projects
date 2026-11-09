"""Meter transfer tests use temporary files, never real device batches."""

import csv
import io
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from flask import Flask, render_template
from flask_login import login_required

import auth
import config
from blueprints import amr


class MeterTransferTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.path = self.root / 'amr' / 'upload' / 'meter_batch.csv'
        self.path.parent.mkdir(parents=True)
        self.original = b'999,original.cfg,1\r\n'
        self.path.write_bytes(self.original)
        for patcher in (
            patch.object(amr, 'METER_BATCH_FILE', str(self.path)),
            patch.object(auth, '_load_users', return_value={
                role: {'role': role} for role in ('admin', 'dec', 'amr')
            }),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)
        app = Flask(__name__, template_folder=str(Path(__file__).resolve().parents[1] / 'templates'))
        app.config.update(TESTING=True, SECRET_KEY='test-only')
        auth.login_manager.init_app(app)
        app.register_blueprint(auth.auth_bp)
        app.register_blueprint(amr.amr_bp)

        @app.route('/')
        @login_required
        def index():
            return render_template('amr.html')

        self.client = app.test_client()
        self.login('admin')

    def login(self, role):
        with self.client.session_transaction() as session:
            session.clear()
            if role:
                session['_user_id'] = role
                session['_fresh'] = True
            session['meter_upload_csrf_token'] = 'test-csrf'

    def upload(self, content=b'00123,meter.cfg,007\n', filename='anything.txt', token='test-csrf'):
        response = self.client.post('/api/amr/meters/upload',
                                    data={'file': (io.BytesIO(content), filename)},
                                    headers={'X-CSRF-Token': token})
        self.addCleanup(response.close)
        self.addCleanup(response.request.input_stream.close)
        return response

    def stored_rows(self):
        with self.path.open(encoding='utf-8', newline='') as f:
            return list(csv.reader(f))

    def test_any_upload_filename_is_ignored_and_default_server_path_is_fixed(self):
        self.assertEqual(config.METER_BATCH_FILE, os.path.join(config.AMR_UPLOAD_DIR, 'meter_batch.csv'))
        for filename in ('batch.txt', 'no_extension', '../../outside.csv', r'C:\temp\batch.exe'):
            with self.subTest(filename=filename):
                response = self.upload(filename=filename)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(self.stored_rows(), [['00123', 'meter.cfg', '007']])
                self.assertEqual(response.get_json()['meters'][0], {
                    'meter_sn': '00123', 'config_file': 'meter.cfg', 'alt_address': '007',
                })
                self.assertEqual(response.headers['Cache-Control'], 'no-store')
                self.assertFalse((self.root / 'outside.csv').exists())

    def test_utf8_bom_quoted_fields_blank_lines_and_whitespace_are_supported(self):
        content = '\ufeff\n0001,"méter, model.cfg",0002\r\n \r\n 3 , any name.cfg , 9999999999999999\n'
        response = self.upload(content.encode('utf-8'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.stored_rows(), [
            ['0001', 'méter, model.cfg', '0002'],
            ['3', 'any name.cfg', '9999999999999999'],
        ])
        # Unknown configs are allowed and remain readable after upload.
        data = self.client.get('/api/amr/meters').get_json()
        self.assertEqual(data['meters'][0]['config_file'], 'méter, model.cfg')

    def test_every_entry_is_validated_before_first_32_are_saved(self):
        content = ''.join(f'{i},meter.cfg,{i}\n' for i in range(40)).encode()
        response = self.upload(content)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.get_json()['truncated'])
        self.assertIn('Validated all 40', response.get_json()['message'])
        self.assertEqual(len(self.stored_rows()), 32)
        self.assertEqual(self.stored_rows()[-1], ['31', 'meter.cfg', '31'])
        before = self.path.read_bytes()
        response = self.upload(content + b'not-a-number,meter.cfg,1\n')
        self.assertEqual(response.status_code, 400)
        self.assertIn('Line 41', response.get_json()['error'])
        self.assertEqual(self.path.read_bytes(), before)

    def test_invalid_schema_and_csv_never_replace_existing_file(self):
        invalid = (
            b'', b'\n \r\n', b'1,meter.cfg\n', b'1,meter.cfg,2,extra\n',
            b'serial,config_file,modbus_id\n', b'-1,meter.cfg,2\n',
            b'1.5,meter.cfg,2\n', b'+1,meter.cfg,2\n', b'1e3,meter.cfg,2\n',
            b'1,meter.txt,2\n', b'1,.cfg,2\n', b'1,../meter.cfg,2\n',
            b'1,/tmp/meter.cfg,2\n', b'1,C:\\tmp\\meter.cfg,2\n',
            b'1,meter\x00.cfg,2\n', b'1,,2\n', b'1,meter.cfg,\n',
            b'1,meter.cfg,-2\n', b'1,meter.cfg,2.5\n', b'1,"meter.cfg"x,2\n',
            b'1,"unterminated.cfg,2\n', '１,meter.cfg,2\n'.encode('utf-8'),
            b'\xff\xfe\x00\x00',
        )
        for content in invalid:
            with self.subTest(content=content):
                response = self.upload(content)
                self.assertEqual(response.status_code, 400, response.get_data(as_text=True))
                self.assertEqual(self.path.read_bytes(), self.original)

    def test_missing_file_part_or_empty_filename_is_rejected(self):
        response = self.client.post('/api/amr/meters/upload',
                                    headers={'X-CSRF-Token': 'test-csrf'})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.upload(filename='').status_code, 400)
        self.assertEqual(self.path.read_bytes(), self.original)

    def test_content_and_multipart_size_limits_preserve_existing_file(self):
        for size in (amr.MAX_METER_UPLOAD_BYTES + 1, amr.MAX_METER_UPLOAD_BYTES + 70000):
            with self.subTest(size=size):
                self.assertEqual(self.upload(b'x' * size).status_code, 413)
                self.assertEqual(self.path.read_bytes(), self.original)

    def test_only_admin_and_dec_can_upload_all_signed_in_roles_can_download(self):
        for role in ('admin', 'dec', 'amr', None):
            with self.subTest(role=role):
                self.login(role)
                before = self.path.read_bytes()
                response = self.upload()
                self.assertEqual(response.status_code, 302 if role is None else (403 if role == 'amr' else 200))
                if role in ('amr', None):
                    self.assertEqual(self.path.read_bytes(), before)
                self.assertEqual(self.client.get('/api/amr/meters/download').status_code, 200 if role else 302)

    def test_csrf_missing_invalid_and_nonascii_are_rejected_before_writes(self):
        for token in ('', 'wrong', '\u00e9'):
            self.assertEqual(self.upload(token=token).status_code, 403)
        with self.client.session_transaction() as session:
            session.pop('meter_upload_csrf_token')
        self.assertEqual(self.upload().status_code, 403)
        self.assertEqual(self.path.read_bytes(), self.original)

    def test_new_install_upload_creates_only_fixed_batch_and_lock(self):
        self.path.unlink()
        response = self.upload(filename='different-name.csv')
        self.assertEqual(response.status_code, 200)
        self.assertEqual({p.name for p in self.path.parent.iterdir()},
                         {'meter_batch.csv', 'meter_batch.csv.lock'})

    def test_failed_atomic_upload_preserves_original_and_cleans_temporary_files(self):
        with patch.object(amr.os, 'replace', side_effect=PermissionError('test-only')):
            response = self.upload()
        self.assertEqual(response.status_code, 500)
        self.assertEqual(self.path.read_bytes(), self.original)
        self.assertEqual({p.name for p in self.path.parent.iterdir()},
                         {'meter_batch.csv', 'meter_batch.csv.lock'})

    def test_download_is_exact_saved_bytes_attachment_without_path_override(self):
        contents = b'0001,"meter, name.cfg",02\r\n'
        self.path.write_bytes(contents)
        response = self.client.get('/api/amr/meters/download?filename=../../users.json')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, contents)
        self.assertEqual(response.mimetype, 'text/csv')
        self.assertIn('attachment', response.headers['Content-Disposition'])
        self.assertIn('meter_batch.csv', response.headers['Content-Disposition'])
        self.assertEqual(response.headers['Cache-Control'], 'no-store')
        self.assertEqual(response.headers['X-Content-Type-Options'], 'nosniff')
        self.assertEqual(self.path.read_bytes(), contents)

    def test_download_missing_and_permission_errors_are_reported(self):
        self.path.unlink()
        self.assertEqual(self.client.get('/api/amr/meters/download').status_code, 404)
        self.path.write_bytes(self.original)
        with patch.object(amr, '_meter_batch_lock', side_effect=PermissionError('test-only')):
            self.assertEqual(self.client.get('/api/amr/meters/download').status_code, 500)

    def test_http_methods_are_restricted(self):
        self.assertEqual(self.client.get('/api/amr/meters/upload').status_code, 405)
        self.assertEqual(self.client.post('/api/amr/meters/download').status_code, 405)

    def test_template_has_any_filename_picker_upload_permissions_and_download(self):
        for role in ('admin', 'dec', 'amr'):
            with self.subTest(role=role):
                self.login(role)
                with self.client.session_transaction() as session:
                    session.pop('meter_upload_csrf_token')
                response = self.client.get('/')
                self.assertEqual(response.status_code, 200)
                html = response.get_data(as_text=True)
                self.assertIn('id="meterBatchDownloadBtn"', html)
                self.assertEqual('id="meterBatchUploadBtn"' in html, role != 'amr')
                self.assertEqual('id="meterBatchUploadFile"' in html, role != 'amr')
                if role != 'amr':
                    picker = html.split('id="meterBatchUploadFile"', 1)[1].split('>', 1)[0]
                    self.assertNotIn('accept=', picker)
                with self.client.session_transaction() as session:
                    self.assertEqual('meter_upload_csrf_token' in session, role != 'amr')


if __name__ == '__main__':
    unittest.main()
