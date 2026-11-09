/** Run with node tests/test_sms_delete_ui.js; never sends real modem requests. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const flush = async () => { for (let i = 0; i < 12; i++) await Promise.resolve(); };

function element() {
  const classes = new Set();
  return {
    children: [], style: {}, textContent: '', disabled: false,
    dataset: { csrfToken: 'test-csrf' }, listeners: {},
    appendChild(child) { this.children.push(child); },
    replaceChildren() { this.children = []; },
    addEventListener(name, callback) { this.listeners[name] = callback; },
    classList: {
      add: name => classes.add(name), remove: name => classes.delete(name),
      contains: name => classes.has(name),
      toggle: (name, force) => force ? classes.add(name) : classes.delete(name),
    },
  };
}

function setup(manage = true) {
  const elements = {};
  for (const id of ['smsNavItem', 'lteNavItem', 'sms', 'lte', 'rtu-tab', 'sms-tab',
    'smsMessages', 'smsStatus', 'smsError', 'smsRefreshBtn', 'smsDeleteAllBtn', 'mainTabs']) {
    elements[id] = element();
  }
  elements.smsMessages.children.push({ textContent: 'Keep until deletion is confirmed' });
  const events = {};
  const requests = [];
  const pending = [];
  const confirmations = [];
  const context = vm.createContext({
    CAN_MANAGE: manage,
    document: {
      getElementById: id => elements[id], createElement: element,
      addEventListener: (name, callback) => { events[name] = callback; },
    },
    window: { location: {} },
    bootstrap: { Tab: { getOrCreateInstance: () => ({ show() {} }) } },
    confirm: text => { confirmations.push(text); return true; },
    fetch: (url, options) => {
      requests.push({ url, options });
      return new Promise((resolve, reject) => pending.push({ resolve, reject }));
    },
  });
  vm.runInContext(fs.readFileSync(path.join(__dirname, '../static/js/sms.js'), 'utf8'), context);
  context.updateSmsPresence(true);
  return {
    context, elements, requests, confirmations, events,
    complete: async (data = {}, response = {}) => {
      pending.shift().resolve({ ok: true, status: 200, json: async () => data, ...response });
      await flush();
    },
    fail: async () => { pending.shift().reject(new Error('Connection lost')); await flush(); },
  };
}

async function main() {
  const test = setup();
  const deletion = test.context.deleteAllSmsMessages();
  assert.match(test.confirmations[0], /currently selected SMS storage/);
  assert.match(test.confirmations[0], /read, unread, sent and unsent/);
  assert.match(test.confirmations[0], /cannot be undone/);
  assert.equal(test.elements.smsRefreshBtn.disabled, true);
  assert.equal(test.elements.smsDeleteAllBtn.disabled, true);
  assert.equal(test.elements.smsMessages.children.length, 1, 'Do not clear before confirmation from modem');
  await test.context.deleteAllSmsMessages();
  await test.context.loadSmsMessages();
  assert.equal(test.requests.length, 1, 'No duplicate delete or concurrent read during deletion');
  assert.equal(test.requests[0].url, '/api/lte/sms/delete-all');
  assert.equal(test.requests[0].options.method, 'POST');
  assert.equal(test.requests[0].options.headers['X-CSRF-Token'], 'test-csrf');
  assert.deepEqual(JSON.parse(test.requests[0].options.body), { confirm: true });
  await test.complete({ present: true, storage: 'ME', used: 0, capacity: 100, message: 'All messages deleted.' });
  await deletion;
  assert.equal(test.elements.smsMessages.children.length, 0);
  assert.match(test.elements.smsStatus.textContent, /All messages deleted/);
  assert.equal(test.elements.smsRefreshBtn.disabled, false);
  assert.equal(test.elements.smsDeleteAllBtn.disabled, false);

  const cancelled = setup();
  cancelled.context.confirm = () => false;
  await cancelled.context.deleteAllSmsMessages();
  assert.equal(cancelled.requests.length, 0);
  assert.equal(cancelled.elements.smsMessages.children.length, 1);
  const readonly = setup(false);
  await readonly.context.deleteAllSmsMessages();
  assert.equal(readonly.requests.length, 0);
  const absent = setup();
  absent.context.updateSmsPresence(false);
  await absent.context.deleteAllSmsMessages();
  assert.equal(absent.requests.length, 0);
  assert.equal(absent.elements.smsDeleteAllBtn.disabled, true);

  for (const response of [
    { data: { error: 'Delete result is uncertain', present: true }, status: 503 },
    { data: { error: 'Messages remain', present: true, used: 1 }, status: 409 },
    { data: { error: 'Invalid CSRF token' }, status: 403 },
  ]) {
    const failed = setup();
    const request = failed.context.deleteAllSmsMessages();
    await failed.complete(response.data, { ok: false, status: response.status });
    await request;
    assert.equal(failed.elements.smsMessages.children.length, 1);
    assert.match(failed.elements.smsStatus.textContent, /not confirmed/);
    assert.match(failed.elements.smsError.textContent, /Refresh SMS/);
    assert.equal(failed.elements.smsDeleteAllBtn.disabled, false);
  }
  const lost = setup();
  const interrupted = lost.context.deleteAllSmsMessages();
  await lost.fail();
  await interrupted;
  assert.equal(lost.elements.smsMessages.children.length, 1);
  assert.match(lost.elements.smsStatus.textContent, /not confirmed/);

  const reading = setup();
  const read = reading.context.loadSmsMessages();
  await reading.context.deleteAllSmsMessages();
  assert.equal(reading.requests.length, 1, 'Read and delete share a busy guard');
  await reading.complete({ present: true, storage: 'ME', used: 1, capacity: 100,
    messages: [{ index: 1, sender: '+12345678901234567890', timestamp: '26/11/09,10:00:00+32',
      status: 'REC READ', text: '<script>Safe text</script>' }] });
  await read;
  const sender = reading.elements.smsMessages.children[0].children[1];
  assert.equal(sender.className, 'sms-sender');
  assert.equal(sender.textContent, '+12345678901234567890', 'Long senders wrap, never truncate data');
  assert.equal(reading.elements.smsMessages.children[0].children[4].textContent, '<script>Safe text</script>');

  for (const response of [{ status: 401 }, { redirected: true }]) {
    const expired = setup();
    const request = expired.context.deleteAllSmsMessages();
    await expired.complete({}, response);
    await request;
    assert.equal(expired.context.window.location.href, '/login');
  }
  const wired = setup();
  wired.events.DOMContentLoaded();
  await wired.complete({ present: true });
  assert.equal(wired.elements.smsDeleteAllBtn.listeners.click, wired.context.deleteAllSmsMessages);
  const template = fs.readFileSync(path.join(__dirname, '../templates/sms.html'), 'utf8');
  assert.match(template, /15ch/);
  assert.match(template, />DateTime<\/th>/);
  assert.doesNotMatch(template, /Received \(SMS timestamp\)|Received messages in the modem/);
  console.log('SMS Delete All and column frontend checks passed');
}

main().catch(error => { console.error(error); process.exitCode = 1; });
