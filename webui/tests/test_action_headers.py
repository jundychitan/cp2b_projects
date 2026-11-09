"""Render action headers for every role without accessing device state."""

import unittest
from html.parser import HTMLParser
from pathlib import Path
from unittest.mock import patch

from flask import Flask, render_template
from flask_login import login_required

import auth
import config
from blueprints import amr, connection, diagnostic, rtu


class HeaderParser(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.headers = []
        self.actions = {}
        self.ids = {}
        self.stack = []
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        header = next((node for node in reversed(self.stack)
                       if 'card-header' in node['attrs'].get('class', '').split()), None)
        card = next((node for node in reversed(self.stack)
                     if 'card' in node['attrs'].get('class', '').split()), None)
        panel = next((node['attrs']['id'] for node in reversed(self.stack)
                      if 'tab-pane' in node['attrs'].get('class', '').split()), None)
        node = {'tag': tag, 'attrs': attrs, 'title': '', 'header': header, 'card': card, 'panel': panel}
        if 'id' in attrs:
            self.ids.setdefault(attrs['id'], []).append(node)
        if 'card-header' in attrs.get('class', '').split():
            self.headers.append(node)
        if tag == 'button' and 'onclick' in attrs:
            self.actions.setdefault(attrs['onclick'], []).append(node)
        if tag not in ('area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input',
                       'link', 'meta', 'param', 'source', 'track', 'wbr'):
            self.stack.append(node)

    def handle_data(self, data):
        if self.stack and self.stack[-1]['tag'] == 'h5':
            header = self.stack[-1]['header']
            if header is not None:
                header['title'] += data.strip()
                if header['card'] is not None:
                    header['card']['title'] += data.strip()

    def handle_endtag(self, tag):
        for index in range(len(self.stack) - 1, -1, -1):
            if self.stack[index]['tag'] == tag:
                del self.stack[index:]
                break


class ActionHeaderTests(unittest.TestCase):
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

    def page(self, role):
        with self.client.session_transaction() as session:
            session.clear()
            session['_user_id'] = role
            session['_fresh'] = True
        response = self.client.get('/')
        self.assertEqual(response.status_code, 200)
        return HeaderParser(response.get_data(as_text=True))

    def test_operational_actions_are_in_their_labeled_headers_once(self):
        expected = {
            'Internet Watchdog': ('addWatchdogTarget()', 'saveWatchdogTargets()'),
            'Network Setting': ('saveRtuNetwork()',),
            'AMR Server Setting': ('saveRtuServer()', 'saveRtuLocation()'),
            'Auto Read': ('saveAutoRead()',),
            'Config File Editor': ('saveConfigFile()', 'saveConfigFileAs()', 'deleteConfigFile()'),
        }
        for role in ('admin', 'dec', 'amr'):
            page = self.page(role)
            for title, handlers in expected.items():
                for handler in handlers:
                    with self.subTest(role=role, title=title, handler=handler):
                        actions = page.actions.get(handler, [])
                        self.assertEqual(len(actions), 1)
                        action = actions[0]
                        self.assertIsNotNone(action['header'])
                        self.assertEqual(action['header']['title'], title)
                        classes = action['attrs']['class'].split()
                        self.assertIn('manage-only', classes)
                        self.assertIn('btn-sm', classes)
                        self.assertEqual(action['attrs']['type'], 'button')
                        self.assertIn('flex-wrap', action['header']['attrs']['class'].split())

    def test_existing_meter_controls_remain_in_meter_batch_header(self):
        page = self.page('admin')
        for handler in ('chooseMeterBatchUpload()', 'downloadMeterBatch()', 'addMeterRow()', 'saveMeters()'):
            self.assertEqual(page.actions[handler][0]['header']['title'], 'Meter Batch File')

    def test_binding_reset_remains_platform_specific_and_admin_only(self):
        for role in ('admin', 'dec', 'amr'):
            with self.subTest(role=role):
                page = self.page(role)
                actions = page.actions.get('resetRtuBinding()', [])
                expected = role == 'admin' and hasattr(config, 'MACHINE_BINDING_SCRIPT')
                self.assertEqual(len(actions), 1 if expected else 0)
                if expected:
                    self.assertEqual(actions[0]['header']['title'], 'MAC Address')
                    self.assertEqual(actions[0]['attrs']['id'], 'rtuResetBinding')
                    self.assertTrue(actions[0]['attrs']['data-csrf-token'])

    def test_rtu_card_order_and_fields_are_preserved_without_duplicates(self):
        expected = {
            'MAC Address': ('rtuMac',),
            'Network Setting': ('rtuDhcp', 'rtuStatic', 'rtuIp', 'rtuSubnet', 'rtuGateway', 'rtuDns'),
            'AMR Server Setting': ('rtuServerIp', 'rtuLocation'),
            'Internet Watchdog': ('watchdogTargets',),
        }
        for role in ('admin', 'dec', 'amr'):
            with self.subTest(role=role):
                page = self.page(role)
                self.assertEqual([h['title'] for h in page.headers if h['panel'] == 'rtu'],
                                 list(expected))
                for title, fields in expected.items():
                    for field in fields:
                        nodes = page.ids[field]
                        self.assertEqual(len(nodes), 1)
                        self.assertEqual(nodes[0]['panel'], 'rtu')
                        self.assertEqual(nodes[0]['card']['title'], title)
                bound = page.ids.get('rtuBoundMac', [])
                has_binding = role == 'admin' and hasattr(config, 'MACHINE_BINDING_SCRIPT')
                self.assertEqual(len(bound), 1 if has_binding else 0)
                if bound:
                    self.assertEqual(bound[0]['card']['title'], 'MAC Address')
                self.assertEqual([h['title'] for h in page.headers if h['panel'] == 'lte'], ['LTE Status'])

    def test_rtu_is_active_and_modem_tabs_are_initially_hidden(self):
        page = self.page('admin')
        for name in ('rtu-tab', 'rtu'):
            self.assertIn('active', page.ids[name][0]['attrs']['class'].split())
        for name in ('lte-tab', 'lte', 'sms-tab', 'sms'):
            self.assertNotIn('active', page.ids[name][0]['attrs']['class'].split())
        for name in ('lteNavItem', 'smsNavItem'):
            self.assertIn('d-none', page.ids[name][0]['attrs']['class'].split())


if __name__ == '__main__':
    unittest.main()
