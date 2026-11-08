/** Run with node tests/test_rtu_network_ui.js; no device/network operations. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

function setup(isStatic = true, canManage = true) {
  const elements = {};
  for (const id of ['rtuStatic', 'rtuIp', 'rtuSubnet', 'rtuGateway', 'rtuDns',
    'rtuNetworkNotice', 'rtuNetworkMessage', 'rtuNetworkDetail', 'rtuNetworkReturn',
    'statusAlert']) {
    const classes = new Set(['d-none']);
    elements[id] = {
      checked: isStatic, value: '', disabled: false, classes,
      classList: { add: name => classes.add(name), remove: name => classes.delete(name) },
    };
  }
  elements.rtuIp.value = ' 192.168.10.50 ';
  elements.rtuSubnet.value = '24';
  elements.rtuGateway.value = '192.168.10.1';
  elements.rtuDns.value = '8.8.8.8';
  const requests = [];
  const timers = new Map();
  let timerId = 0;
  let pending;
  const context = vm.createContext({
    CAN_MANAGE: canManage, AbortController,
    document: { getElementById: id => elements[id], addEventListener() {} },
    window: { location: {} },
    setTimeout: (fn, delay) => { timers.set(++timerId, { fn, delay }); return timerId; },
    clearTimeout: id => timers.delete(id),
    fetch: (url, options) => {
      requests.push({ url, options });
      return new Promise((resolve, reject) => {
        pending = { resolve, reject };
        options.signal.addEventListener('abort', () => reject(new Error('Timed out')));
      });
    },
  });
  vm.runInContext(fs.readFileSync(path.join(__dirname, '../static/js/app.js'), 'utf8'), context);
  return {
    context, elements, requests, timers,
    reply: (data, extra = {}) => pending.resolve({
      ok: true, status: 200, json: async () => data, ...extra,
    }),
    disconnect: () => pending.reject(new Error('Connection lost')),
  };
}

async function main() {
  for (const isStatic of [true, false]) {
    const test = setup(isStatic);
    const saving = test.context.saveRtuNetwork();
    assert.equal(test.elements.rtuNetworkNotice.classes.has('d-none'), false);
    assert.match(test.elements.rtuNetworkMessage.textContent, /Applying IP Address/);
    assert.equal(test.elements.rtuNetworkReturn.disabled, true);
    test.context.dismissRtuNetworkNotice();
    assert.equal(test.elements.rtuNetworkNotice.classes.has('d-none'), false);
    await test.context.saveRtuNetwork();
    assert.equal(test.requests.length, 1, 'Prevent duplicate saves while applying');
    assert.equal(test.requests[0].url, '/api/rtu/network');
    assert.equal(test.requests[0].options.method, 'POST');
    const body = JSON.parse(test.requests[0].options.body);
    assert.equal(body.method, isStatic ? 'manual' : 'auto');
    assert.equal(body.ip_address, isStatic ? '192.168.10.50' : undefined);
    test.reply({ message: 'Network settings saved successfully' });
    await saving;
    assert.equal(test.elements.rtuNetworkMessage.textContent,
      'IP Address changed... Please revert to the new IP.');
    assert.match(test.elements.rtuNetworkDetail.textContent,
      isStatic ? /192\.168\.10\.50/ : /router\/DHCP lease list/);
    assert.equal(test.elements.rtuNetworkReturn.disabled, false);
    assert.equal(test.timers.size, 0, 'Successful notice must not auto-dismiss');
    test.context.dismissRtuNetworkNotice();
    assert.equal(test.elements.rtuNetworkNotice.classes.has('d-none'), true);
  }

  for (const timeout of [false, true]) {
    const test = setup(!timeout);
    const saving = test.context.saveRtuNetwork();
    if (timeout) {
      const timer = [...test.timers.values()][0];
      assert.equal(timer.delay, 15000);
      timer.fn();
    } else {
      test.disconnect();
    }
    await saving;
    assert.match(test.elements.rtuNetworkMessage.textContent, /Please revert to the new IP/);
    assert.match(test.elements.rtuNetworkDetail.textContent, /could not be confirmed/);
    assert.equal(test.elements.rtuNetworkNotice.classes.has('d-none'), false);
    assert.equal(test.elements.rtuNetworkReturn.disabled, false);
    assert.equal(test.timers.size, 0);
  }

  for (const status of [400, 403, 500]) {
    const test = setup();
    const saving = test.context.saveRtuNetwork();
    test.reply({ error: 'Settings rejected' }, { ok: false, status });
    await saving;
    assert.equal(test.elements.rtuNetworkNotice.classes.has('d-none'), true);
    assert.equal(test.elements.statusAlert.textContent, 'Settings rejected');
    assert.equal(test.elements.statusAlert.className, 'alert alert-danger');
  }

  for (const response of [{ status: 401 }, { redirected: true }]) {
    const test = setup();
    const saving = test.context.saveRtuNetwork();
    test.reply({}, response);
    await saving;
    assert.equal(test.context.window.location.href, '/login');
    assert.equal(test.elements.rtuNetworkNotice.classes.has('d-none'), true);
  }

  const readonly = setup(true, false);
  await readonly.context.saveRtuNetwork();
  assert.equal(readonly.requests.length, 0);
  const template = fs.readFileSync(path.join(__dirname, '../templates/rtu.html'), 'utf8');
  for (const id of ['rtuNetworkNotice', 'rtuNetworkMessage', 'rtuNetworkDetail', 'rtuNetworkReturn']) {
    assert.ok(template.includes(`id="${id}"`), `Missing notice element ${id}`);
  }
  console.log('RTU network change frontend checks passed');
}

main().catch(error => {
  console.error(error);
  process.exitCode = 1;
});
