/** Run with node tests/test_save_changes_ui.js; DOM, timers and requests are mocked. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const flush = async () => { for (let i = 0; i < 30; i++) await Promise.resolve(); };
const escape = text => String(text).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');

function setup(manage = true) {
  class Element {
    constructor(tag = 'div') {
      this.tag = tag;
      this.value = '';
      this.textContent = '';
      this.checked = false;
      this.disabled = false;
      this.style = {};
      this.dataset = { csrfToken: 'test-only' };
      this.children = [];
      this.fields = {};
      this.classList = { add() {}, remove() {} };
    }
    appendChild(child) {
      child.parent = this;
      this.children.push(child);
      if (child.selected) this.value = child.value;
    }
    remove() { this.parent.children = this.parent.children.filter(child => child !== this); }
    closest() { return this.parent; }
    querySelector(selector) { return this.fields[selector]; }
    set innerHTML(text) {
      this.html = text;
      this.children = [];
      if (this.tag === 'tr') {
        for (const cls of ['meter-sn', 'meter-cfg', 'meter-alt']) {
          const field = new Element();
          const match = text.match(new RegExp(`class="[^"]*${cls}"[^>]*value="([^"]*)"`));
          field.value = match ? match[1] : '';
          if (cls === 'meter-cfg') {
            const selected = text.match(/<option value="([^"]*)" selected>/);
            if (selected) field.value = selected[1];
          }
          this.fields['.' + cls] = field;
        }
        this.fields['td:first-child'] = new Element();
      }
      if (text.includes('watchdog-ip')) {
        const input = new Element('input');
        input.value = text.match(/value="([^"]*)"/)[1];
        this.fields['.watchdog-ip'] = input;
      }
    }
    get innerHTML() { return this.html || escape(this.textContent); }
  }
  const elements = {};
  const ids = [
    'rtuNetworkSaveBtn', 'rtuServerSaveBtn', 'rtuLocationSaveBtn', 'watchdogSaveBtn',
    'autoReadSaveBtn', 'meterBatchSaveBtn', 'configSaveBtn', 'configSaveAsBtn', 'passwordSaveBtn',
    'rtuDhcp', 'rtuStatic', 'rtuIp', 'rtuSubnet', 'rtuGateway', 'rtuDns',
    'rtuMac', 'rtuServerIp', 'rtuLocation', 'watchdogTargets',
    'autoReadEnable', 'autoReadInterval', 'autoReadLabel',
    'meterTableBody', 'meterBatchUploadFile', 'meterBatchUploadBtn', 'meterBatchAddBtn',
    'cfgFileSelect', 'cfgEditor', 'configDeleteBtn', 'oldPassword', 'newPassword',
    'confirmPassword', 'pwdAlert', 'statusAlert', 'rtuNetworkMessage',
    'rtuNetworkDetail', 'rtuNetworkNotice', 'rtuNetworkReturn',
  ];
  ids.forEach(id => { elements[id] = new Element(); });
  const events = {};
  const requests = [];
  const pending = [];
  const context = vm.createContext({
    CAN_MANAGE: manage, IS_ADMIN: manage, AbortController, TextDecoder,
    FormData: class { append() {} },
    window: { location: {} }, confirm: () => true, prompt: () => 'new.cfg',
    setTimeout() { return 1; }, clearTimeout() {},
    document: {
      getElementById: id => elements[id],
      addEventListener: (name, callback) => { events[name] = callback; },
      createElement: tag => new Element(tag),
      querySelectorAll: selector => {
        if (selector === '#meterTableBody tr') return elements.meterTableBody.children;
        if (selector === '.watchdog-ip') return elements.watchdogTargets.children.map(
          row => row.fields['.watchdog-ip']);
        if (selector === '.rtu-static-field') return ['rtuIp', 'rtuSubnet', 'rtuGateway', 'rtuDns']
          .map(id => elements[id]);
        return [];
      },
    },
    fetch: (url, options = {}) => new Promise((resolve, reject) => {
      const request = { url, options, resolve, reject };
      requests.push(request);
      pending.push(request);
    }),
  });
  for (const filename of ['app.js', 'meter_transfer.js']) {
    vm.runInContext(fs.readFileSync(path.join(__dirname, '../static/js', filename), 'utf8'), context);
  }
  context.initializeSaveChanges();
  const tracker = vm.runInContext('saveChanges', context);
  return {
    context, elements, events, requests, pending, tracker,
    edit(id, value) { elements[id].value = value; events.input(); },
    check(id, value) { elements[id].checked = value; events.change(); },
    disabled(id, expected = true) { assert.equal(elements[id].disabled, expected, id); },
    async reply(url, data = {}, options = {}) {
      const index = pending.findIndex(request => request.url === url);
      assert.notEqual(index, -1, `No pending request to ${url}`);
      pending.splice(index, 1)[0].resolve({
        ok: true, status: 200, json: async () => data, ...options,
      });
      await flush();
    },
  };
}

const rtu = {
  method: 'auto', server_ip: '10.0.0.1', location: 'Office', mac_address: 'AA:BB',
  ip_address: '', subnet: '24', gateway: '', dns: '',
};
const meter = { meter_sn: '000123', config_file: 'meter.cfg', alt_address: '007' };

async function main() {
  const test = setup();
  const { context: app, tracker, elements } = test;
  const saveIds = ['rtuNetworkSaveBtn', 'rtuServerSaveBtn', 'rtuLocationSaveBtn',
    'watchdogSaveBtn', 'autoReadSaveBtn', 'meterBatchSaveBtn', 'configSaveBtn',
    'configSaveAsBtn', 'passwordSaveBtn'];
  saveIds.forEach(id => test.disabled(id));
  let loading = app.loadRtuConfig();
  await test.reply('/api/rtu/config', rtu);
  await loading;
  saveIds.forEach(id => test.disabled(id));

  test.edit('rtuServerIp', '10.0.0.2');
  test.disabled('rtuServerSaveBtn', false);
  test.disabled('rtuLocationSaveBtn');
  test.disabled('autoReadSaveBtn');
  test.edit('rtuServerIp', ' 10.0.0.1 ');
  test.disabled('rtuServerSaveBtn');
  const count = test.requests.length;
  await app.saveRtuServer();
  assert.equal(test.requests.length, count, 'Clean forms must not submit');
  test.edit('rtuLocation', 'Branch');
  test.disabled('rtuLocationSaveBtn', false);
  await app.loadRtuConfig();
  assert.equal(test.requests.length, count, 'Tab reload must not discard unsaved changes');
  test.edit('rtuLocation', 'Office');

  test.edit('rtuServerIp', '10.0.0.2');
  let saving = app.saveRtuServer();
  test.disabled('rtuServerSaveBtn');
  await app.saveRtuServer();
  assert.equal(test.pending.length, 1, 'Prevent duplicate saves');
  test.edit('rtuServerIp', '10.0.0.3');
  await test.reply('/api/rtu/server', { message: 'Saved' });
  await saving;
  test.disabled('rtuServerSaveBtn', false);
  saving = app.saveRtuServer();
  await test.reply('/api/rtu/server', { error: 'Rejected' }, { ok: false });
  await saving;
  test.disabled('rtuServerSaveBtn', false);
  saving = app.saveRtuServer();
  await test.reply('/api/rtu/server', { message: 'Saved' });
  await saving;
  test.disabled('rtuServerSaveBtn');
  test.edit('rtuLocation', 'Branch');
  saving = app.saveRtuLocation();
  await test.reply('/api/rtu/location', { message: 'Saved' });
  await saving;
  test.disabled('rtuLocationSaveBtn');

  test.edit('rtuLocation', 'Disconnected save');
  saving = app.saveRtuLocation();
  test.pending.shift().reject(new Error('Disconnected'));
  await saving;
  test.disabled('rtuLocationSaveBtn', false);
  test.edit('rtuLocation', 'Branch');
  test.disabled('rtuLocationSaveBtn');

  test.edit('rtuIp', '192.168.1.50');
  test.disabled('rtuNetworkSaveBtn', true); // DHCP ignores inactive static fields.
  test.check('rtuStatic', true);
  test.disabled('rtuNetworkSaveBtn', false);
  test.check('rtuStatic', false);
  test.disabled('rtuNetworkSaveBtn');
  test.check('rtuStatic', true);
  saving = app.saveRtuNetwork();
  test.disabled('rtuNetworkSaveBtn');
  await test.reply('/api/rtu/network', { message: 'Saved' });
  await saving;
  test.disabled('rtuNetworkSaveBtn');

  loading = app.loadWatchdogTargets();
  await test.reply('/api/lte/watchdog', { targets: ['8.8.8.8'] });
  await loading;
  test.disabled('watchdogSaveBtn');
  app.addWatchdogTarget();
  test.disabled('watchdogSaveBtn'); // Empty targets are not submitted.
  elements.watchdogTargets.children[1].fields['.watchdog-ip'].value = '1.1.1.1';
  test.events.input();
  test.disabled('watchdogSaveBtn', false);
  elements.watchdogTargets.children[1].remove();
  tracker.refresh('watchdog');
  test.disabled('watchdogSaveBtn');
  elements.watchdogTargets.children[0].fields['.watchdog-ip'].value = '9.9.9.9';
  test.events.change();
  saving = app.saveWatchdogTargets();
  await test.reply('/api/lte/watchdog', { message: 'Saved' });
  await saving;
  test.disabled('watchdogSaveBtn');

  loading = app.loadAutoRead();
  await test.reply('/api/amr/autoread', { enabled: true, interval: '30' });
  await loading;
  test.edit('autoReadInterval', '60');
  test.disabled('autoReadSaveBtn', false);
  test.edit('autoReadInterval', '30');
  test.disabled('autoReadSaveBtn');
  test.check('autoReadEnable', false);
  saving = app.saveAutoRead();
  await test.reply('/api/amr/autoread', { message: 'Saved' });
  await saving;
  test.disabled('autoReadSaveBtn');

  loading = app.loadMeters();
  await test.reply('/api/amr/meter-configs', { configs: ['meter.cfg'] });
  await test.reply('/api/amr/meters', { meters: [meter] });
  await loading;
  test.disabled('meterBatchSaveBtn');
  app.addMeterRow();
  test.disabled('meterBatchSaveBtn', false);
  app.deleteMeterRow({ closest: () => elements.meterTableBody.children[1] });
  test.disabled('meterBatchSaveBtn');
  const row = elements.meterTableBody.children[0];
  app.editMeterRow({ closest: () => row });
  test.disabled('meterBatchSaveBtn');
  row.fields['.meter-alt'].value = '008';
  test.events.input();
  saving = app.saveMeters();
  test.disabled('meterBatchUploadBtn');
  row.fields['.meter-alt'].value = '009';
  await test.reply('/api/amr/meters', { message: 'Saved' });
  await saving;
  test.disabled('meterBatchSaveBtn', false);
  const bytes = new TextEncoder().encode('000123,meter.cfg,007\n');
  elements.meterBatchUploadFile.files = [{ size: bytes.length, arrayBuffer: async () => bytes.buffer }];
  saving = app.uploadMeterBatch();
  await flush();
  test.disabled('meterBatchSaveBtn');
  await test.reply('/api/amr/meters/upload', { meters: [meter], message: 'Uploaded' });
  await saving;
  test.disabled('meterBatchSaveBtn');
  assert.equal(elements.meterTableBody.children[0].fields['.meter-alt'].value, '007');
  // Upload cancellation preserves dirty state, not a hard-coded enabled Save.
  elements.meterTableBody.children[0].fields['.meter-alt'].value = '010';
  test.events.input();
  app.confirm = () => false;
  await app.uploadMeterBatch();
  test.disabled('meterBatchSaveBtn', false);
  app.confirm = () => true;

  test.edit('cfgEditor', 'new draft');
  test.disabled('configSaveBtn');
  test.disabled('configSaveAsBtn', false);
  saving = app.saveConfigFileAs();
  test.disabled('configSaveAsBtn');
  await test.reply('/api/amr/configs', { filename: 'new.cfg', message: 'Saved' });
  await test.reply('/api/amr/meter-configs', { configs: ['meter.cfg', 'new.cfg'] });
  await saving;
  test.disabled('configSaveBtn');
  test.disabled('configSaveAsBtn');
  test.edit('cfgEditor', 'modified');
  saving = app.saveConfigFile();
  test.disabled('configDeleteBtn');
  test.disabled('cfgFileSelect');
  test.edit('cfgEditor', 'edited during save');
  await test.reply('/api/amr/configs/new.cfg', { message: 'Saved' });
  await saving;
  test.disabled('configSaveBtn', false);
  test.edit('cfgEditor', 'modified');
  test.disabled('configSaveBtn');
  elements.cfgFileSelect.value = 'a.cfg';
  const first = app.loadConfigFile();
  test.disabled('configSaveBtn');
  elements.cfgFileSelect.value = 'b.cfg';
  const second = app.loadConfigFile();
  await test.reply('/api/amr/configs/b.cfg', { content: 'B' });
  await second;
  await test.reply('/api/amr/configs/a.cfg', { content: 'A' });
  await first;
  assert.equal(elements.cfgEditor.value, 'B', 'Ignore an older file-load response');
  test.disabled('configSaveBtn');
  elements.cfgFileSelect.value = 'failed.cfg';
  loading = app.loadConfigFile();
  await test.reply('/api/amr/configs/failed.cfg', { error: 'Unreadable' }, { ok: false });
  await loading;
  test.disabled('configSaveBtn');
  test.disabled('configSaveAsBtn');
  elements.cfgFileSelect.value = 'delete.cfg';
  loading = app.loadConfigFile();
  await test.reply('/api/amr/configs/delete.cfg', { content: 'saved' });
  await loading;
  saving = app.deleteConfigFile();
  test.disabled('cfgFileSelect');
  await test.reply('/api/amr/configs/delete.cfg', { message: 'Deleted' });
  await test.reply('/api/amr/configs', { configs: ['meter.cfg'] });
  await test.reply('/api/amr/meter-configs', { configs: ['meter.cfg'] });
  await saving;
  assert.equal(elements.cfgFileSelect.value, '');
  assert.equal(elements.cfgEditor.value, '');
  test.disabled('configSaveBtn');
  test.disabled('configSaveAsBtn');
  elements.cfgFileSelect.value = '';
  await app.loadConfigFile();
  assert.equal(elements.cfgEditor.readOnly, false);
  test.disabled('configSaveAsBtn');

  test.edit('oldPassword', 'old-test-password');
  test.edit('newPassword', 'new-test-password');
  test.disabled('passwordSaveBtn');
  test.edit('confirmPassword', 'new-test-password');
  test.disabled('passwordSaveBtn', false);
  assert.equal(tracker.sections.get('password').baseline, '{"changed":false}');
  saving = app.changePassword();
  test.disabled('passwordSaveBtn');
  await test.reply('/api/auth/change-password', { message: 'Changed' });
  await saving;
  test.disabled('passwordSaveBtn');
  assert.equal(elements.newPassword.value, '');
  assert.equal(elements.oldPassword.value, '');

  const stale = setup();
  const older = stale.context.loadRtuConfig();
  const newer = stale.context.loadRtuConfig();
  const oldRequest = stale.pending.shift();
  await stale.reply('/api/rtu/config', { ...rtu, location: 'Newest' });
  await newer;
  stale.edit('rtuLocation', 'Unsaved newest');
  oldRequest.resolve({ status: 200, ok: true, json: async () => rtu });
  await older;
  assert.equal(stale.elements.rtuLocation.value, 'Unsaved newest');
  stale.disabled('rtuLocationSaveBtn', false);

  const failedLoad = setup();
  loading = failedLoad.context.loadRtuConfig();
  await failedLoad.reply('/api/rtu/config', { error: 'Failed' }, { ok: false });
  await loading;
  failedLoad.disabled('rtuServerSaveBtn');
  const readonly = setup(false);
  readonly.tracker.sections.forEach((state, name) => {
    if (name !== 'password') readonly.tracker.saved(name);
  });
  readonly.edit('rtuServerIp', '10.0.0.2');
  readonly.edit('cfgEditor', 'draft');
  saveIds.filter(id => id !== 'passwordSaveBtn').forEach(id => readonly.disabled(id));
  await readonly.context.saveRtuServer();
  assert.equal(readonly.requests.length, 0);
  readonly.edit('oldPassword', 'old');
  readonly.edit('newPassword', 'new');
  readonly.edit('confirmPassword', 'new');
  readonly.disabled('passwordSaveBtn', false);

  for (const filename of ['rtu.html', 'amr.html', 'watchdog.html', 'base.html']) {
    const text = fs.readFileSync(path.join(__dirname, '../templates', filename), 'utf8');
    for (const tag of text.matchAll(/<button\b[^>]*onclick="(?:save[A-Za-z]+|changePassword)\(\)"[^>]*>/g)) {
      assert.match(tag[0], /\bid="/, filename);
      assert.match(tag[0], /\bdisabled\b/, filename);
    }
  }
  assert.ok(fs.readFileSync(path.join(__dirname, '../static/js/app.js'), 'utf8')
    .includes("initializeSaveChanges();"), 'Initialize before initial settings loads');
  console.log('Section-specific Save state integration checks passed');
}

main().catch(error => { console.error(error); process.exitCode = 1; });
