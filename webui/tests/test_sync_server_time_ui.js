/** Run with node tests/test_sync_server_time_ui.js; filesystem and requests are mocked. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

function setup(canManage = true) {
  const checkbox = { checked: false, disabled: true, dataset: { csrfToken: 'test-csrf' } };
  const alert = { classList: { add() {}, remove() {} } };
  const requests = [];
  let pending;
  const context = vm.createContext({
    CAN_MANAGE: canManage,
    document: {
      getElementById: id => id === 'syncServerTime' ? checkbox : alert,
      addEventListener() {},
    },
    window: { location: {} },
    setTimeout() {},
    fetch: (url, options) => {
      requests.push({ url, options });
      return new Promise((resolve, reject) => { pending = { resolve, reject }; });
    },
  });
  const source = fs.readFileSync(path.join(__dirname, '../static/js/app.js'), 'utf8');
  vm.runInContext(source, context);
  return {
    context, checkbox, alert, requests, source,
    reply: (data, response = {}) => pending.resolve({
      ok: true, status: 200, json: async () => data, ...response,
    }),
    fail: () => pending.reject(new Error('Disconnected')),
  };
}

async function load(test, enabled = false) {
  const request = test.context.loadSyncServerTime();
  assert.equal(test.checkbox.disabled, true);
  test.reply({ enabled });
  await request;
}

async function main() {
  const test = setup();
  await test.context.saveSyncServerTime();
  assert.equal(test.requests.length, 0, 'Do not write before initial state is loaded');
  await load(test);
  assert.equal(test.requests[0].url, '/api/amr/sync-time');
  assert.equal(test.requests[0].options.cache, 'no-store');
  assert.equal(test.checkbox.checked, false);
  assert.equal(test.checkbox.disabled, false);
  for (const enabled of [true, false]) {
    test.checkbox.checked = enabled;
    const request = test.context.saveSyncServerTime();
    assert.equal(test.checkbox.disabled, true);
    const count = test.requests.length;
    await test.context.saveSyncServerTime();
    await test.context.loadSyncServerTime();
    assert.equal(test.requests.length, count, 'No overlapping requests during save');
    const saved = test.requests[count - 1];
    assert.equal(saved.options.method, 'POST');
    assert.equal(saved.options.headers['X-CSRF-Token'], 'test-csrf');
    assert.deepEqual(JSON.parse(saved.options.body), { enabled });
    test.reply({ enabled, message: 'Saved' });
    await request;
    assert.equal(test.checkbox.checked, enabled);
    assert.equal(test.checkbox.disabled, false);
    assert.equal(test.alert.className, 'alert alert-success');
  }
  await load(test, true);
  assert.equal(test.checkbox.checked, true, 'Reload reflects current marker existence');

  test.checkbox.checked = false;
  let request = test.context.saveSyncServerTime();
  test.reply({ error: 'Permission denied' }, { ok: false, status: 500 });
  await request;
  assert.equal(test.checkbox.checked, true, 'Rejected save restores last known state');
  assert.equal(test.alert.textContent, 'Permission denied');

  test.checkbox.checked = false;
  request = test.context.saveSyncServerTime();
  test.fail();
  await request;
  assert.equal(test.checkbox.disabled, true, 'Unknown save state requires a refresh');
  assert.match(test.alert.textContent, /result is unknown/);
  await load(test, false);
  assert.equal(test.checkbox.disabled, false);
  assert.equal(test.checkbox.checked, false);

  const readonly = setup(false);
  await load(readonly, true);
  assert.equal(readonly.checkbox.checked, true);
  assert.equal(readonly.checkbox.disabled, true);
  await readonly.context.saveSyncServerTime();
  assert.equal(readonly.requests.length, 1, 'Read-only users cannot write');

  const unavailable = setup();
  request = unavailable.context.loadSyncServerTime();
  unavailable.fail();
  await request;
  assert.equal(unavailable.checkbox.disabled, true);
  await load(unavailable, true);
  assert.equal(unavailable.checkbox.disabled, false);
  request = unavailable.context.loadSyncServerTime();
  unavailable.reply({ error: 'Cannot read' }, { ok: false, status: 500 });
  await request;
  assert.equal(unavailable.checkbox.disabled, true);
  assert.equal(unavailable.alert.textContent, 'Cannot read');

  for (const response of [{ status: 401 }, { redirected: true }]) {
    const expired = setup();
    request = expired.context.loadSyncServerTime();
    expired.reply({}, response);
    await request;
    assert.equal(expired.context.window.location.href, '/login');
  }
  const template = fs.readFileSync(path.join(__dirname, '../templates/amr.html'), 'utf8');
  assert.match(template, /for="syncServerTime">Sync Server Time<\/label>/);
  assert.match(template, /onchange="saveSyncServerTime\(\)"/);
  assert.doesNotMatch(template, /Saved immediately\. Checked creates/);
  assert.match(test.source, /'amr-tab'[\s\S]*?loadSyncServerTime\(\);/);
  console.log('Sync Server Time frontend checks passed');
}

main().catch(error => {
  console.error(error);
  process.exitCode = 1;
});
