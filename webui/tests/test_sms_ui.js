/** Run with node tests/test_sms_ui.js; no browser or modem needed. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

function element() {
  const classes = new Set();
  return {
    children: [], style: {}, textContent: '', disabled: false, listeners: {},
    appendChild(child) { this.children.push(child); },
    replaceChildren() { this.children = []; },
    addEventListener(event, callback) { this.listeners[event] = callback; },
    classList: {
      add(name) { classes.add(name); },
      remove(name) { classes.delete(name); },
      contains(name) { return classes.has(name); },
      toggle(name, force) { if (force) classes.add(name); else classes.delete(name); },
    },
    set innerHTML(value) { throw new Error('SMS must not use HTML rendering'); },
  };
}

async function main() {
  const elements = Object.fromEntries([
    'smsNavItem', 'smsMessages', 'smsStatus', 'smsError', 'sms', 'sms-tab',
    'lteNavItem', 'lte', 'lte-tab', 'rtu-tab', 'smsRefreshBtn', 'mainTabs',
  ].map(id => [id, element()]));
  const replies = [];
  const requests = [];
  const listeners = {};
  let fallback = 0;
  const context = vm.createContext({
    document: {
      getElementById: id => elements[id],
      createElement: element,
      addEventListener: (name, callback) => { listeners[name] = callback; },
    },
    window: { location: {} },
    bootstrap: { Tab: { getOrCreateInstance: target => {
      assert.equal(target, elements['rtu-tab'], 'Unavailable modem tabs must fall back to RTU');
      return { show() { fallback++; } };
    } } },
    fetch: async (url, options) => {
      requests.push({ url, options });
      assert.ok(replies.length, 'Unexpected request');
      const reply = replies.shift();
      if (reply instanceof Error) throw reply;
      return { ok: true, status: 200, json: async () => reply, ...reply.http };
    },
  });
  vm.runInContext(fs.readFileSync(path.join(__dirname, '../static/js/sms.js'), 'utf8'), context);
  context.updateSmsPresence(false);
  assert.equal(elements.smsNavItem.classList.contains('d-none'), true);
  assert.equal(elements.lteNavItem.classList.contains('d-none'), true);
  replies.push({ present: true });
  await context.refreshSmsPresence();
  assert.equal(elements.smsNavItem.classList.contains('d-none'), false);
  assert.equal(elements.lteNavItem.classList.contains('d-none'), false);
  assert.equal(requests[0].url, '/api/lte/presence');
  assert.equal(requests[0].options.cache, 'no-store');

  const malicious = '<img src=x onerror=alert(1)>\n你好 & OK ERROR';
  replies.push({
    present: true, storage: 'ME', used: 1, capacity: 100,
    messages: [{ index: 1, sender: '<script>', timestamp: '26/11/09,10:00:00+32',
      status: 'REC UNREAD', text: malicious }],
  });
  const first = context.loadSmsMessages();
  assert.equal(elements.smsRefreshBtn.disabled, true);
  await context.loadSmsMessages();
  await first;
  assert.equal(requests.filter(r => r.url === '/api/lte/sms').length, 1);
  assert.equal(elements.smsRefreshBtn.disabled, false);
  assert.equal(elements.smsMessages.children[0].children[4].textContent, malicious);
  assert.equal(elements.smsMessages.children[0].children[3].textContent, 'Unread');
  assert.match(elements.smsStatus.textContent, /1\/100/);

  replies.push({ present: true, storage: 'SM', used: 0, capacity: 20, messages: [] });
  await context.loadSmsMessages();
  assert.equal(elements.smsMessages.children.length, 0);
  assert.match(elements.smsStatus.textContent, /No received SMS/);
  replies.push({ present: true, error: 'SIM unavailable', http: { ok: false, status: 503 } });
  await context.loadSmsMessages();
  assert.equal(elements.smsError.textContent, 'SIM unavailable');
  assert.equal(elements.smsError.classList.contains('d-none'), false);
  assert.equal(elements.smsRefreshBtn.disabled, false);
  replies.push(new Error('offline'));
  await context.loadSmsMessages();
  assert.equal(elements.smsError.textContent, 'offline');

  elements.sms.classList.add('active');
  context.updateSmsPresence(false);
  assert.equal(fallback, 1);
  elements.sms.classList.remove('active');
  elements.lte.classList.add('active');
  context.updateSmsPresence(false);
  assert.equal(fallback, 2, 'Losing the modem from LTE Status also falls back to RTU');
  elements.lte.classList.remove('active');
  assert.equal(elements.smsMessages.children.length, 0);
  replies.push({ http: { redirected: true } });
  await context.loadSmsMessages();
  assert.equal(context.window.location.href, '/login');
  assert.equal(elements.smsRefreshBtn.disabled, false);

  replies.push({ present: false });
  listeners.DOMContentLoaded();
  await context.refreshSmsPresence();
  assert.equal(elements['sms-tab'].listeners['shown.bs.tab'], context.loadSmsMessages);
  assert.equal(elements.smsRefreshBtn.listeners.click, context.loadSmsMessages);
  const base = fs.readFileSync(path.join(__dirname, '../templates/base.html'), 'utf8');
  assert.match(base, /class="nav-item d-none" id="smsNavItem"/);
  assert.match(base, /class="nav-item d-none" id="lteNavItem"/);
  assert.match(base, />LTE Status<\/button>/);
  assert.match(base, /include 'sms.html'/);
  console.log('SMS UI checks passed');
}

main().catch(error => { console.error(error); process.exitCode = 1; });
