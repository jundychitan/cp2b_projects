"""Diagnostic log deletion tests never read or modify real system logs."""

import os
import stat
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from flask import Flask, render_template
from flask_login import login_required

from auth import auth_bp, login_manager
from blueprints import diagnostic


class DiagnosticLogTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.paths = {
            'cm': (str(self.root / 'connection_manager.log'), 3),
            'ping': (str(self.root / 'internet-watchdog.log'), 5),
        }
        patcher = patch.object(diagnostic, 'DIAGNOSTIC_LOGS', self.paths)
        patcher.start()
        self.addCleanup(patcher.stop)
        patcher = patch('auth._load_users', return_value={
            role: {'role': role} for role in ('admin', 'dec', 'amr')
        })
        patcher.start()
        self.addCleanup(patcher.stop)
        app = Flask(__name__, template_folder=str(Path(__file__).resolve().parents[1] / 'templates'))
        app.config.update(TESTING=True, SECRET_KEY='test-only')
        login_manager.init_app(app)
        app.register_blueprint(auth_bp)
        app.register_blueprint(diagnostic.diagnostic_bp)

        @app.route('/test-diagnostic')
        @login_required
        def page():
            return render_template('diagnostic.html')

        self.client = app.test_client()
        self.login('admin')

    def login(self, role):
        with self.client.session_transaction() as session:
            session.clear()
            if role:
                session['_user_id'] = role
                session['_fresh'] = True
                session['diagnostic_logs_csrf_token'] = 'test-csrf'

    def delete(self, source='cm', body=None, token='test-csrf'):
        return self.client.post(
            '/api/diagnostic/logs/' + source + '/delete',
            json={'confirm': True} if body is None else body,
            headers={'X-CSRF-Token': token},
        )

    def populate(self, source):
        path, copies = self.paths[source]
        for index in range(copies + 1):
            Path(path if index == 0 else f'{path}.{index}').write_text('history\n')
        return Path(path), copies

    def test_each_source_clears_active_and_exact_rotations_only(self):
        for source in ('cm', 'ping'):
            with self.subTest(source=source):
                active, copies = self.populate(source)
                inode = active.stat().st_ino
                other = 'ping' if source == 'cm' else 'cm'
                other_active, _ = self.populate(other)
                unrelated = Path(str(active) + '.unrelated')
                unrelated.write_text('unrelated')
                outside_range = Path(str(active) + f'.{copies + 1}')
                outside_range.write_text('outside range')
                with patch.object(diagnostic.subprocess, 'run') as command:
                    response = self.delete(source)
                self.assertEqual(response.status_code, 200, response.get_json())
                self.assertEqual(response.get_json()['files_cleared'], copies + 1)
                self.assertIn('journal history was not deleted', response.get_json()['message'])
                self.assertEqual(active.read_text(), '')
                self.assertEqual(active.stat().st_ino, inode)
                for index in range(1, copies + 1):
                    self.assertFalse(Path(str(active) + f'.{index}').exists())
                self.assertEqual(other_active.read_text(), 'history\n')
                self.assertEqual(unrelated.read_text(), 'unrelated')
                self.assertEqual(outside_range.read_text(), 'outside range')
                self.assertEqual(response.headers['Cache-Control'], 'no-store')
                command.assert_not_called()

    def test_active_file_stays_usable_for_append_logging(self):
        active, _ = self.populate('cm')
        with active.open('a') as writer:
            self.assertEqual(self.delete().status_code, 200)
            writer.write('new entry\n')
            writer.flush()
        self.assertEqual(active.read_text(), 'new entry\n')

    def test_missing_files_are_already_empty_and_not_created(self):
        response = self.delete()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()['files_cleared'], 0)
        self.assertIn('No saved', response.get_json()['message'])
        self.assertFalse(Path(self.paths['cm'][0]).exists())

    def test_permissions_before_any_filesystem_access(self):
        for role, expected in ((None, 302), ('amr', 403)):
            with self.subTest(role=role):
                self.login(role)
                with patch.object(diagnostic, '_clear_log_files') as clear:
                    self.assertEqual(self.delete().status_code, expected)
                    clear.assert_not_called()
        for role in ('admin', 'dec'):
            self.login(role)
            self.assertEqual(self.delete().status_code, 200)

    def test_csrf_required(self):
        for token in ('', 'wrong', '\u00e9'):
            with self.subTest(token=token), patch.object(diagnostic, '_clear_log_files') as clear:
                self.assertEqual(self.delete(token=token).status_code, 403)
                clear.assert_not_called()
        with self.client.session_transaction() as session:
            del session['diagnostic_logs_csrf_token']
        self.assertEqual(self.delete().status_code, 403)

    def test_explicit_boolean_confirmation_required(self):
        for body in ({}, {'confirm': False}, {'confirm': 1}, {'confirm': 'true'}, []):
            with self.subTest(body=body), patch.object(diagnostic, '_clear_log_files') as clear:
                self.assertEqual(self.delete(body=body).status_code, 400)
                clear.assert_not_called()

    def test_unknown_source_and_get_cannot_delete(self):
        with patch.object(diagnostic, '_clear_log_files') as clear:
            self.assertEqual(self.delete('journal').status_code, 404)
            self.assertEqual(self.client.get('/api/diagnostic/logs/cm/delete').status_code, 405)
            clear.assert_not_called()

    def test_client_supplied_path_cannot_expand_scope(self):
        victim = self.root / 'do-not-touch.txt'
        victim.write_text('safe')
        self.populate('cm')
        response = self.delete(body={'confirm': True, 'path': str(victim), 'source': 'journal'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(victim.read_text(), 'safe')

    def test_preflight_rejects_nonregular_rotated_entry_without_partial_delete(self):
        active = Path(self.paths['cm'][0])
        active.write_text('keep')
        Path(str(active) + '.1').mkdir()
        response = self.delete()
        self.assertEqual(response.status_code, 409)
        self.assertEqual(active.read_text(), 'keep')

    def test_hard_links_are_refused_without_changing_the_target(self):
        active = Path(self.paths['cm'][0])
        victim = self.root / 'victim'
        victim.write_text('keep')
        try:
            os.link(victim, active)
        except OSError:
            self.skipTest('Hard links are unavailable')
        self.assertEqual(self.delete().status_code, 409)
        self.assertEqual(victim.read_text(), 'keep')

    def test_symlinks_are_refused_without_changing_the_target(self):
        victim = self.root / 'victim'
        victim.write_text('keep')
        active = Path(self.paths['cm'][0])
        try:
            active.symlink_to(victim)
        except OSError:
            self.skipTest('Symlink creation is unavailable on this machine')
        self.assertEqual(self.delete().status_code, 409)
        self.assertEqual(victim.read_text(), 'keep')

    def test_linked_file_preflight_without_platform_symlink_support(self):
        linked = SimpleNamespace(st_mode=stat.S_IFLNK | 0o777, st_nlink=1)
        with patch.object(diagnostic.os.path, 'realpath', return_value=str(self.root)), \
                patch.object(diagnostic.os, 'lstat', return_value=linked), \
                patch.object(diagnostic.os, 'open') as opened:
            with self.assertRaisesRegex(ValueError, 'refused'):
                diagnostic._clear_log_files('cm')
            opened.assert_not_called()

    def test_replacement_before_open_is_not_truncated(self):
        self.populate('cm')
        victim = self.root / 'victim'
        victim.write_text('keep')
        real_open = os.open
        with patch.object(diagnostic.os, 'open', side_effect=lambda path, flags: real_open(str(victim), flags)):
            response = self.delete()
        self.assertEqual(response.status_code, 500)
        self.assertEqual(victim.read_text(), 'keep')

    def test_partial_failure_does_not_claim_success(self):
        active, _ = self.populate('cm')
        with patch.object(diagnostic.os, 'unlink', side_effect=PermissionError('denied')):
            response = self.delete()
        self.assertEqual(response.status_code, 409)
        self.assertIn('may already', response.get_json()['error'])
        self.assertEqual(active.read_text(), '')
        self.assertTrue(Path(str(active) + '.1').exists())

    def test_permission_failure_before_changes_reports_failure(self):
        active, _ = self.populate('cm')
        with patch.object(diagnostic.os, 'open', side_effect=PermissionError('denied')):
            response = self.delete()
        self.assertEqual(response.status_code, 500)
        self.assertEqual(active.read_text(), 'history\n')

    def test_views_read_the_same_dedicated_files_and_never_journalctl(self):
        for source, endpoint in (('cm', 'cm-status'), ('ping', 'ping-logs')):
            with self.subTest(source=source):
                with patch.object(diagnostic, '_run', return_value=(True, 'line', '')) as run:
                    response = self.client.post('/api/diagnostic/' + endpoint)
                self.assertEqual(response.get_json()['output'], 'line')
                self.assertEqual(run.call_args.args[0], 'tail -n 100 ' + self.paths[source][0])
                self.assertEqual(response.headers['Cache-Control'], 'no-store')

    def test_delete_controls_and_token_are_role_restricted(self):
        for role in ('admin', 'dec', 'amr'):
            with self.subTest(role=role):
                self.login(role)
                response = self.client.get('/test-diagnostic')
                self.assertEqual(response.status_code, 200)
                html = response.get_data(as_text=True)
                self.assertEqual('id="diagCmDeleteBtn"' in html, role != 'amr')
                self.assertEqual('id="diagPingDeleteBtn"' in html, role != 'amr')
                self.assertIn('diagCmLogsBtn', html)
                if role != 'amr':
                    self.assertIn('data-csrf-token="test-csrf"', html)


if __name__ == '__main__':
    unittest.main()
