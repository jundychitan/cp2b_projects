"""Device clock API and navbar tests, without actual user-store or hardware access."""

import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from flask import Flask, render_template
from flask_login import login_required

import auth
from blueprints import amr, connection, diagnostic, rtu


class MachineClockTests(unittest.TestCase):
    def setUp(self):
        app = Flask(__name__, template_folder=str(Path(__file__).resolve().parents[1] / 'templates'))
        app.config.update(TESTING=True, SECRET_KEY='test-only')
        auth.login_manager.init_app(app)
        for blueprint in (auth.auth_bp, amr.amr_bp, connection.connection_bp,
                          diagnostic.diagnostic_bp, rtu.rtu_bp):
            app.register_blueprint(blueprint)

        @app.route('/')
        @login_required
        def index():
            return render_template('base.html')

        self.client = app.test_client()
        patcher = patch.object(auth, '_load_users', return_value={
            role: {'role': role} for role in ('admin', 'dec', 'amr')
        })
        patcher.start()
        self.addCleanup(patcher.stop)

    def login(self, role):
        with self.client.session_transaction() as session:
            session.clear()
            if role:
                session['_user_id'] = role
                session['_fresh'] = True

    def test_all_roles_get_actual_device_timestamp_and_offset_uncached(self):
        for offset in (8, -5, 0, 5.5):
            now = datetime(2026, 11, 9, 12, 34, 56, tzinfo=timezone(timedelta(hours=offset)))
            for role in ('admin', 'dec', 'amr'):
                with self.subTest(role=role, offset=offset):
                    self.login(role)
                    with patch.object(diagnostic, 'datetime') as clock:
                        clock.now.return_value.astimezone.return_value = now
                        response = self.client.get('/api/diagnostic/time')
                    self.assertEqual(response.status_code, 200)
                    self.assertEqual(response.headers['Cache-Control'], 'no-store')
                    self.assertEqual(response.get_json(), {
                        'unix_ms': int(now.timestamp() * 1000),
                        'utc_offset_seconds': int(offset * 3600),
                        'timezone': now.tzname(),
                    })

    def test_anonymous_cannot_read_time_and_post_is_disallowed(self):
        self.assertEqual(self.client.get('/api/diagnostic/time').status_code, 302)
        self.login('admin')
        self.assertEqual(self.client.post('/api/diagnostic/time', json={}).status_code, 405)

    def test_navbar_shows_username_once_without_duplicate_role_badge(self):
        for role in ('admin', 'dec', 'amr'):
            with self.subTest(role=role):
                self.login(role)
                response = self.client.get('/')
                self.assertEqual(response.status_code, 200)
                html = response.get_data(as_text=True)
                navbar = html.split('<nav ', 1)[1].split('</nav>', 1)[0]
                self.assertIn(f'Logged in as: <span class="navbar-account-green fw-bold">{role}</span>', navbar)
                self.assertNotIn('class="badge', navbar)
                self.assertIn('id="machineClock"', navbar)
                self.assertNotIn('Device time:', navbar)
                self.assertIn('class="text-white fw-normal" id="loginDetails"', navbar)
                self.assertIn('class="navbar-account-green small text-nowrap" id="machineClock"', navbar)
                self.assertIn('flex-column align-items-end', navbar)
                self.assertLess(navbar.index('>Logout</a>'), navbar.index('id="machineClock"'))
                actions = navbar.split('id="accountActions">', 1)[1].split('</div>', 1)[0]
                self.assertIn('Change Password', actions)
                self.assertIn('Logout', actions)
                self.assertNotIn('id="machineClock"', actions)
                self.assertIn('js/machine_clock.js', html)


if __name__ == '__main__':
    unittest.main()
