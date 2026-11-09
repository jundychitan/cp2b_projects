/** Run with node tests/test_rtu_layout_ui.js; modem, tabs, requests and timers are mocked. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const flush = async () => { for (let i = 0; i < 16; i++) await Promise.resolve(); };

async function main() {
  const elements = {};
  const events = {};
  const timers = new Map();
  const requests = [];
  const pending = [];
  let timerId = 0;
  let rtuLoads = 0;
  let watchdogLoads = 0;
  function element(id) {
    if (!elements[id]) {
      const classes = new Set();
      elements[id] = {
        id, value: '', style: {}, textContent: '', listeners: {},
        replaceChildren() {},
        addEventListener: (name, callback) => { elements[id].listeners[name] = callback; },
        classList: {
          add: name => classes.add(name), remove: name => classes.delete(name),
          contains: name => classes.has(name),
          toggle: (name, force) => force ? classes.add(name) : classes.delete(name),
        },
      };
    }
    return elements[id];
  }
  for (const id of ['rtu', 'rtu-tab']) element(id).classList.add('active');
  for (const id of ['lteNavItem', 'smsNavItem']) element(id).classList.add('d-none');
  function showTab(target) {
    const panel = target.id.replace('-tab', '');
    if (element(panel).classList.contains('active')) return;
    for (const name of ['rtu', 'lte', 'sms']) {
      if (element(name).classList.contains('active')) {
        element(name).classList.remove('active');
        element(name + '-tab').classList.remove('active');
        element(name + '-tab').listeners['hidden.bs.tab']?.();
      }
    }
    element(panel).classList.add('active');
    target.classList.add('active');
    target.listeners['shown.bs.tab']?.();
    element('mainTabs').listeners['shown.bs.tab']?.();
  }
  const context = vm.createContext({
    CAN_MANAGE: true, IS_ADMIN: false,
    document: {
      hidden: false, getElementById: element, querySelectorAll: () => [],
      addEventListener: (name, callback) => { (events[name] ||= []).push(callback); },
    },
    window: { location: {} },
    bootstrap: { Tab: { getOrCreateInstance: target => ({ show: () => showTab(target) }) } },
    setTimeout: (fn, delay) => { timers.set(++timerId, { fn, delay }); return timerId; },
    clearTimeout: id => timers.delete(id),
    fetch: (url, options) => {
      requests.push({ url, options });
      return new Promise((resolve, reject) => pending.push({ url, resolve, reject }));
    },
  });
  for (const name of ['app.js', 'sms.js']) {
    vm.runInContext(fs.readFileSync(path.join(__dirname, '../static/js', name), 'utf8'), context);
  }
  context.updateThemeIcon = () => {};
  context.loadRtuConfig = () => { rtuLoads++; };
  context.loadWatchdogTargets = () => { watchdogLoads++; };
  const complete = async (url, data) => {
    const index = pending.findIndex(request => request.url === url);
    assert.notEqual(index, -1, `Missing request ${url}`);
    pending.splice(index, 1)[0].resolve({ ok: true, status: 200, json: async () => data });
    await flush();
  };
  events.DOMContentLoaded.forEach(callback => callback());
  assert.equal(rtuLoads, 1);
  assert.equal(watchdogLoads, 1);
  assert.equal(requests.length, 1, 'Startup checks presence without polling hidden LTE status');
  assert.equal(requests[0].url, '/api/lte/presence');
  await complete('/api/lte/presence', { present: false });
  assert.equal(element('rtu').classList.contains('active'), true);
  assert.equal(element('lteNavItem').classList.contains('d-none'), true);
  assert.equal(element('smsNavItem').classList.contains('d-none'), true);
  assert.equal(timers.size, 0);

  const presence = context.refreshSmsPresence();
  await complete('/api/lte/presence', { present: true });
  await presence;
  assert.equal(element('lteNavItem').classList.contains('d-none'), false);
  assert.equal(element('smsNavItem').classList.contains('d-none'), false);
  assert.equal(element('rtu').classList.contains('active'), true, 'Detection does not steal the selected tab');
  assert.equal(requests.filter(request => request.url === '/api/lte/status').length, 0);

  showTab(element('lte-tab'));
  assert.equal(watchdogLoads, 1, 'Watchdog no longer loads when selecting LTE');
  await complete('/api/lte/presence', { present: true });
  await complete('/api/lte/status', { present: true, available: false, status: 'Unavailable' });
  assert.equal(element('lteNavItem').classList.contains('d-none'), false,
    'A present but unresponsive/disconnected modem remains visible like SMS');
  assert.equal(timers.size, 1);
  assert.equal([...timers.values()][0].delay, 60000);

  const removed = context.refreshSmsPresence();
  await complete('/api/lte/presence', { present: false });
  await removed;
  assert.equal(element('rtu').classList.contains('active'), true);
  assert.equal(element('lteNavItem').classList.contains('d-none'), true);
  assert.equal(element('smsNavItem').classList.contains('d-none'), true);
  assert.equal(rtuLoads, 2);
  assert.equal(watchdogLoads, 2, 'Returning to RTU reloads Watchdog independently of modem state');
  assert.equal(timers.size, 0, 'Removal stops status polling before the request can reschedule');

  const recovered = context.refreshSmsPresence();
  await complete('/api/lte/presence', { present: true });
  await recovered;
  assert.equal(element('lteNavItem').classList.contains('d-none'), false);
  assert.equal(element('rtu').classList.contains('active'), true);
  const count = requests.filter(request => request.url === '/api/lte/status').length;
  assert.equal(count, 1, 'Showing modem tabs alone does not restart hidden LTE polling');
  console.log('RTU layout and conditional LTE/SMS integration checks passed');
}

main().catch(error => {
  console.error(error);
  process.exitCode = 1;
});
