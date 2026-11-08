/** Run with node tests/test_reboot_ui.js; no real timers or device requests. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const flush = async () => { for (let i = 0; i < 12; i++) await Promise.resolve(); };

function monitor(replies) {
  let now = 0;
  let timerId = 0;
  const timers = new Map();
  const elements = { rebootTitle: {}, rebootStatus: {} };
  const redirects = [];
  const requests = [];
  const context = vm.createContext({
    Date: { now: () => now },
    AbortController,
    document: { getElementById: id => elements[id] },
    window: { location: { replace: url => redirects.push(url) } },
    setTimeout: (fn, delay) => {
      const id = ++timerId;
      timers.set(id, { fn, delay });
      return id;
    },
    clearTimeout: id => timers.delete(id),
    fetch: async (url, options) => {
      requests.push({ url, options });
      assert.ok(replies.length, 'Unexpected readiness request');
      const reply = replies.shift();
      if (reply === 'hang') {
        return new Promise((resolve, reject) => {
          options.signal.addEventListener('abort', () => reject(new Error('Aborted')));
        });
      }
      if (reply instanceof Error) throw reply;
      return { ok: true, json: async () => ({ status: 'ready' }), ...reply };
    },
  });
  vm.runInContext(fs.readFileSync(path.join(__dirname, '../static/js/reboot.js'), 'utf8'), context);
  return {
    elements, redirects, requests, timers,
    setNow: value => { now = value; },
    tick: async delay => {
      const entry = [...timers].find(([, timer]) => timer.delay === delay);
      assert.ok(entry, `Missing ${delay}ms timer`);
      timers.delete(entry[0]);
      now += delay;
      await entry[1].fn();
      await flush();
    },
  };
}

function rebootRequest(manage = true) {
  const elements = {
    rebootDeviceBtn: { disabled: false, dataset: { csrfToken: 'test-csrf' } },
    statusAlert: { classList: { add() {}, remove() {} } },
  };
  const replies = [];
  const redirects = [];
  const requests = [];
  const context = vm.createContext({
    CAN_MANAGE: manage,
    document: { getElementById: id => elements[id], addEventListener() {} },
    window: { location: { replace: url => redirects.push(url) } },
    setTimeout() {},
    fetch: async (url, options) => {
      requests.push({ url, options });
      const reply = replies.shift();
      if (reply instanceof Error) throw reply;
      return { ok: true, status: 200, json: async () => ({}), ...reply };
    },
  });
  vm.runInContext(fs.readFileSync(path.join(__dirname, '../static/js/app.js'), 'utf8'), context);
  return { context, elements, replies, redirects, requests };
}

async function main() {
  const cycle = monitor([{}, {}, new Error('Offline'), {}]);
  await flush();
  assert.deepEqual(cycle.redirects, [], 'Do not redirect while still online before reboot');
  await cycle.tick(2000);
  assert.deepEqual(cycle.redirects, []);
  await cycle.tick(2000);
  assert.match(cycle.elements.rebootStatus.textContent, /offline/);
  await cycle.tick(2000);
  assert.deepEqual(cycle.redirects, ['/login']);
  assert.equal(cycle.timers.size, 0, 'Stop polling on navigation');
  assert.equal(cycle.requests[0].options.cache, 'no-store');
  assert.equal(cycle.requests[0].options.credentials, 'omit');
  assert.equal(cycle.requests[0].options.redirect, 'error');

  const timeout = monitor([{}]);
  await flush();
  timeout.setNow(300000);
  await timeout.tick(2000);
  assert.match(timeout.elements.rebootTitle.textContent, /not yet available/);
  assert.match(timeout.elements.rebootStatus.textContent, /IP address/);
  assert.equal(timeout.timers.size, 0);
  assert.deepEqual(timeout.redirects, []);

  const hanging = monitor(['hang', {}]);
  await flush();
  await hanging.tick(3000);
  assert.equal(hanging.requests[0].options.signal.aborted, true);
  assert.match(hanging.elements.rebootStatus.textContent, /offline/);
  await hanging.tick(2000);
  assert.deepEqual(hanging.redirects, ['/login']);

  const wrong = monitor([
    new Error('offline'),
    { json: async () => ({ status: 'not-ready' }) },
    { ok: false },
    { json: async () => { throw new Error('Not JSON'); } },
    {},
  ]);
  await flush();
  for (let i = 0; i < 3; i++) {
    await wrong.tick(2000);
    assert.deepEqual(wrong.redirects, [], 'Reject errors and unexpected readiness responses');
  }
  await wrong.tick(2000);
  assert.deepEqual(wrong.redirects, ['/login']);

  const request = rebootRequest();
  request.replies.push({});
  const first = request.context.rebootDevice();
  await request.context.rebootDevice();
  await first;
  assert.equal(request.requests.length, 1, 'Prevent duplicate submissions');
  assert.equal(request.requests[0].options.headers['X-CSRF-Token'], 'test-csrf');
  assert.deepEqual(request.redirects, ['/rebooting']);

  const failure = rebootRequest();
  failure.replies.push({ ok: false, json: async () => ({ error: 'Scheduling failed' }) });
  await failure.context.rebootDevice();
  assert.deepEqual(failure.redirects, []);
  assert.match(failure.elements.statusAlert.textContent, /Scheduling failed/);
  assert.equal(failure.elements.rebootDeviceBtn.disabled, false);
  failure.replies.push(new Error('network'));
  await failure.context.rebootDevice();
  assert.match(failure.elements.statusAlert.textContent, /unknown/);
  assert.deepEqual(failure.redirects, []);

  const expired = rebootRequest();
  expired.replies.push({ redirected: true });
  await expired.context.rebootDevice();
  assert.deepEqual(expired.redirects, ['/login']);

  const readonly = rebootRequest(false);
  await readonly.context.rebootDevice();
  assert.equal(readonly.requests.length, 0);
  console.log('Reboot workflow frontend checks passed');
}

main().catch(error => {
  console.error(error);
  process.exitCode = 1;
});
