/** Run with node tests/test_meter_transfer_ui.js; no real files or network requests. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const flush = async () => { for (let i = 0; i < 12; i++) await Promise.resolve(); };
const valid = '000123,meter.cfg,007\n';
const meters = [{ meter_sn: '000123', config_file: 'meter.cfg', alt_address: '007' }];

function setup(manage = true, text = valid) {
  const elements = {};
  const alerts = [];
  const confirmations = [];
  const requests = [];
  const pending = [];
  const rendered = [];
  const links = [];
  const timers = [];
  const revoked = [];
  for (const id of ['meterBatchUploadFile', 'meterBatchUploadBtn', 'meterBatchDownloadBtn',
    'meterBatchAddBtn', 'meterBatchSaveBtn']) {
    elements[id] = { disabled: false, value: 'chosen', clicks: 0,
      dataset: { csrfToken: 'test-csrf' }, click() { this.clicks++; } };
  }
  const bytes = new TextEncoder().encode(text);
  elements.meterBatchUploadFile.files = [{
    name: 'any-name.txt', size: bytes.length, arrayBuffer: async () => bytes.buffer,
  }];
  class FormData {
    constructor() { this.entries = []; }
    append(...args) { this.entries.push(args); }
  }
  const context = vm.createContext({
    CAN_MANAGE: manage, TextDecoder, FormData,
    window: { location: {} },
    document: {
      getElementById: id => elements[id],
      body: { appendChild() {} },
      createElement: () => {
        const link = { clicked: false, removed: false,
          click() { this.clicked = true; }, remove() { this.removed = true; } };
        links.push(link);
        return link;
      },
    },
    URL: { createObjectURL: () => 'blob:test', revokeObjectURL: url => revoked.push(url) },
    setTimeout: fn => timers.push(fn),
    confirm: text => { confirmations.push(text); return true; },
    showAlert: (...args) => alerts.push(args),
    renderMeterTable: rows => rendered.push(rows),
    fetch: (url, options) => {
      requests.push({ url, options });
      return new Promise((resolve, reject) => pending.push({ resolve, reject }));
    },
  });
  vm.runInContext(fs.readFileSync(path.join(__dirname, '../static/js/meter_transfer.js'), 'utf8'), context);
  return {
    context, elements, alerts, confirmations, requests, rendered, links, timers, revoked,
    complete: async (data = {}, response = {}) => {
      pending.shift().resolve({
        ok: true, status: 200, json: async () => data, blob: async () => 'csv-blob', ...response,
      });
      await flush();
    },
    fail: async () => { pending.shift().reject(new Error('Disconnected')); await flush(); },
  };
}

async function main() {
  const parser = setup().context.validateMeterBatchCsv;
  for (const content of [valid, '\ufeff\n0001,"meter, model.cfg",0002\r\n \r\n',
    '1,"meter ""name"".cfg",999999999999999999', ' 1 , any name.cfg , 2 \n']) {
    assert.equal(parser(content), 1);
  }
  for (const content of ['', '\n \r\n', '1,meter.cfg', '1,meter.cfg,2,extra',
    'serial,config_file,modbus_id', '-1,meter.cfg,2', '1.5,meter.cfg,2',
    '１,meter.cfg,2', '1,.cfg,2', '1,../meter.cfg,2', '1,C:\\temp\\meter.cfg,2',
    '1,meter.txt,2', '1,,2', '1,meter.cfg,', '1,meter.cfg,-1',
    '1,meter.cfg,2.5', '1,"unterminated.cfg,2', '1,"meter.cfg"x,2',
    '1,"me\nter.cfg",2']) {
    assert.throws(() => parser(content), /Line |no meter entries/);
  }
  const many = Array.from({ length: 40 }, (_, i) => `${i},meter.cfg,${i}\n`).join('');
  assert.equal(parser(many), 40);
  assert.throws(() => parser(many + 'bad,meter.cfg,1\n'), /Line 41/);

  const upload = setup();
  upload.context.chooseMeterBatchUpload();
  assert.equal(upload.elements.meterBatchUploadFile.clicks, 1);
  const sending = upload.context.uploadMeterBatch();
  await upload.context.uploadMeterBatch();
  await flush();
  assert.equal(upload.requests.length, 1);
  assert.equal(upload.elements.meterBatchUploadBtn.disabled, true);
  assert.match(upload.confirmations[0], /Replace the saved/);
  assert.equal(upload.requests[0].url, '/api/amr/meters/upload');
  assert.equal(upload.requests[0].options.method, 'POST');
  assert.equal(upload.requests[0].options.headers['X-CSRF-Token'], 'test-csrf');
  assert.equal(upload.requests[0].options.headers['Content-Type'], undefined,
    'Browser must set the multipart boundary');
  assert.equal(upload.requests[0].options.body.entries[0][0], 'file');
  await upload.complete({ meters, message: 'Uploaded', truncated: false });
  await sending;
  assert.deepEqual(upload.rendered[0], meters);
  assert.equal(upload.alerts.at(-1)[1], 'success');
  assert.equal(upload.elements.meterBatchUploadBtn.disabled, false);
  assert.equal(upload.elements.meterBatchUploadFile.value, '');

  const truncated = setup(true, many);
  const truncating = truncated.context.uploadMeterBatch();
  await flush();
  assert.match(truncated.confirmations[0], /first 32 of 40/);
  await truncated.complete({ meters, message: 'First 32 kept', truncated: true });
  await truncating;
  assert.equal(truncated.alerts.at(-1)[1], 'warning');

  for (const test of [setup(false), setup(true, 'bad,meter.cfg,1')]) {
    await test.context.uploadMeterBatch();
    assert.equal(test.requests.length, 0);
  }
  const cancel = setup();
  cancel.context.confirm = () => false;
  await cancel.context.uploadMeterBatch();
  assert.equal(cancel.requests.length, 0);
  const oversize = setup();
  oversize.elements.meterBatchUploadFile.files[0].size = 1024 * 1024 + 1;
  await oversize.context.uploadMeterBatch();
  assert.equal(oversize.requests.length, 0);
  assert.match(oversize.alerts.at(-1)[0], /1 MiB/);
  const invalidEncoding = setup();
  invalidEncoding.elements.meterBatchUploadFile.files[0].arrayBuffer = async () => new Uint8Array([255]).buffer;
  await invalidEncoding.context.uploadMeterBatch();
  assert.equal(invalidEncoding.requests.length, 0);
  assert.match(invalidEncoding.alerts.at(-1)[0], /UTF-8/);

  const rejected = setup();
  const rejection = rejected.context.uploadMeterBatch();
  await flush();
  await rejected.complete({ error: 'Line 2: invalid entry' }, { ok: false, status: 400 });
  await rejection;
  assert.equal(rejected.rendered.length, 0);
  assert.equal(rejected.alerts.at(-1)[0], 'Line 2: invalid entry');
  const unknown = setup();
  const interrupted = unknown.context.uploadMeterBatch();
  await flush();
  await unknown.fail();
  await interrupted;
  assert.match(unknown.alerts.at(-1)[0], /result is unknown/);

  const download = setup(false);
  const downloading = download.context.downloadMeterBatch();
  await download.context.downloadMeterBatch();
  assert.equal(download.requests.length, 1, 'Downloads are available read-only, without duplicates');
  assert.equal(download.requests[0].url, '/api/amr/meters/download');
  assert.equal(download.requests[0].options.cache, 'no-store');
  await download.complete();
  await downloading;
  assert.equal(download.links[0].download, 'meter_batch.csv');
  assert.equal(download.links[0].clicked, true);
  assert.equal(download.links[0].removed, true);
  download.timers[0]();
  assert.deepEqual(download.revoked, ['blob:test']);

  const picker = setup();
  const writes = [];
  let closed = false;
  picker.context.window.showSaveFilePicker = async options => {
    assert.equal(options.suggestedName, 'meter_batch.csv');
    return { createWritable: async () => ({
      write: async blob => writes.push(blob), close: async () => { closed = true; },
    }) };
  };
  const saving = picker.context.downloadMeterBatch();
  await flush();
  await picker.complete();
  await saving;
  assert.deepEqual(writes, ['csv-blob']);
  assert.equal(closed, true);
  assert.equal(picker.links.length, 0);
  const pickerCancel = setup();
  pickerCancel.context.window.showSaveFilePicker = async () => {
    const error = new Error('Cancelled'); error.name = 'AbortError'; throw error;
  };
  await pickerCancel.context.downloadMeterBatch();
  assert.equal(pickerCancel.requests.length, 0);
  assert.equal(pickerCancel.alerts.length, 0);

  const missing = setup();
  const unavailable = missing.context.downloadMeterBatch();
  await missing.complete({ error: 'meter_batch.csv was not found' }, { ok: false, status: 404 });
  await unavailable;
  assert.equal(missing.links.length, 0);
  assert.match(missing.alerts.at(-1)[0], /not found/);
  for (const action of ['uploadMeterBatch', 'downloadMeterBatch']) {
    const expired = setup();
    const request = expired.context[action]();
    await flush();
    await expired.complete({}, { status: 401 });
    await request;
    assert.equal(expired.context.window.location.href, '/login');
  }
  console.log('Meter CSV upload/download frontend checks passed');
}

main().catch(error => {
  console.error(error);
  process.exitCode = 1;
});
