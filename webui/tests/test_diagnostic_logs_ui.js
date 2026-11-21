/** Run with node tests/test_diagnostic_logs_ui.js; no real logs or network I/O. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

function setup(manage = true) {
  const elements = {};
  for (const id of ['diagCmLogsBtn', 'diagPingLogsBtn', 'diagCmDeleteBtn', 'diagPingDeleteBtn']) {
    elements[id] = { disabled: false, dataset: { csrfToken: 'test-csrf' } };
  }
  elements.diagOutput = { textContent: 'old log history' };
  const requests = [];
  const pending = [];
  const alerts = [];
  const confirmations = [];
  const context = vm.createContext({
    CAN_MANAGE: manage,
    document: { getElementById: id => elements[id], addEventListener() {} },
    window: { location: {} },
    confirm: message => { confirmations.push(message); return true; },
    fetch: (url, options) => new Promise((resolve, reject) => {
      requests.push({ url, options });
      pending.push({ resolve, reject });
    }),
  });
  vm.runInContext(fs.readFileSync(path.join(__dirname, '../static/js/app.js'), 'utf8'), context);
  context.showAlert = (...args) => alerts.push(args);
  return {
    context, elements, requests, pending, alerts, confirmations,
    reply(data, response = {}) {
      pending.shift().resolve({ status: 200, ok: true, json: async () => data, ...response });
    },
  };
}

async function main() {
  for (const source of ['cm', 'ping']) {
    const test = setup();
    const deleting = test.context.diagDeleteLogs(source);
    assert.equal(test.requests.length, 1);
    assert.match(test.confirmations[0], /cannot be undone/);
    assert.match(test.confirmations[0], /journal history will remain/);
    assert.match(test.confirmations[0], /rotated copies/);
    assert.equal(test.requests[0].url, `/api/diagnostic/logs/${source}/delete`);
    assert.equal(test.requests[0].options.method, 'POST');
    assert.equal(test.requests[0].options.headers['X-CSRF-Token'], 'test-csrf');
    assert.equal(test.requests[0].options.body, '{"confirm":true}');
    Object.entries(test.elements).filter(([id]) => id !== 'diagOutput')
      .forEach(([, button]) => assert.equal(button.disabled, true));
    await test.context.diagDeleteLogs(source);
    await test.context.diagCheckCM();
    await test.context.diagPingLogs();
    assert.equal(test.requests.length, 1, 'No overlapping read or duplicate delete');
    test.reply({ message: 'Log files deleted. Journal history remains.' });
    await deleting;
    assert.equal(test.elements.diagOutput.textContent, 'Log files deleted. Journal history remains.');
    assert.equal(test.alerts.at(-1)[1], 'success');
    assert.equal(test.elements.diagCmDeleteBtn.disabled, false);
    assert.equal(test.elements.diagPingLogsBtn.disabled, false);
  }

  const cancelled = setup();
  cancelled.context.confirm = () => false;
  await cancelled.context.diagDeleteLogs('cm');
  assert.equal(cancelled.requests.length, 0);
  assert.equal(cancelled.elements.diagOutput.textContent, 'old log history');
  const readonly = setup(false);
  await readonly.context.diagDeleteLogs('cm');
  assert.equal(readonly.requests.length, 0);
  assert.equal(readonly.confirmations.length, 0);
  const invalid = setup();
  await invalid.context.diagDeleteLogs('../journal');
  assert.equal(invalid.requests.length, 0);

  const failed = setup();
  const deletion = failed.context.diagDeleteLogs('ping');
  failed.reply({ error: 'Some log data may already have been deleted' }, { ok: false, status: 409 });
  await deletion;
  assert.equal(failed.elements.diagOutput.textContent, 'old log history');
  assert.match(failed.alerts.at(-1)[0], /may already/);
  assert.equal(failed.elements.diagPingDeleteBtn.disabled, false);
  const lost = setup();
  const uncertain = lost.context.diagDeleteLogs('cm');
  lost.pending.shift().reject(new Error('offline'));
  await uncertain;
  assert.match(lost.alerts.at(-1)[0], /result is unknown/);
  assert.equal(lost.requests.length, 1, 'Never retry destructive requests automatically');
  for (const response of [{ status: 401 }, { redirected: true }]) {
    const expired = setup();
    const request = expired.context.diagDeleteLogs('cm');
    expired.reply({}, response);
    await request;
    assert.equal(expired.context.window.location.href, '/login');
    assert.equal(expired.elements.diagOutput.textContent, 'old log history');
  }

  for (const action of ['diagCheckCM', 'diagPingLogs']) {
    const test = setup(false);
    const reading = test.context[action]();
    assert.equal(test.requests.length, 1, 'Read-only accounts can still view logs');
    await test.context.diagCheckCM();
    assert.equal(test.requests.length, 1);
    test.reply({ output: 'log line', success: true });
    await reading;
    assert.match(test.elements.diagOutput.textContent, /log line/);
    assert.equal(test.elements.diagCmLogsBtn.disabled, false);
    assert.equal(test.elements.diagCmDeleteBtn.disabled, true);
    test.context.clearDiagnostic();
    assert.equal(test.requests.length, 1, 'Clear changes the display only');
    assert.equal(test.elements.diagOutput.textContent, 'Waiting for command...');
  }
  console.log('Diagnostic log deletion frontend checks passed');
}

main().catch(error => { console.error(error); process.exitCode = 1; });
