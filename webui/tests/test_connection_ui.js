/** Run with node tests/test_connection_ui.js; all requests are mocked. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const enabledState = {
  enabled: true, running: true, boot_state: 'enabled', active_state: 'active', sub_state: 'running',
};
const disabledState = {
  enabled: false, running: false, boot_state: 'disabled', active_state: 'inactive', sub_state: 'dead',
};

function setup(admin = true) {
  const elements = {};
  for (const id of ['connEnableBtn', 'connDisableBtn', 'connRefreshBtn', 'connError',
    'connStatus', 'connBootStatus', 'statusAlert']) {
    const classes = new Set();
    elements[id] = {
      disabled: false, dataset: { csrfToken: 'test-csrf' },
      classList: { add: name => classes.add(name), remove: name => classes.delete(name) },
      classes,
    };
  }
  const requests = [];
  const replies = [];
  const context = vm.createContext({
    IS_ADMIN: admin,
    document: { getElementById: id => elements[id], addEventListener() {} },
    window: { location: {} },
    confirm: () => true,
    setTimeout() {},
    fetch: async (url, options) => {
      requests.push({ url, options });
      assert.ok(replies.length, 'Unexpected request');
      const reply = replies.shift();
      if (reply instanceof Error) throw reply;
      return { status: 200, ok: true, json: async () => reply, ...reply.response };
    },
  });
  vm.runInContext(fs.readFileSync(path.join(__dirname, '../static/js/app.js'), 'utf8'), context);
  return { context, elements, requests, replies };
}

async function main() {
  const admin = setup();
  assert.equal(admin.requests.length, 0, 'Do not fetch service status at script load');
  admin.replies.push(enabledState);
  await admin.context.refreshConnectionStatus();
  assert.match(admin.elements.connStatus.textContent, /Running/);
  assert.equal(admin.elements.connBootStatus.textContent, 'enabled');
  assert.equal(admin.elements.connEnableBtn.disabled, true);
  assert.equal(admin.elements.connDisableBtn.disabled, false);

  admin.context.confirm = () => false;
  await admin.context.setConnectionEnabled(false);
  assert.equal(admin.requests.length, 1, 'Cancel must not send a command');

  admin.context.confirm = () => true;
  admin.replies.push({ message: 'Disabled' }, disabledState);
  await admin.context.setConnectionEnabled(false);
  const toggle = admin.requests[1];
  assert.equal(toggle.url, '/api/connection/toggle');
  assert.equal(toggle.options.headers['X-CSRF-Token'], 'test-csrf');
  assert.deepEqual(JSON.parse(toggle.options.body), { enabled: false });
  assert.equal(admin.requests[2].url, '/api/connection/status');
  assert.equal(admin.elements.connDisableBtn.disabled, true);
  assert.equal(admin.elements.connEnableBtn.disabled, false);
  assert.equal(admin.elements.connRefreshBtn.disabled, false);

  admin.replies.push({ message: 'Enabled' }, enabledState);
  await admin.context.setConnectionEnabled(true);
  assert.deepEqual(JSON.parse(admin.requests[3].options.body), { enabled: true });
  assert.equal(admin.elements.connEnableBtn.disabled, true);

  admin.replies.push(new Error('Disconnected'), disabledState);
  await admin.context.setConnectionEnabled(false);
  assert.match(admin.elements.statusAlert.textContent, /result is unknown/);
  assert.equal(admin.elements.connRefreshBtn.disabled, false);
  assert.equal(admin.elements.connEnableBtn.disabled, false);

  admin.replies.push({
    error: 'Service is not installed', response: { ok: false, status: 503 },
  });
  await admin.context.refreshConnectionStatus();
  assert.equal(admin.elements.connStatus.textContent, 'Unavailable');
  assert.equal(admin.elements.connEnableBtn.disabled, true);
  assert.equal(admin.elements.connDisableBtn.disabled, true);
  assert.match(admin.elements.connError.textContent, /not installed/);
  assert.equal(admin.elements.connError.classes.has('d-none'), false);

  admin.replies.push({
    error: 'Service has not reached the requested state', response: { ok: false, status: 409 },
  }, disabledState);
  await admin.context.setConnectionEnabled(true);
  assert.match(admin.elements.statusAlert.textContent, /not reached/);
  assert.equal(admin.elements.statusAlert.className, 'alert alert-danger');

  const nonadmin = setup(false);
  await nonadmin.context.refreshConnectionStatus();
  await nonadmin.context.loadConnectionStatus();
  await nonadmin.context.setConnectionEnabled(false);
  await nonadmin.context.setConnectionEnabled(true);
  assert.equal(nonadmin.requests.length, 0);

  console.log('Connection Manager frontend checks passed');
}

main().catch(error => {
  console.error(error);
  process.exitCode = 1;
});
