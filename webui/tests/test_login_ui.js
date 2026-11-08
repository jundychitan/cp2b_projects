/** Run with node tests/test_login_ui.js; no real timers, passwords, or device I/O. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = name => fs.readFileSync(path.join(__dirname, '../static/js', name), 'utf8');
const flush = async () => { for (let i = 0; i < 12; i++) await Promise.resolve(); };

function polling(canManage = true) {
  let timerId = 0;
  const timers = new Map();
  const requests = [];
  const pending = [];
  const elements = {};
  for (const id of ['lteStatus', 'lteCarrier', 'lteAccess', 'lteBand', 'lteSignal',
    'lteRebootBtn', 'lteRefreshBtn', 'diagLteCheckBtn', 'lteSimStatus', 'lteIpAddress']) {
    elements[id] = { style: {} };
  }
  const context = vm.createContext({
    CAN_MANAGE: canManage,
    document: { getElementById: id => elements[id], addEventListener() {} },
    setTimeout: (fn, delay) => {
      const id = ++timerId;
      timers.set(id, { fn, delay });
      return id;
    },
    clearTimeout: id => timers.delete(id),
    setInterval: () => assert.fail('LTE polling must not use fixed intervals'),
    fetch: url => {
      requests.push(url);
      return new Promise((resolve, reject) => pending.push({ resolve, reject }));
    },
  });
  vm.runInContext(source('app.js'), context);
  return {
    context, timers, requests, elements,
    complete: async (data = { status: 'Connected', carrier: 'Test', available: true, present: true,
      sim_status: 'Inserted', ip_address: '10.1.2.3' }) => {
      const request = pending.shift();
      assert.ok(request);
      request.resolve({ status: 200, json: async () => data });
      await flush();
    },
    fail: async () => {
      pending.shift().reject(new Error('Modem not ready'));
      await flush();
    },
    tick: async (delay = 5000) => {
      const entry = [...timers][0];
      assert.ok(entry);
      assert.equal(entry[1].delay, delay);
      timers.delete(entry[0]);
      entry[1].fn();
      await flush();
    },
  };
}

function loginFeedback() {
  const events = {};
  const attributes = {};
  const elements = {
    loginForm: {
      addEventListener: (name, fn) => { events[name] = fn; },
      setAttribute: (name, value) => { attributes[name] = value; },
      removeAttribute: name => { delete attributes[name]; },
    },
    loginButton: {},
    loginProgress: {},
  };
  const context = vm.createContext({
    document: { getElementById: id => elements[id] },
    window: { addEventListener: (name, fn) => { events[name] = fn; } },
  });
  vm.runInContext(source('login.js'), context);
  let prevented = 0;
  const event = { preventDefault: () => { prevented++; } };
  events.submit(event);
  assert.equal(prevented, 0, 'Keep the first native form submission');
  assert.equal(elements.loginButton.disabled, true);
  assert.match(elements.loginButton.textContent, /Signing in/);
  assert.equal(attributes['aria-busy'], 'true');
  assert.match(elements.loginProgress.textContent, /Verifying credentials/);
  events.submit(event);
  assert.equal(prevented, 1, 'Prevent repeated submissions');
  events.pageshow();
  assert.equal(elements.loginButton.disabled, false);
  assert.equal(elements.loginButton.textContent, 'Login');
  assert.equal(attributes['aria-busy'], undefined);
  assert.equal(elements.loginProgress.textContent, '');
  events.submit(event);
  assert.equal(prevented, 1, 'Allow submission again after browser back navigation');
}

async function main() {
  const poll = polling();
  poll.context.startLtePolling();
  poll.context.startLtePolling();
  poll.context.loadLteStatus();
  assert.equal(poll.requests.length, 1, 'Only one request while the modem is slow');
  assert.equal(poll.timers.size, 0, 'Do not schedule another check until completion');
  await poll.complete();
  assert.equal(poll.elements.lteStatus.value, 'Connected');
  assert.equal(poll.elements.lteSimStatus.value, 'Inserted');
  assert.equal(poll.elements.lteSimStatus.style.color, 'green');
  assert.equal(poll.elements.lteIpAddress.value, '10.1.2.3');
  assert.equal(poll.timers.size, 1);
  poll.context.startLtePolling();
  assert.equal(poll.requests.length, 1);
  await poll.tick();
  assert.equal(poll.requests.length, 2);
  assert.equal(poll.timers.size, 0);

  poll.context.stopLtePolling();
  await poll.complete();
  assert.equal(poll.timers.size, 0, 'Do not resume polling after leaving the tab');
  poll.context.startLtePolling();
  assert.equal(poll.requests.length, 3);
  poll.context.stopLtePolling();
  poll.context.startLtePolling();
  assert.equal(poll.requests.length, 3, 'Reuse the in-flight request when reopening');
  await poll.fail();
  assert.equal(poll.elements.lteStatus.value, 'Unavailable');
  assert.equal(poll.timers.size, 1, 'Retry after a transient failure');
  poll.context.stopLtePolling();
  assert.equal(poll.timers.size, 0, 'Cancel scheduled retries when hidden');

  const absent = polling();
  absent.context.startLtePolling();
  await absent.complete({ status: 'Not installed', available: false, present: false });
  assert.equal(absent.elements.lteStatus.value, 'Not installed');
  assert.equal(absent.elements.diagLteCheckBtn.disabled, true);
  assert.equal(absent.elements.lteRebootBtn.disabled, true);
  assert.equal(absent.elements.lteCarrier.value, '--');
  assert.equal(absent.elements.lteSimStatus.value, '--');
  assert.equal(absent.elements.lteIpAddress.value, '--');
  await absent.tick(60000);
  await absent.complete();
  assert.equal(absent.elements.lteStatus.value, 'Connected');
  assert.equal(absent.elements.diagLteCheckBtn.disabled, false);
  assert.equal(absent.elements.lteRebootBtn.disabled, false);
  await absent.tick(5000);
  await absent.complete({ status: 'Unavailable', available: false, present: true });
  assert.equal(absent.elements.lteRebootBtn.disabled, true);
  assert.equal([...absent.timers.values()][0].delay, 60000);
  absent.context.stopLtePolling();
  absent.context.startLtePolling();
  assert.equal(absent.requests.length, 4, 'Reopening the tab checks immediately');
  await absent.complete();
  absent.context.stopLtePolling();

  const manual = polling();
  manual.context.startLtePolling();
  assert.equal(manual.elements.lteRefreshBtn.disabled, true);
  assert.match(manual.elements.lteRefreshBtn.textContent, /Refreshing/);
  const duringAutomatic = manual.context.refreshLteStatus();
  assert.equal(manual.requests.length, 1, 'Manual refresh reuses an automatic request');
  await manual.complete({ status: 'Not installed', available: false });
  await duringAutomatic;
  assert.equal(manual.elements.lteRefreshBtn.disabled, false);
  assert.equal(manual.elements.lteRefreshBtn.textContent, 'Refresh');
  assert.equal([...manual.timers.values()][0].delay, 60000);
  const immediate = manual.context.refreshLteStatus();
  const repeated = manual.context.refreshLteStatus();
  assert.equal(manual.requests.length, 2, 'Refresh bypasses backoff but repeated calls do not overlap');
  assert.equal(manual.timers.size, 0, 'Cancel the pending automatic check during manual refresh');
  await manual.complete();
  await Promise.all([immediate, repeated]);
  assert.equal(manual.elements.lteRefreshBtn.disabled, false);
  assert.equal([...manual.timers.values()][0].delay, 5000, 'Resume normal polling after recovery');
  const failed = manual.context.refreshLteStatus();
  await manual.fail();
  await failed;
  assert.equal(manual.elements.lteRefreshBtn.disabled, false, 'Restore Refresh after a failure');
  assert.equal(manual.elements.lteRefreshBtn.textContent, 'Refresh');
  manual.context.stopLtePolling();

  const readonly = polling(false);
  const readonlyRefresh = readonly.context.refreshLteStatus();
  assert.equal(readonly.requests.length, 1, 'Read-only users can refresh status');
  await readonly.complete();
  await readonlyRefresh;
  assert.equal(readonly.elements.lteRebootBtn.disabled, true);
  assert.equal(readonly.elements.lteRefreshBtn.disabled, false);
  assert.equal(readonly.timers.size, 0, 'A refresh outside active polling does not start a polling loop');

  loginFeedback();
  console.log('Login responsiveness and LTE polling checks passed');
}

main().catch(error => {
  console.error(error);
  process.exitCode = 1;
});
