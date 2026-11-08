/** Run with node tests/test_password_reset_ui.js; all requests are mocked. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

function setup(admin = true) {
  const elements = {};
  for (const id of ['resetUserPasswordTarget', 'resetUserPasswordBtn', 'resetUserPasswordAlert']) {
    const classes = new Set(['d-none']);
    elements[id] = {
      value: 'dec', disabled: false, dataset: { csrfToken: 'test-csrf' }, classes,
      classList: { add: name => classes.add(name), remove: name => classes.delete(name) },
    };
  }
  const requests = [];
  const confirmations = [];
  let pending;
  const context = vm.createContext({
    IS_ADMIN: admin, window: { location: {} },
    document: { getElementById: id => elements[id] },
    confirm: text => { confirmations.push(text); return true; },
    fetch: (url, options) => {
      requests.push({ url, options });
      return new Promise((resolve, reject) => { pending = { resolve, reject }; });
    },
  });
  vm.runInContext(fs.readFileSync(path.join(__dirname, '../static/js/password_reset.js'), 'utf8'), context);
  return {
    context, elements, requests, confirmations,
    reply: (data, extra = {}) => pending.resolve({
      ok: true, status: 200, json: async () => data, ...extra,
    }),
    fail: () => pending.reject(new Error('Disconnected')),
  };
}

async function main() {
  for (const username of ['dec', 'amr']) {
    const test = setup();
    test.elements.resetUserPasswordTarget.value = username;
    const request = test.context.resetUserPassword();
    assert.match(test.confirmations[0], new RegExp(`for ${username} to 1234`));
    assert.equal(test.elements.resetUserPasswordBtn.disabled, true);
    assert.equal(test.elements.resetUserPasswordTarget.disabled, true);
    assert.equal(test.elements.resetUserPasswordBtn.textContent, 'Resetting...');
    await test.context.resetUserPassword();
    assert.equal(test.requests.length, 1, 'No duplicate in-flight reset');
    assert.equal(test.requests[0].url, '/api/auth/reset-password');
    assert.equal(test.requests[0].options.method, 'POST');
    assert.equal(test.requests[0].options.headers['X-CSRF-Token'], 'test-csrf');
    assert.deepEqual(JSON.parse(test.requests[0].options.body), { username });
    test.reply({ message: 'Password reset' });
    await request;
    assert.equal(test.elements.resetUserPasswordAlert.textContent, 'Password reset');
    assert.equal(test.elements.resetUserPasswordAlert.className, 'alert alert-success');
    assert.equal(test.elements.resetUserPasswordAlert.classes.has('d-none'), false);
    assert.equal(test.elements.resetUserPasswordBtn.disabled, false);
    assert.equal(test.elements.resetUserPasswordTarget.disabled, false);
    assert.equal(test.elements.resetUserPasswordBtn.textContent, 'Reset to 1234');
  }
  const cancel = setup();
  cancel.context.confirm = () => false;
  await cancel.context.resetUserPassword();
  assert.equal(cancel.requests.length, 0);
  const nonadmin = setup(false);
  await nonadmin.context.resetUserPassword();
  assert.equal(nonadmin.requests.length, 0);
  const invalid = setup();
  invalid.elements.resetUserPasswordTarget.value = 'admin';
  await invalid.context.resetUserPassword();
  assert.equal(invalid.requests.length, 0);
  assert.equal(invalid.confirmations.length, 0);
  const rejected = setup();
  const rejection = rejected.context.resetUserPassword();
  rejected.reply({ error: 'Invalid CSRF token' }, { ok: false, status: 403 });
  await rejection;
  assert.equal(rejected.elements.resetUserPasswordAlert.textContent, 'Invalid CSRF token');
  assert.equal(rejected.elements.resetUserPasswordAlert.className, 'alert alert-danger');
  const failure = setup();
  const disconnected = failure.context.resetUserPassword();
  failure.fail();
  await disconnected;
  assert.match(failure.elements.resetUserPasswordAlert.textContent, /result is unknown/);
  assert.equal(failure.elements.resetUserPasswordBtn.disabled, false);
  assert.equal(failure.elements.resetUserPasswordTarget.disabled, false);
  for (const response of [{ status: 401 }, { redirected: true }]) {
    const test = setup();
    const request = test.context.resetUserPassword();
    test.reply({}, response);
    await request;
    assert.equal(test.context.window.location.href, '/login');
  }
  console.log('Admin password reset frontend checks passed');
}

main().catch(error => {
  console.error(error);
  process.exitCode = 1;
});
